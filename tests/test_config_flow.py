from datetime import UTC, datetime

from homeassistant import config_entries
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_nutrition.const import (
    CONF_BMR_ENTITY,
    CONF_BURN_ENTITY,
    CONF_BURN_STATISTIC,
    CONF_WEIGHT_ENTITY,
    DEFAULT_BURN_STATISTIC,
    DOMAIN,
)


def _suggested(schema, key):
    for marker in schema.schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value")
    raise KeyError(key)


async def _samsung_burn_statistic(hass):
    meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name="burn", source="ha_samsung_health",
                             statistic_id=DEFAULT_BURN_STATISTIC, unit_class=None, unit_of_measurement="kcal")
    async_add_external_statistics(hass, meta, [StatisticData(start=datetime(2026, 11, 8, 12, tzinfo=UTC),
                                                             state=2400, sum=2400)])
    await async_wait_recording_done(hass)


async def test_prefills_samsung_entities(hass):
    await _samsung_burn_statistic(hass)  # a pre-0.5.0 Samsung Health statistic: not suggested any more
    reg = er.async_get(hass)
    ids = {
        key: reg.async_get_or_create("sensor", "ha_samsung_health", f"abc_{key}",
                                     suggested_object_id=f"x_samsung_health_{key}").entity_id
        for key in ("total_calories", "bmr", "weight", "body_fat_mass")
    }
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert _suggested(schema, CONF_BURN_ENTITY) == ids["total_calories"]
    assert _suggested(schema, CONF_BMR_ENTITY) == ids["bmr"]
    assert _suggested(schema, CONF_WEIGHT_ENTITY) == ids["weight"]
    # the burn sensor's own statistics carry its history now
    assert _suggested(schema, CONF_BURN_STATISTIC) is None

    user_input = {CONF_BURN_ENTITY: ids["total_calories"], CONF_BURN_STATISTIC: DEFAULT_BURN_STATISTIC}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nutrition"
    assert result["options"] == user_input
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_no_samsung_entities_no_prefill(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert _suggested(result["data_schema"], CONF_BURN_ENTITY) is None
    # no Samsung Health statistic in the recorder: don't suggest an id that doesn't exist
    assert _suggested(result["data_schema"], CONF_BURN_STATISTIC) is None


async def test_single_instance(hass):
    MockConfigEntry(domain=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_change_reloads(hass, entry):
    old_data = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BURN_STATISTIC: "other:burn"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options == {CONF_BURN_STATISTIC: "other:burn"}
    assert entry.runtime_data is not old_data

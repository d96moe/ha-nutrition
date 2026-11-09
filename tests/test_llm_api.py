from datetime import UTC, datetime

import probatio
import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    async_import_statistics,
)
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_nutrition.const import CONF_BURN_ENTITY, CONF_BURN_STATISTIC, DOMAIN

CONTEXT = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)

TOOLS = {"log_food", "update_entry", "delete_entry", "set_day_note", "get_nutrition", "get_goals", "set_goals"}
BREAKFAST = {"category": "breakfast", "description": "Gröt", "kcal": 400, "protein_g": 20, "time": "07:30"}
LUNCH = {"category": "lunch", "description": "Gryta", "kcal": 650, "protein_g": 45}
WINE = {"category": "drink", "description": "Rödvin", "kcal": 120, "alcohol_units": 1}


@pytest.fixture
async def instance(hass, entry):
    return await llm.async_get_api(hass, DOMAIN, CONTEXT)


async def call(instance, name, **args):
    """Call a tool the way the MCP server does, with schema-validated arguments.

    Not via APIInstance.async_call_tool: that imports the conversation integration,
    which needs packages the test environment doesn't install.
    """
    [tool] = [t for t in instance.tools if t.name == name]
    tool_args = tool.parameters(args)
    result = await tool.async_call(instance.api.hass, llm.ToolInput(tool_name=name, tool_args=tool_args), CONTEXT)
    return result.data if hasattr(result, "data") else result


async def test_registered_and_schemas_convert(hass, entry, instance):
    assert {t.name for t in instance.tools} == TOOLS
    for tool in instance.tools:  # the MCP server converts every schema like this
        assert probatio.to_openapi(tool.parameters, openapi_version="3.1.0")["type"] == "object", tool.name
    assert "snack" in instance.api_prompt
    assert "0 clears" in instance.api_prompt and "repeated" in instance.api_prompt
    await hass.config_entries.async_unload(entry.entry_id)
    assert DOMAIN not in {a.id for a in llm.async_get_apis(hass)}


async def test_log_food_multi_entry(instance):
    data = await call(instance, "log_food", entries=[BREAKFAST, LUNCH, WINE], request_id="m1")
    assert len({e["id"] for e in data["entries"]}) == 3
    assert data["entries"][0]["source"] == "llm"
    assert data["totals"]["kcal"] == 1170
    assert data["totals"]["kcal_by_category"] == {"breakfast": 400, "lunch": 650, "drink": 120}


async def test_errors_reported_not_raised(instance):
    data = await call(instance, "log_food", entries=[{**LUNCH, "kcal": -5}])
    assert "kcal" in data["error"]
    data = await call(instance, "delete_entry", entry_id="nope")
    assert "no entry" in data["error"]
    data = await call(instance, "log_food", date="2026-11-10", entries=[LUNCH])
    assert "future" in data["error"]
    with pytest.raises(probatio.Invalid):  # the enum is enforced before the tool runs
        await call(instance, "log_food", entries=[{**LUNCH, "category": "fika"}])


async def test_update_and_delete(instance):
    [logged] = (await call(instance, "log_food", entries=[LUNCH]))["entries"]
    data = await call(instance, "update_entry", entry_id=logged["id"], kcal=700, category="dinner")
    assert data["entry"]["kcal"] == 700 and data["entry"]["category"] == "dinner"
    data = await call(instance, "delete_entry", entry_id=logged["id"])
    assert data["totals"]["entries"] == 0


async def _burn(hass, values):
    meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name="burn", source="ha_samsung_health",
                             statistic_id="ha_samsung_health:total_calories", unit_class=None,
                             unit_of_measurement="kcal")
    rows, running = [], 0.0
    for day, kcal in values:
        running += kcal
        rows.append(StatisticData(start=datetime(2026, 11, day, 12, tzinfo=UTC), state=kcal, sum=running))
    async_add_external_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)


async def test_get_nutrition_with_burn(hass, entry, instance):
    hass.config_entries.async_update_entry(entry, options={CONF_BURN_STATISTIC: "ha_samsung_health:total_calories"})
    await _burn(hass, [(7, 2500), (8, 2400)])
    await call(instance, "log_food", date="2026-11-08", entries=[BREAKFAST, WINE])
    await call(instance, "log_food", entries=[LUNCH])
    await call(instance, "set_day_note", date="2026-11-08", note="Middag hos Kim")

    data = await call(instance, "get_nutrition", start="2026-11-06", end="2026-11-09", include_entries=True)
    rows = {r["period_start"]: r for r in data["rows"]}
    assert data["logged_days"] == ["2026-11-08", "2026-11-09"]
    assert data["unlogged_days"] == ["2026-11-06", "2026-11-07"]
    assert rows["2026-11-08"]["kcal"] == 520
    assert rows["2026-11-08"]["burn_kcal"] == 2400
    assert rows["2026-11-08"]["net_kcal"] == 520 - 2400
    assert rows["2026-11-08"]["notes"] == {"2026-11-08": "Middag hos Kim"}
    assert [e["description"] for e in rows["2026-11-08"]["entries"]] == ["Gröt", "Rödvin"]
    assert rows["2026-11-07"]["days_logged"] == 0 and rows["2026-11-07"]["burn_kcal"] == 2500
    assert rows["2026-11-07"]["net_kcal"] is None
    assert rows["2026-11-09"]["burn_kcal"] is None

    week = await call(instance, "get_nutrition", start="2026-11-02", end="2026-11-09", period="week")
    by_start = {r["period_start"]: r for r in week["rows"]}
    assert by_start["2026-11-02"]["kcal"] == 520 and by_start["2026-11-02"]["days_logged"] == 1
    assert by_start["2026-11-09"]["kcal"] == 650
    assert "entries" not in by_start["2026-11-02"]


async def test_burn_from_the_burn_sensors_own_statistics(hass, entry, instance):
    """Any integration's daily-total sensor (state_class total_increasing) works without a statistic id."""
    hass.config_entries.async_update_entry(entry, options={CONF_BURN_ENTITY: "sensor.other_total_calories"})
    hass.states.async_set("sensor.other_total_calories", "900", {"state_class": "total_increasing"})
    meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name=None, source="recorder",
                             statistic_id="sensor.other_total_calories", unit_class=None,
                             unit_of_measurement="kcal")
    async_import_statistics(hass, meta, [
        StatisticData(start=datetime(2026, 11, 8, h, tzinfo=UTC), state=s, sum=s) for h, s in ((8, 1000), (20, 2600))
    ])
    await async_wait_recording_done(hass)
    await call(instance, "log_food", date="2026-11-08", entries=[LUNCH])
    data = await call(instance, "get_nutrition", start="2026-11-08", end="2026-11-08")
    assert data["burn_source"] == "sensor.other_total_calories"
    assert data["rows"][0]["burn_kcal"] == 2600
    assert data["rows"][0]["net_kcal"] == 650 - 2600


async def test_no_burn_source(hass, entry, instance):
    hass.states.async_set("sensor.weird", "900")  # no state_class: no statistics to use
    hass.config_entries.async_update_entry(entry, options={CONF_BURN_ENTITY: "sensor.weird"})
    data = await call(instance, "get_nutrition")
    assert data["burn_source"] is None
    assert all(r["burn_kcal"] is None for r in data["rows"])


async def test_get_nutrition_defaults_and_limits(instance):
    data = await call(instance, "get_nutrition")
    assert data["start"] == "2026-11-03" and data["end"] == "2026-11-09"
    data = await call(instance, "get_nutrition", start="2025-01-01", end="2026-11-09")
    assert "400" in data["error"]


async def test_set_goals_and_get_goals(hass, instance):
    data = await call(instance, "set_goals", deficit_target=600, target_weight=75)
    assert data["deficit_target"] == {"value": 600, "unit": "kcal/d"}
    assert data["protein_floor"]["value"] is None
    assert float(hass.states.get("number.nutrition_deficit_target").state) == 600
    assert (await call(instance, "get_goals"))["target_weight"]["value"] == 75
    cleared = await call(instance, "set_goals", target_weight=0)
    assert cleared["target_weight"]["value"] is None
    assert "max" in (await call(instance, "set_goals", deficit_target=5000))["error"]

"""Plan goals and the plan start date (v0.3.0)."""
from datetime import date

from homeassistant.core import State
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.ha_nutrition.const import DOMAIN

from .test_llm_api import CONTEXT, call

PLAN_NUMBERS = {
    "start_weight": "number.nutrition_start_weight",
    "stage_1_weight": "number.nutrition_stage_1_weight",
    "stage_2_weight": "number.nutrition_stage_2_weight",
    "deficit_min": "number.nutrition_deficit_min",
    "deficit_max": "number.nutrition_deficit_max",
    "deficit_too_small": "number.nutrition_deficit_too_small",
    "deficit_too_large": "number.nutrition_deficit_too_large",
    "sat_fat_max": "number.nutrition_saturated_fat_max",
}
START = "date.nutrition_plan_start_date"


async def test_plan_goals_exist_and_default_zero(hass, entry):
    for entity_id in PLAN_NUMBERS.values():
        assert float(hass.states.get(entity_id).state) == 0, entity_id
    assert hass.states.get(START).state == "unknown"


async def test_set_goals_plan_values(hass, entry):
    instance = await llm.async_get_api(hass, DOMAIN, CONTEXT)
    data = await call(instance, "set_goals", deficit_min=500, deficit_max=600, sat_fat_max=20,
                      plan_start_date="2026-09-30")
    assert data["deficit_min"]["value"] == 500 and data["sat_fat_max"]["value"] == 20
    assert data["plan_start_date"] == "2026-09-30"
    assert hass.states.get(START).state == "2026-09-30"
    assert entry.runtime_data.plan_start == date(2026, 9, 30)


async def test_clearing_start_date(hass, entry):
    instance = await llm.async_get_api(hass, DOMAIN, CONTEXT)
    await call(instance, "set_goals", plan_start_date="2026-09-30")
    data = await call(instance, "set_goals", plan_start_date="")
    assert data["plan_start_date"] is None
    assert entry.runtime_data.plan_start is None


async def test_bad_start_date_is_an_error(hass, entry):
    instance = await llm.async_get_api(hass, DOMAIN, CONTEXT)
    data = await call(instance, "set_goals", plan_start_date="30 sep")
    assert "plan_start_date" in data["error"]


async def test_start_date_restored(hass, freezer):
    mock_restore_cache(hass, [State(START, "2026-09-30")])
    config_entry = MockConfigEntry(domain=DOMAIN, title="Nutrition")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(START).state == "2026-09-30"
    assert config_entry.runtime_data.plan_start == date(2026, 9, 30)

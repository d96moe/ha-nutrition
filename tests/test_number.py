from homeassistant.core import State
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache_with_extra_data

from custom_components.ha_nutrition.const import DOMAIN, SIGNAL_UPDATED
from custom_components.ha_nutrition.number import GOALS, goal, goal_entity_ids

ENTITY_IDS = {
    "deficit_target": "number.nutrition_deficit_target",
    "protein_floor": "number.nutrition_protein_floor",
    "protein_ceiling": "number.nutrition_protein_ceiling",
    "target_weight": "number.nutrition_target_weight",
    "alcohol_limit_week": "number.nutrition_alcohol_limit_per_week",
}


async def test_defaults_zero_means_not_set(hass, entry):
    assert set(GOALS) == set(ENTITY_IDS)
    # these ids are what the recorder include glob number.*nutrition_* matches
    assert goal_entity_ids(hass, entry) == ENTITY_IDS
    for key, entity_id in ENTITY_IDS.items():
        assert float(hass.states.get(entity_id).state) == 0
        assert goal(entry, key) is None
    assert hass.states.get(ENTITY_IDS["deficit_target"]).attributes["unit_of_measurement"] == "kcal/d"


async def test_set_value_updates_goals_and_signals(hass, entry):
    signals = []
    async_dispatcher_connect(hass, SIGNAL_UPDATED, lambda: signals.append(1))
    await hass.services.async_call("number", "set_value", {"entity_id": ENTITY_IDS["deficit_target"], "value": 600},
                                   blocking=True)
    assert goal(entry, "deficit_target") == 600
    assert float(hass.states.get(ENTITY_IDS["deficit_target"]).state) == 600
    assert signals


async def test_restore(hass, freezer):
    mock_restore_cache_with_extra_data(hass, [(
        State(ENTITY_IDS["target_weight"], "75.0"),
        {"native_max_value": 300, "native_min_value": 0, "native_step": 0.1,
         "native_unit_of_measurement": "kg", "native_value": 75.0},
    )])
    config_entry = MockConfigEntry(domain=DOMAIN, title="Nutrition")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(hass.states.get(ENTITY_IDS["target_weight"]).state) == 75.0
    assert goal(config_entry, "target_weight") == 75.0

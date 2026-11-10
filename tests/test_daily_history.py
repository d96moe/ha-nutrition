"""History on the today sensors (v0.2.0): logged days end up in the sensors' own statistics."""
from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.ha_nutrition import repairs
from custom_components.ha_nutrition.const import DOMAIN

from .conftest import NOW

INTAKE, PROTEIN = "sensor.nutrition_intake_today", "sensor.nutrition_protein_today"


async def _changes(hass, entity_id) -> dict[date, float]:
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2000, 1, 1, tzinfo=UTC), None, {entity_id}, "day", None, {"change"}
    )
    return {dt_util.as_local(dt_util.utc_from_timestamp(r["start"])).date(): round(r["change"], 2)
            for r in result.get(entity_id, []) if r["change"]}


async def _compile_once(hass, freezer):
    """The recorder compiles the new sensors for the first time."""
    freezer.move_to(NOW + timedelta(minutes=6))
    do_adhoc_statistics(hass, start=NOW)
    await async_wait_recording_done(hass)


async def _log(hass, day, entries):
    data = {"entries": entries} | ({"date": day} if day else {})
    await hass.services.async_call(DOMAIN, "log_food", data,
                                   blocking=True, return_response=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)


async def _settle(hass):
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)


LUNCH = {"category": "lunch", "description": "Gryta", "kcal": 500, "protein_g": 30}
DINNER = {"category": "dinner", "description": "Lax", "kcal": 700}


async def test_logged_past_days_land_on_the_sensors(hass, entry, freezer):
    await _compile_once(hass, freezer)
    await _log(hass, "2026-11-07", [LUNCH])
    await _log(hass, "2026-11-08", [DINNER])
    assert await _changes(hass, INTAKE) == {date(2026, 11, 7): 500, date(2026, 11, 8): 700}
    assert await _changes(hass, PROTEIN) == {date(2026, 11, 7): 30}


async def test_waits_for_the_recorder_then_syncs_within_the_hour(hass, entry, freezer):
    await _log(hass, "2026-11-07", [LUNCH])  # sensors not compiled yet: waits
    assert await _changes(hass, INTAKE) == {}
    await _compile_once(hass, freezer)
    later = NOW + timedelta(hours=1, minutes=10)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await _settle(hass)
    assert await _changes(hass, INTAKE) == {date(2026, 11, 7): 500}


async def test_external_series_are_no_longer_written(hass, entry, freezer):
    await _compile_once(hass, freezer)
    await _log(hass, "2026-11-07", [LUNCH])
    meta = await get_instance(hass).async_add_executor_job(
        lambda: get_metadata(hass, statistic_ids={"ha_nutrition:kcal", "ha_nutrition:protein"}))
    assert meta == {}


async def test_corrected_day_is_adjusted(hass, entry, freezer):
    await _compile_once(hass, freezer)
    await _log(hass, "2026-11-07", [LUNCH])
    store = entry.runtime_data.store
    [logged] = store.entries_on(date(2026, 11, 7))
    await hass.services.async_call(DOMAIN, "update_entry", {"entry_id": logged.id, "kcal": 650},
                                   blocking=True, return_response=True)
    await _settle(hass)
    assert await _changes(hass, INTAKE) == {date(2026, 11, 7): 650}


async def test_intake_attributes_list_today(hass, entry):
    await _log(hass, None, [{**LUNCH, "time": "12:00"}, {"category": "snack", "description": "Kanelbulle",
                                                              "kcal": 310, "time": "15:00"}])
    attrs = hass.states.get(INTAKE).attributes
    assert attrs["by_category"] == {"lunch": 500, "snack": 310}
    assert attrs["entries"] == [
        {"time": "12:00", "category": "lunch", "description": "Gryta", "kcal": 500},
        {"time": "15:00", "category": "snack", "description": "Kanelbulle", "kcal": 310},
    ]


@pytest.mark.parametrize("legacy", [True, False])
async def test_legacy_repair(hass, entry, freezer, legacy):
    await _compile_once(hass, freezer)
    if legacy:
        meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name="x", source=DOMAIN,
                                 statistic_id="ha_nutrition:kcal_lunch", unit_class=None, unit_of_measurement="kcal")
        async_add_external_statistics(hass, meta, [StatisticData(start=datetime(2026, 1, 1, 12, tzinfo=UTC),
                                                                 state=1, sum=1)])
        await async_wait_recording_done(hass)
    await _log(hass, "2026-11-07", [LUNCH])  # first sync changes things
    later = NOW + timedelta(hours=1, minutes=10)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)  # next sync: nothing left to change
    await _settle(hass)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, repairs.ISSUE_ID)
    assert (issue is not None) is legacy
    if legacy:
        flow = await repairs.async_create_fix_flow(hass, repairs.ISSUE_ID, None)
        flow.hass = hass
        await flow.async_step_init()
        assert (await flow.async_step_confirm({}))["type"] == "create_entry"
        await async_wait_recording_done(hass)
        meta = await get_instance(hass).async_add_executor_job(
            lambda: get_metadata(hass, statistic_ids={"ha_nutrition:kcal_lunch"}))
        assert meta == {}


def test_entities_opt_in_to_in_place_entity_id_changes():
    """HA 2026.11: entities with async_added_to_hass must implement async_entity_id_changed
    in the same class, or a rename removes and re-adds them (until 2027.11)."""
    from custom_components.ha_nutrition.number import GoalNumber
    from custom_components.ha_nutrition.sensor import NutritionSensor

    for cls in (NutritionSensor, GoalNumber):
        assert "async_added_to_hass" in cls.__dict__
        assert "async_entity_id_changed" in cls.__dict__, cls.__name__

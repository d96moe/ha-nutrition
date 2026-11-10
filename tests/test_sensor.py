from datetime import date, datetime

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.ha_nutrition.const import CONF_BMR_ENTITY, CONF_BURN_ENTITY, CONF_WEIGHT_ENTITY, DOMAIN
from custom_components.ha_nutrition.sensor import budget_left, remaining_day_fraction

BURN, BMR, WEIGHT = "sensor.burn", "sensor.bmr", "sensor.weight"


@pytest.fixture
def options():
    return {CONF_BURN_ENTITY: BURN, CONF_BMR_ENTITY: BMR, CONF_WEIGHT_ENTITY: WEIGHT}


def s(hass, name):
    return hass.states.get(f"sensor.nutrition_{name}")


def value(hass, name):
    return float(s(hass, name).state)


async def log(hass, entries, day=None):
    data = {"entries": entries} | ({"date": day} if day else {})
    await hass.services.async_call(DOMAIN, "log_food", data, blocking=True, return_response=True)
    await hass.async_block_till_done()


async def set_goal(hass, key, val):
    await hass.services.async_call("number", "set_value", {"entity_id": f"number.nutrition_{key}", "value": val},
                                   blocking=True)
    await hass.async_block_till_done()


async def test_intake_and_midnight_reset(hass, entry, freezer):
    await log(hass, [{"category": "lunch", "description": "x", "kcal": 640, "protein_g": 30, "sat_fat_g": 9}])
    assert value(hass, "intake_today") == 640
    assert value(hass, "protein_today") == 30
    assert value(hass, "saturated_fat_today") == 9
    assert s(hass, "intake_today").attributes["date"] == "2026-11-09"
    midnight = datetime.fromisoformat("2026-11-09T23:00:00+00:00")  # 00:00 on 11-10 in Stockholm
    freezer.move_to(midnight)
    async_fire_time_changed(hass, midnight)
    await hass.async_block_till_done()
    assert value(hass, "intake_today") == 0
    assert s(hass, "intake_today").attributes["date"] == "2026-11-10"


async def test_alcohol_today_and_week_monday_start(hass, entry):
    wine = {"category": "drink", "description": "Vin", "kcal": 120, "alcohol_units": 1}
    await log(hass, [wine, wine], day="2026-11-08")  # Sunday, last week
    await log(hass, [wine])  # Monday 11-09
    assert value(hass, "alcohol_today") == 1
    assert value(hass, "alcohol_this_week") == 1
    assert s(hass, "alcohol_this_week").attributes["week_start"] == "2026-11-09"
    assert "limit" not in s(hass, "alcohol_this_week").attributes
    await set_goal(hass, "alcohol_limit_per_week", 7)
    assert s(hass, "alcohol_this_week").attributes["limit"] == 7


async def test_net_and_budget(hass, entry, freezer):
    freezer.move_to("2026-11-09T17:00:00+00:00")  # 18:00 local: a quarter of the day left
    hass.states.async_set(BURN, "1500", {"date": "2026-11-09"})
    hass.states.async_set(BMR, "1800")
    await set_goal(hass, "deficit_target", 500)
    await log(hass, [{"category": "dinner", "description": "x", "kcal": 640}])
    assert value(hass, "net_today") == 640 - 1500
    assert value(hass, "budget_left_today") == 1500 + 1800 * 0.25 - 500 - 640
    attrs = s(hass, "budget_left_today").attributes
    assert attrs["forecast"] is True and attrs["projected_burn"] == 1950


async def test_burn_from_yesterday_is_unavailable(hass, entry):
    hass.states.async_set(BURN, "2900", {"date": "2026-11-08"})  # Samsung hasn't polled since midnight
    hass.states.async_set(BMR, "1800")
    await set_goal(hass, "deficit_target", 500)
    await log(hass, [{"category": "breakfast", "description": "x", "kcal": 400}])
    assert s(hass, "net_today").state == STATE_UNAVAILABLE
    assert s(hass, "budget_left_today").state == STATE_UNAVAILABLE
    assert value(hass, "intake_today") == 400
    hass.states.async_set(BURN, "600", {"date": "2026-11-09"})
    await hass.async_block_till_done()
    assert value(hass, "net_today") == -200


async def test_burn_without_date_unchanged_since_yesterday_is_unavailable(hass, entry, freezer):
    """Sources without a date attribute: a value not changed since local midnight is yesterday's."""
    freezer.move_to("2026-11-08T22:30:00+00:00")  # 23:30 local, yesterday
    hass.states.async_set(BURN, "2900")
    freezer.move_to("2026-11-09T10:00:00+00:00")
    await log(hass, [{"category": "breakfast", "description": "x", "kcal": 400}])
    assert s(hass, "net_today").state == STATE_UNAVAILABLE
    hass.states.async_set(BURN, "700")  # the source's first update today
    await hass.async_block_till_done()
    assert value(hass, "net_today") == -300


async def test_unset_goals_make_sensors_unavailable(hass, entry):
    hass.states.async_set(BURN, "1500", {"date": "2026-11-09"})
    hass.states.async_set(BMR, "1800")
    hass.states.async_set(WEIGHT, "90.4")
    await hass.async_block_till_done()
    for name in ("budget_left_today", "protein_left_today", "to_target_weight"):
        assert s(hass, name).state == STATE_UNAVAILABLE, name
    await log(hass, [{"category": "lunch", "description": "x", "kcal": 500, "protein_g": 30}])
    await set_goal(hass, "protein_floor", 150)
    assert value(hass, "protein_left_today") == 120
    await log(hass, [{"category": "supplement", "description": "shake", "kcal": 800, "protein_g": 170}])
    assert value(hass, "protein_left_today") == 0
    await set_goal(hass, "target_weight", 85)
    assert value(hass, "to_target_weight") == pytest.approx(5.4)


async def test_missing_source_entities(hass, entry):
    await log(hass, [{"category": "lunch", "description": "x", "kcal": 500}])
    assert value(hass, "intake_today") == 500
    for name in ("net_today", "budget_left_today", "to_target_weight"):
        assert s(hass, name).state == STATE_UNAVAILABLE, name
    hass.states.async_set(BURN, "unavailable")
    await hass.async_block_till_done()
    assert s(hass, "net_today").state == STATE_UNAVAILABLE


async def test_restored_goals_available_at_once(hass, freezer, options):
    freezer.move_to("2026-11-09T10:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/Stockholm")
    mock_restore_cache_with_extra_data(hass, [(
        State("number.nutrition_protein_floor", "150.0"),
        {"native_max_value": 400, "native_min_value": 0, "native_step": 1,
         "native_unit_of_measurement": "g", "native_value": 150.0},
    )])
    config_entry = MockConfigEntry(domain=DOMAIN, title="Nutrition", options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()  # no time tick: available without waiting for the 5-minute refresh
    assert value(hass, "protein_left_today") == 150


async def test_empty_log_at_startup_keeps_the_sensors_history(hass, entry, hass_storage, freezer):
    """A lost or corrupt log file must not set every logged day's history to 0."""
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
        do_adhoc_statistics,
    )

    from .test_daily_history import INTAKE, NOW, _changes

    freezer.move_to(NOW + timedelta(minutes=6))
    do_adhoc_statistics(hass, start=NOW)
    await async_wait_recording_done(hass)
    await log(hass, [{"category": "lunch", "description": "x", "kcal": 500}], day="2026-11-08")
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert (await _changes(hass, INTAKE))[date(2026, 11, 8)] == 500
    await hass.config_entries.async_unload(entry.entry_id)
    del hass_storage["ha_nutrition.log"]  # e.g. a corrupt file HA moved aside
    assert await hass.config_entries.async_setup(entry.entry_id)
    later = NOW + timedelta(hours=1, minutes=10)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)  # the hourly sync runs
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert (await _changes(hass, INTAKE))[date(2026, 11, 8)] == 500


async def test_remaining_fraction_dst(hass):
    await hass.config.async_set_time_zone("Europe/Stockholm")
    tz = dt_util.get_default_time_zone()
    # 2026-10-25 has 25 hours in Stockholm; at 12:00 local, 12 of them are left
    assert remaining_day_fraction(datetime(2026, 10, 25, 12, tzinfo=tz)) == pytest.approx(12 / 25)
    assert remaining_day_fraction(datetime(2026, 11, 9, 18, tzinfo=tz)) == pytest.approx(0.25)


def test_budget_left_formula():
    assert budget_left(burn=1500, bmr=1800, fraction=0.25, deficit=500, intake=640) == 810


async def test_daily_totals_have_state_class_total_with_reset(hass, entry):
    """Like a 'rain today' sensor: the recorder keeps their statistics on the sensors."""
    midnight = datetime.fromisoformat("2026-11-08T23:00:00+00:00")  # 2026-11-09 00:00 Stockholm (a Monday)
    for name in ("intake_today", "protein_today", "saturated_fat_today", "alcohol_today", "alcohol_this_week"):
        attrs = s(hass, name).attributes
        assert attrs["state_class"] == "total", name
        assert datetime.fromisoformat(attrs["last_reset"]) == midnight, name
    for name in ("net_today", "budget_left_today", "protein_left_today", "to_target_weight"):
        assert "state_class" not in s(hass, name).attributes, name  # not totals

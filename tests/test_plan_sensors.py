"""Derived plan sensors: net 7-day average, deficit since start, expected weight."""
from datetime import UTC, date, datetime

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics, statistics_during_period
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_nutrition.const import (
    CONF_BURN_ENTITY,
    CONF_BURN_STATISTIC,
    CONF_WEIGHT_ENTITY,
    DOMAIN,
)
from custom_components.ha_nutrition.plan import summarize

from .test_daily_history import _log, _settle
from .test_llm_api import CONTEXT, call
from .test_net_history import BURN_ID, _burn

D = lambda d: date(2026, 11, d)  # noqa: E731
AVG, DEFICIT, EXPECTED = ("sensor.nutrition_net_7_day_average", "sensor.nutrition_deficit_since_start",
                          "sensor.nutrition_expected_weight")
WEIGHT = "sensor.weight"


@pytest.fixture
def options():
    return {CONF_BURN_STATISTIC: BURN_ID, CONF_BURN_ENTITY: "sensor.burn", CONF_WEIGHT_ENTITY: WEIGHT}


def test_summarize():
    net = {D(1): -500.0, D(2): -700.0, D(4): -300.0, D(8): -600.0, D(9): -9999.0}  # D(9) is today
    s = summarize(net, today=D(9), start=D(2), start_weight=90.0)
    assert s.avg_7 == pytest.approx((-700 - 300 - 600) / 3)  # 11-02..11-08 with a net
    assert s.deficit_since_start == 1600  # 700 + 300 + 600, from the start, today excluded
    assert s.expected[D(2)] == pytest.approx(90 - 700 / 7700)
    assert s.expected[D(3)] == pytest.approx(90 - 700 / 7700)  # no net that day: no change
    assert s.expected[D(8)] == pytest.approx(90 - 1600 / 7700)
    assert D(9) not in s.expected and D(1) not in s.expected


def test_today_not_in_derived_values():
    s = summarize({D(9): -2000.0}, today=D(9), start=D(1), start_weight=90.0)
    assert s.avg_7 is None and s.deficit_since_start == 0 and D(9) not in s.expected


def test_summarize_without_start():
    s = summarize({D(8): -500.0, D(7): -500.0, D(6): -500.0}, today=D(9), start=None, start_weight=None)
    assert s.avg_7 == -500 and s.deficit_since_start is None and s.expected == {}


async def _goals(hass, **kw):
    instance = await llm.async_get_api(hass, DOMAIN, CONTEXT)
    await call(instance, "set_goals", **kw)
    await _settle(hass)


async def _weighing(hass, when, kg):
    meta = StatisticMetaData(mean_type=StatisticMeanType.ARITHMETIC, has_sum=False, name=None, source="recorder",
                             statistic_id=WEIGHT, unit_class="mass", unit_of_measurement="kg")
    async_import_statistics(hass, meta, [StatisticData(start=when, mean=kg, min=kg, max=kg)])
    await async_wait_recording_done(hass)


async def _setup_days(hass):
    hass.states.async_set("sensor.burn", "900", {"date": "2026-11-09", "unit_of_measurement": "kcal"})
    await _burn(hass, [(6, 2500), (7, 2400), (8, 2500)])
    for day, kcal in (("2026-11-06", 2000), ("2026-11-07", 1700), ("2026-11-08", 1900)):
        await _log(hass, day, [{"category": "dinner", "description": "x", "kcal": kcal}])


async def test_derived_sensors(hass, entry):
    await _setup_days(hass)  # nets: -500, -700, -600
    await _goals(hass, plan_start_date="2026-11-07", start_weight=90)
    assert float(hass.states.get(AVG).state) == -600
    assert float(hass.states.get(DEFICIT).state) == 1300
    assert float(hass.states.get(EXPECTED).state) == pytest.approx(90 - 1300 / 7700, abs=0.01)


async def test_no_start_weight_is_unavailable(hass, entry):
    await _setup_days(hass)
    await _goals(hass, plan_start_date="2026-11-07")  # no start weight, no weighing
    assert hass.states.get(EXPECTED).state == STATE_UNAVAILABLE
    assert float(hass.states.get(DEFICIT).state) == 1300


async def test_start_weight_from_first_weighing(hass, entry, freezer):
    freezer.move_to("2026-11-07T06:00:00+00:00")
    hass.states.async_set(WEIGHT, "91.0", {"unit_of_measurement": "kg"})
    await async_wait_recording_done(hass)
    freezer.move_to("2026-11-09T10:00:00+00:00")
    await _setup_days(hass)
    await _goals(hass, plan_start_date="2026-11-07")  # start weight 0: first weighing on/after
    assert float(hass.states.get(EXPECTED).state) == pytest.approx(91 - 1300 / 7700, abs=0.01)


async def test_changing_start_date_recomputes(hass, entry):
    await _setup_days(hass)
    await _goals(hass, plan_start_date="2026-11-07", start_weight=90)
    await _goals(hass, plan_start_date="2026-11-06")
    assert float(hass.states.get(DEFICIT).state) == 1800
    assert float(hass.states.get(EXPECTED).state) == pytest.approx(90 - 1800 / 7700, abs=0.01)


async def test_expected_weight_history(hass, entry):
    await _setup_days(hass)
    await _goals(hass, plan_start_date="2026-11-06", start_weight=90)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 11, 1, tzinfo=UTC), None, {EXPECTED}, "day", None, {"mean"})
    means = {dt_util.as_local(dt_util.utc_from_timestamp(r["start"])).date(): round(r["mean"], 3)
             for r in result.get(EXPECTED, [])}
    assert means[D(6)] == round(90 - 500 / 7700, 3)
    assert means[D(8)] == round(90 - 1800 / 7700, 3)
    assert D(9) not in means or means[D(9)] == round(90 - 1800 / 7700, 3)  # today only from the live sensor


async def test_start_weight_is_the_first_weighing_after_start(hass, entry, freezer):
    """Review #2: not the carried-forward hourly mean of an earlier weighing; kg from lb."""
    freezer.move_to("2026-11-05T06:00:00+00:00")
    hass.states.async_set(WEIGHT, "92.0", {"unit_of_measurement": "kg", "state_class": "measurement"})
    freezer.move_to("2026-11-07T06:00:00+00:00")
    hass.states.async_set(WEIGHT, "200.6", {"unit_of_measurement": "lb", "state_class": "measurement"})
    await async_wait_recording_done(hass)
    from custom_components.ha_nutrition.plan import async_start_weight
    freezer.move_to("2026-11-09T10:00:00+00:00")
    assert await async_start_weight(hass, WEIGHT, D(7)) == pytest.approx(200.6 * 0.45359237, abs=0.01)
    assert await async_start_weight(hass, WEIGHT, D(9)) is None  # nothing on/after


async def test_set_goals_validates_everything_first(hass, entry):
    """Review #3: no half-applied call."""
    instance = await llm.async_get_api(hass, DOMAIN, CONTEXT)
    data = await call(instance, "set_goals", plan_start_date="2026-09-30", deficit_max=5000)
    assert "deficit_max" in data["error"]
    assert entry.runtime_data.plan_start is None
    data = await call(instance, "set_goals", plan_start_date="2026-12-01")
    assert "future" in data["error"]

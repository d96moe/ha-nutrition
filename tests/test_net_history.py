"""Net per day (intake − burn) as history on Net today."""
from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_nutrition.const import CONF_BURN_ENTITY, CONF_BURN_STATISTIC
from custom_components.ha_nutrition.daily_history import net_truth

from .conftest import NOW
from .test_daily_history import _changes, _compile_once, _log, _settle

NET = "sensor.nutrition_net_today"
BURN_ID = "ha_samsung_health:total_calories"
LUNCH = {"category": "lunch", "description": "Gryta", "kcal": 500}


@pytest.fixture
def options():
    return {CONF_BURN_STATISTIC: BURN_ID, CONF_BURN_ENTITY: "sensor.burn"}


@pytest.fixture
async def burn_live(hass, entry):
    """The live burn sensor (Samsung Health's), so Net today is available like in real life."""
    hass.states.async_set("sensor.burn", "900", {"date": "2026-11-09", "unit_of_measurement": "kcal"})
    await hass.async_block_till_done()


async def _burn(hass, values):
    meta = StatisticMetaData(mean_type=StatisticMeanType.NONE, has_sum=True, name="burn",
                             source="ha_samsung_health", statistic_id=BURN_ID, unit_class=None,
                             unit_of_measurement="kcal")
    rows, running = [], 0.0
    for day, kcal in values:
        running += kcal
        rows.append(StatisticData(start=datetime(2026, 11, day, 12, tzinfo=UTC), state=kcal, sum=running))
    async_add_external_statistics(hass, meta, rows)
    await async_wait_recording_done(hass)


def test_net_truth():
    intake = {date(2026, 11, 6): 1800.0, date(2026, 11, 7): 500.0}
    burn = {date(2026, 11, 7): 2400.0, date(2026, 11, 8): 2500.0}
    assert net_truth(intake, burn) == {date(2026, 11, 7): -1900.0}


async def test_net_today_is_a_total(hass, entry, burn_live):
    attrs = hass.states.get(NET).attributes
    assert attrs["state_class"] == "total"
    assert datetime.fromisoformat(attrs["last_reset"]) == dt_util.start_of_local_day()


async def test_net_history_lands(hass, entry, burn_live, freezer):
    await _burn(hass, [(7, 2400)])
    await _compile_once(hass, freezer)
    await _log(hass, "2026-11-07", [LUNCH])
    assert (await _changes(hass, NET)) == {date(2026, 11, 7): -1900}


async def test_net_history_waits_for_burn(hass, entry, burn_live, freezer):
    await _compile_once(hass, freezer)
    await _log(hass, "2026-11-07", [LUNCH])
    assert date(2026, 11, 7) not in await _changes(hass, NET)  # no burn yet: no net
    await _burn(hass, [(7, 2400)])
    later = NOW + timedelta(hours=1, minutes=10)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await _settle(hass)
    assert (await _changes(hass, NET))[date(2026, 11, 7)] == -1900

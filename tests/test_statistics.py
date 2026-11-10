"""Daily totals from the log (the truth the today sensors are reconciled with)."""
from datetime import UTC, date, datetime

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period

from custom_components.ha_nutrition.models import FoodEntry
from custom_components.ha_nutrition.statistics import STATS, build_series, day_bucket, statistic_id

A, B = date(2026, 11, 7), date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


def entry(day, category, kcal, n=[0], **kw) -> FoodEntry:  # noqa: B006 - unique ids
    n[0] += 1
    return FoodEntry(
        id=f"e{n[0]}", date=day, category=category, description="x", kcal=kcal, time=None,
        protein_g=kw.get("protein_g"), sat_fat_g=kw.get("sat_fat_g"), alcohol_units=kw.get("alcohol_units"),
        source="llm", created_at=NOW, updated_at=NOW,
    )


BASE = [entry(A, "breakfast", 400, protein_g=30), entry(B, "drink", 120, alcohol_units=1)]


@pytest.fixture
async def tz(hass):
    await hass.config.async_set_time_zone("Europe/Stockholm")


async def _rows(hass, key):
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2000, 1, 1, tzinfo=UTC), None,
        {statistic_id(key)}, "hour", None, {"state", "sum"},
    )
    return [(datetime.fromtimestamp(r["start"], UTC), r["state"], r["sum"]) for r in result.get(statistic_id(key), [])]


def test_build_series_row_rules():
    series = build_series(BASE)
    assert set(series) == set(STATS)
    assert series["kcal"] == {A: 400, B: 120}
    assert series["protein"] == {A: 30}
    assert series["alcohol"] == {B: 1}
    assert series["sat_fat"] == {}
    assert series["kcal_breakfast"] == {A: 400}
    assert series["kcal_drink"] == {B: 120}
    assert series["kcal_lunch"] == {}


def test_day_bucket_is_noon_utc():
    assert day_bucket(A) == datetime(2026, 11, 7, 12, tzinfo=UTC)














async def test_day_bucket_stays_on_its_local_day_far_east(hass):
    from homeassistant.util import dt as dt_util

    await hass.config.async_set_time_zone("Pacific/Auckland")  # UTC+13 in November
    for day in (date(2026, 11, 6), date(2026, 6, 6)):
        assert dt_util.as_local(day_bucket(day)).date() == day
    await hass.config.async_set_time_zone("Europe/Stockholm")
    assert day_bucket(A) == datetime(2026, 11, 7, 12, tzinfo=UTC)  # unchanged

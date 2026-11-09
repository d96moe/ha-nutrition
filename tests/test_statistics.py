"""Daily totals as external statistics, against a real (test) recorder."""
from datetime import UTC, date, datetime

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.ha_nutrition.models import FoodEntry
from custom_components.ha_nutrition.statistics import (
    STATS,
    StatisticsWriter,
    build_series,
    day_bucket,
    statistic_id,
)

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


async def test_rows_at_noon_utc_with_cumulative_sum(hass, tz):
    assert await StatisticsWriter(hass).async_write(build_series(BASE)) > 0
    await async_wait_recording_done(hass)
    assert await _rows(hass, "kcal") == [(day_bucket(A), 400, 400), (day_bucket(B), 120, 520)]


async def test_only_changed_tail_is_written(hass, tz):
    writer = StatisticsWriter(hass)
    await writer.async_write(build_series(BASE))
    changed = [BASE[0], entry(B, "drink", 120, alcohol_units=1), entry(B, "snack", 200)]
    written = await writer.async_write(build_series(changed))
    # kcal on B, and the new kcal_snack row on B; nothing else changed
    assert written == 2
    await async_wait_recording_done(hass)
    assert await _rows(hass, "kcal") == [(day_bucket(A), 400, 400), (day_bucket(B), 320, 720)]


async def test_removed_day_clears_and_rewrites(hass, tz):
    writer = StatisticsWriter(hass)
    await writer.async_write(build_series(BASE))
    await async_wait_recording_done(hass)
    await writer.async_write(build_series([BASE[1]]))  # all of day A deleted
    await async_wait_recording_done(hass)
    assert await _rows(hass, "kcal") == [(day_bucket(B), 120, 120)]
    assert await _rows(hass, "protein") == []
    assert await _rows(hass, "kcal_breakfast") == []
    assert await _rows(hass, "alcohol") == [(day_bucket(B), 1, 1)]


async def test_restart_writes_nothing_new(hass, tz):
    await StatisticsWriter(hass).async_write(build_series(BASE))
    await async_wait_recording_done(hass)
    assert await StatisticsWriter(hass).async_write(build_series(BASE)) == 0


async def test_statistics_deleted_in_recorder_are_rewritten(hass, tz):
    await StatisticsWriter(hass).async_write(build_series(BASE))
    await async_wait_recording_done(hass)
    get_instance(hass).async_clear_statistics([statistic_id("kcal")])
    await async_wait_recording_done(hass)
    written = await StatisticsWriter(hass).async_write(build_series(BASE))
    assert written == 2
    await async_wait_recording_done(hass)
    assert len(await _rows(hass, "kcal")) == 2


async def test_local_day_grouping(hass, tz):
    await StatisticsWriter(hass).async_write(build_series(BASE))
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 11, 1, tzinfo=UTC), None,
        {statistic_id("kcal")}, "day", None, {"change"},
    )
    assert [r["change"] for r in result[statistic_id("kcal")]] == [400, 120]

"""Daily totals as external long-term statistics ("ha_nutrition:<key>").

One row per day at 12:00 UTC of the day (as ha_samsung_health and ha-workouts),
so the recorder's local-day grouping never moves a row across a day boundary.
`sum` is cumulative. Days without data get no row, so "not logged" stays
distinguishable from "ate nothing".

Every series is computed from the whole log (it is small). What was last written
is remembered in .storage, so a write only sends rows from the first changed day
onwards. Rows are upserts by start time; but a day that disappears (all its
entries deleted or moved) can't be removed with an upsert, so then that statistic
is cleared and written again in full.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics, get_last_statistics
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CATEGORIES, DOMAIN
from .models import FoodEntry

_LOGGER = logging.getLogger(__name__)

#: key -> (name, unit)
STATS: dict[str, tuple[str, str]] = {
    "kcal": ("Nutrition intake", "kcal"),
    "protein": ("Nutrition protein", "g"),
    "sat_fat": ("Nutrition saturated fat", "g"),
    "alcohol": ("Nutrition alcohol", "units"),
    **{f"kcal_{c}": (f"Nutrition intake {c}", "kcal") for c in CATEGORIES},
}
_NUTRIENT_KEYS = {"protein": "protein_g", "sat_fat": "sat_fat_g", "alcohol": "alcohol_units"}

STORE_KEY = "ha_nutrition.statistics"
LAYOUT = 1

Series = dict[str, dict[date, float]]


def statistic_id(key: str) -> str:
    return f"{DOMAIN}:{key}"


def day_bucket(day: date) -> datetime:
    """12:00 UTC, which is on the same local day from UTC-11 to UTC+11. Further east
    or west it would land on another local day; there, local noon (on the hour)."""
    noon_utc = datetime.combine(day, time(12), tzinfo=UTC)
    if dt_util.as_local(noon_utc).date() == day:
        return noon_utc
    local_noon = datetime.combine(day, time(12), tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(local_noon).replace(minute=0, second=0, microsecond=0)


def build_series(entries: Iterable[FoodEntry]) -> Series:
    series: Series = {key: {} for key in STATS}

    def add(key: str, day: date, value: float) -> None:
        series[key][day] = series[key].get(day, 0.0) + value

    for e in entries:
        add("kcal", e.date, e.kcal)
        add(f"kcal_{e.category}", e.date, e.kcal)
        for key, field in _NUTRIENT_KEYS.items():
            if (value := getattr(e, field)) is not None:
                add(key, e.date, value)
    return {key: dict(sorted(values.items())) for key, values in series.items()}


def metadata(key: str) -> StatisticMetaData:
    name, unit = STATS[key]
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id(key),
        unit_class=None,
        unit_of_measurement=unit,
    )


def to_rows(values: dict[date, float], first: date | None = None) -> list[StatisticData]:
    rows, running = [], 0.0
    for day, value in sorted(values.items()):
        running += value
        if first is None or day >= first:
            rows.append(StatisticData(start=day_bucket(day), state=value, sum=running))
    return rows


class StatisticsWriter:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict] = Store(hass, 1, STORE_KEY)
        self._written: dict[str, dict[date, float]] | None = None

    def _missing_in_recorder(self, keys: list[str]) -> list[str]:
        return [k for k in keys if not get_last_statistics(self.hass, 1, statistic_id(k), False, {"sum"})]

    async def _async_load(self) -> dict[str, dict[date, float]]:
        stored = await self._store.async_load() or {}
        if stored.get("layout") != LAYOUT or stored.get("keys") != sorted(STATS):
            return {}  # nothing known: every statistic is written in full
        written = {
            k: {date.fromisoformat(d): v for d, v in days.items()}
            for k, days in stored.get("written", {}).items() if k in STATS
        }
        if keys := [k for k, days in written.items() if days]:
            missing = await get_instance(self.hass).async_add_executor_job(self._missing_in_recorder, keys)
            for key in missing:
                _LOGGER.info("Statistics for %s missing in the recorder; rewriting in full", key)
                written.pop(key)
        return written

    async def async_write(self, series: Series, *, full: bool = False) -> int:
        """Write what changed since the last write; returns the number of rows sent."""
        first_load = self._written is None
        if self._written is None:
            self._written = await self._async_load()
            full = full or not self._written
        rows_sent, changed = 0, False
        for key in STATS:
            new = series.get(key, {})
            old = self._written.get(key)
            if not full and old == new:
                continue
            if full or old is None or set(old) - set(new):
                if old or full:
                    get_instance(self.hass).async_clear_statistics([statistic_id(key)])
                rows = to_rows(new)
            else:
                rows = to_rows(new, min(d for d in new if old.get(d) != new[d]))
            if rows:
                async_add_external_statistics(self.hass, metadata(key), rows)
            self._written[key] = dict(new)
            rows_sent += len(rows)
            changed = True
        if changed or first_load:
            # Remember what was written only once the recorder has it.
            await get_instance(self.hass).async_block_till_done()
            await self._store.async_save({
                "layout": LAYOUT,
                "keys": sorted(STATS),
                "written": {k: {d.isoformat(): v for d, v in days.items()} for k, days in self._written.items()},
            })
        return rows_sent

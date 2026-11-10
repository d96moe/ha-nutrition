"""Daily totals from the food log, per statistic key.

They are the truth the today sensors' history is reconciled with (daily_history.py).
Until 0.2.0 they were also written as external statistics ("ha_nutrition:<key>");
those ids are kept in STATS for removing them (repairs.py).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

from .const import CATEGORIES, DOMAIN
from .models import FoodEntry

#: key -> (name, unit)
STATS: dict[str, tuple[str, str]] = {
    "kcal": ("Nutrition intake", "kcal"),
    "protein": ("Nutrition protein", "g"),
    "sat_fat": ("Nutrition saturated fat", "g"),
    "alcohol": ("Nutrition alcohol", "units"),
    **{f"kcal_{c}": (f"Nutrition intake {c}", "kcal") for c in CATEGORIES},
}
_NUTRIENT_KEYS = {"protein": "protein_g", "sat_fat": "sat_fat_g", "alcohol": "alcohol_units"}


Series = dict[str, dict[date, float]]


#: The recorder compiles an hour a few minutes after it ends; leave it this long.
COMPILE_MARGIN = timedelta(minutes=15)


def compiled_hours_end(now: datetime) -> datetime:
    """Start of the first hour the recorder may not have compiled yet."""
    return (now - COMPILE_MARGIN).astimezone(UTC).replace(minute=0, second=0, microsecond=0)


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

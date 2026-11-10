"""The today sensors' history, from the food log.

Every logged day's totals are known from the log, so the sensors' own statistics
are reconciled with them (sensor_history.py): logged, backfilled and corrected
days all end up on intake/protein/saturated fat/alcohol today. The old
ha_nutrition:* statistics are no longer written (see repairs.py).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import STATISTIC_UNIT_TO_UNIT_CONVERTER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .burn import async_burn_source, async_daily_burn
from .const import DOMAIN
from .sensor_history import SyncResult, async_statistics_unit, async_sync_daily_history
from .statistics import build_series

if TYPE_CHECKING:
    from . import NutritionConfigEntry

_LOGGER = logging.getLogger(__name__)

#: series key (statistics.build_series) -> today sensor key
SENSOR_KEYS = {"kcal": "intake_today", "protein": "protein_today", "sat_fat": "sat_fat_today",
               "alcohol": "alcohol_today", "net": "net_today"}


def net_truth(intake: dict[date, float], burn: dict[date, float]) -> dict[date, float]:
    """Net (intake − burn) for the days that have both; a day without burn has no net yet."""
    return {day: kcal - burn[day] for day, kcal in intake.items() if day in burn}


def unit_class(unit: str | None) -> str | None:
    """Same rule as the sensor recorder for a sensor without device class."""
    converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit) if unit else None
    return converter.UNIT_CLASS if converter else None


async def async_sync_sensor_history(
    hass: HomeAssistant, entry: NutritionConfigEntry, now: datetime
) -> dict[str, SyncResult]:
    series = build_series(entry.runtime_data.store.all_entries())
    series["net"] = {}
    if series["kcal"]:
        source = await async_burn_source(hass, entry.options)
        burn = await async_daily_burn(hass, source, min(series["kcal"]), dt_util.as_local(now).date())
        series["net"] = net_truth(series["kcal"], burn)
    registry = er.async_get(hass)
    results: dict[str, SyncResult] = {}
    for key, sensor_key in SENSOR_KEYS.items():
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{sensor_key}")
        state = hass.states.get(entity_id) if entity_id else None
        if entity_id is None or state is None:
            continue
        # The unit of existing statistics wins (the recorder keeps it); the log is in
        # these units anyway (kcal, g, units), so a different one can't be converted.
        existing = await async_statistics_unit(hass, entity_id)
        unit = state.attributes.get("unit_of_measurement")
        if existing and existing[0] != unit:
            _LOGGER.warning("No history for %s: its statistics are in %s, not %s", entity_id, existing[0], unit)
            continue
        results[key] = await async_sync_daily_history(
            hass, entity_id, unit, existing[1] if existing else unit_class(unit), series[key], now
        )
    return results

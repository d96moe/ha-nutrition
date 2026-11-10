"""The today sensors' history, from the food log.

Every logged day's totals are known from the log, so the sensors' own statistics
are reconciled with them (sensor_history.py): logged, backfilled and corrected
days all end up on intake/protein/saturated fat/alcohol today. The old
ha_nutrition:* statistics are no longer written (see repairs.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import STATISTIC_UNIT_TO_UNIT_CONVERTER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .sensor_history import SyncResult, async_sync_daily_history
from .statistics import build_series

if TYPE_CHECKING:
    from . import NutritionConfigEntry

#: series key (statistics.build_series) -> today sensor key
SENSOR_KEYS = {"kcal": "intake_today", "protein": "protein_today", "sat_fat": "sat_fat_today",
               "alcohol": "alcohol_today"}


def unit_class(unit: str | None) -> str | None:
    """Same rule as the sensor recorder for a sensor without device class."""
    converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit) if unit else None
    return converter.UNIT_CLASS if converter else None


async def async_sync_sensor_history(
    hass: HomeAssistant, entry: NutritionConfigEntry, now: datetime
) -> dict[str, SyncResult]:
    series = build_series(entry.runtime_data.store.all_entries())
    registry = er.async_get(hass)
    results: dict[str, SyncResult] = {}
    for key, sensor_key in SENSOR_KEYS.items():
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{sensor_key}")
        state = hass.states.get(entity_id) if entity_id else None
        if entity_id is None or state is None:
            continue
        unit = state.attributes.get("unit_of_measurement")
        results[key] = await async_sync_daily_history(hass, entity_id, unit, unit_class(unit), series[key], now)
    return results

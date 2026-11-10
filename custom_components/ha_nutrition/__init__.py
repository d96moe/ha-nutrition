"""Nutrition: food intake logged into Home Assistant, by AI agents over MCP or by services.

Setup only loads the log file and registers things: no network, no waiting on the
recorder. Statistics are written in background tasks.

There is deliberately no async_remove_entry: the log (.storage/ha_nutrition.log)
and its statistics outlive the config entry, so removing and re-adding the
integration loses nothing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_UPDATED
from .daily_history import async_sync_sensor_history
from .llm_api import NutritionAPI
from .repairs import async_check_legacy, async_handle_remove_service
from .services import async_register_services
from .store import NutritionStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.NUMBER, Platform.SENSOR]
SERVICE_REMOVE_LEGACY = "remove_legacy_statistics"
SYNC_INTERVAL = timedelta(hours=1)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class NutritionData:
    store: NutritionStore
    #: goal key -> value; 0 = not set. Filled by the number entities.
    goals: dict[str, float] = field(default_factory=dict)
    stats_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: False while the log is empty since startup (a lost or corrupt log file): then
    #: nothing is synced, which would set every logged day's history to 0.
    history_armed: bool = False


type NutritionConfigEntry = ConfigEntry[NutritionData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_LEGACY, async_handle_remove_service)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NutritionConfigEntry) -> bool:
    store = NutritionStore(hass)
    await store.async_load()
    data = NutritionData(store=store, history_armed=bool(store.all_entries()))
    entry.runtime_data = data

    async def _async_sync_history() -> None:
        async with data.stats_lock:  # one at a time: two syncs would apply a change twice
            if not data.history_armed:
                return
            try:
                results = await async_sync_sensor_history(hass, entry, dt_util.utcnow())
                await async_check_legacy(hass, results)
            except Exception:  # never let a statistics problem break logging
                _LOGGER.exception("Updating the Nutrition sensors' history failed")

    @callback
    def _schedule_sync(*_: object) -> None:
        entry.async_create_background_task(hass, _async_sync_history(), "ha_nutrition history")

    @callback
    def _changed(days: set[date]) -> None:
        data.history_armed = True
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        _schedule_sync()

    entry.async_on_unload(store.async_add_listener(_changed))
    entry.async_on_unload(llm.async_register_api(hass, NutritionAPI(hass, entry)))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Hourly as well: the recorder must have compiled a new sensor before its history
    # can be added, and a day can only be reconciled once it is over and compiled.
    entry.async_on_unload(async_track_time_interval(hass, _schedule_sync, SYNC_INTERVAL))
    if data.history_armed:
        _schedule_sync()
    else:
        _LOGGER.debug("Food log is empty; the sensors' history is left as it is")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NutritionConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

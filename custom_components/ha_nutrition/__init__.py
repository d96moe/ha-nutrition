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
from datetime import date

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SIGNAL_UPDATED
from .llm_api import NutritionAPI
from .services import async_register_services
from .statistics import StatisticsWriter, build_series
from .store import NutritionStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.NUMBER, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class NutritionData:
    store: NutritionStore
    writer: StatisticsWriter
    #: goal key -> value; 0 = not set. Filled by the number entities.
    goals: dict[str, float] = field(default_factory=dict)
    stats_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


type NutritionConfigEntry = ConfigEntry[NutritionData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NutritionConfigEntry) -> bool:
    store = NutritionStore(hass)
    await store.async_load()
    data = NutritionData(store=store, writer=StatisticsWriter(hass))
    entry.runtime_data = data

    async def _async_write_statistics() -> None:
        async with data.stats_lock:  # the series is built inside, so the last write wins
            await data.writer.async_write(build_series(store.all_entries()))

    @callback
    def _changed(days: set[date]) -> None:
        async_dispatcher_send(hass, SIGNAL_UPDATED)
        entry.async_create_background_task(hass, _async_write_statistics(), "ha_nutrition statistics")

    entry.async_on_unload(store.async_add_listener(_changed))
    entry.async_on_unload(llm.async_register_api(hass, NutritionAPI(hass, entry)))
    if store.all_entries():
        entry.async_create_background_task(hass, _async_write_statistics(), "ha_nutrition statistics")
    else:
        # An empty log at startup is a new install or a lost/corrupt log file. Writing
        # now would clear statistics that may be the only other copy of the history;
        # the first real change writes them.
        _LOGGER.debug("Food log is empty; statistics left as they are")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NutritionConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

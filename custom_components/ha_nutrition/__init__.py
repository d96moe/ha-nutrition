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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import CONF_WEIGHT_ENTITY, DOMAIN, SIGNAL_PLAN_UPDATED, SIGNAL_UPDATED
from .daily_history import async_daily_series, async_sync_sensor_history
from .llm_api import NutritionAPI
from .number import goal
from .plan import ExpectedWeightHistory, PlanSummary, async_start_weight, summarize
from .repairs import async_check_legacy, async_handle_remove_service
from .services import async_register_services
from .store import NutritionStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.NUMBER, Platform.DATE, Platform.SENSOR]
SERVICE_REMOVE_LEGACY = "remove_legacy_statistics"
SYNC_INTERVAL = timedelta(hours=1)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class NutritionData:
    store: NutritionStore
    #: goal key -> value; 0 = not set. Filled by the number entities.
    goals: dict[str, float] = field(default_factory=dict)
    stats_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The plan's start date (date entity); None = not set.
    plan_start: date | None = None
    #: False while the log is empty since startup (a lost or corrupt log file): then
    #: nothing is synced, which would set every logged day's history to 0.
    history_armed: bool = False
    #: Derived plan values (plan.py), recomputed on every sync.
    plan: PlanSummary = field(default_factory=PlanSummary)
    expected_history: ExpectedWeightHistory = field(default_factory=ExpectedWeightHistory)
    sync_pending: bool = False


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
            data.sync_pending = False  # changes from here on schedule another sync
            now = dt_util.utcnow()
            try:
                series = await async_daily_series(hass, entry, now)
            except Exception:  # never let a statistics problem break logging
                _LOGGER.exception("Reading the Nutrition daily totals failed")
                return
            try:
                if data.history_armed:
                    results = await async_sync_sensor_history(hass, entry, series, now)
                    await async_check_legacy(hass, results)
            except Exception:
                _LOGGER.exception("Updating the Nutrition sensors' history failed")
            try:
                if series["net"] is not None:  # burn unknown: keep the plan values as they are
                    await _async_update_plan(series["net"], now)
            except Exception:
                _LOGGER.exception("Updating the Nutrition plan values failed")

    async def _async_update_plan(net_by_day: dict[date, float], now) -> None:
        start = data.plan_start
        start_weight = goal(entry, "start_weight")
        if start and not start_weight:
            start_weight = await async_start_weight(hass, entry.options.get(CONF_WEIGHT_ENTITY), start)
        data.plan = summarize(net_by_day, dt_util.as_local(now).date(), start, start_weight)
        async_dispatcher_send(hass, SIGNAL_PLAN_UPDATED)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_expected_weight")
        if entity_id and not registry.async_get(entity_id).disabled_by:
            await data.expected_history.async_write(hass, entity_id, data.plan.expected, now)

    @callback
    def _schedule_sync(*_: object) -> None:
        if data.sync_pending:  # a burst of changes (e.g. goals restored at startup) = one sync
            return
        data.sync_pending = True
        entry.async_create_background_task(hass, _async_sync_history(), "ha_nutrition history")

    @callback
    def _changed(days: set[date]) -> None:
        data.history_armed = True
        async_dispatcher_send(hass, SIGNAL_UPDATED)

    entry.async_on_unload(store.async_add_listener(_changed))
    # Log, goal and plan changes all recompute the history and the plan values.
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_UPDATED, _schedule_sync))
    entry.async_on_unload(llm.async_register_api(hass, NutritionAPI(hass, entry)))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Hourly as well: the recorder must have compiled a new sensor before its history
    # can be added, and a day can only be reconciled once it is over and compiled.
    entry.async_on_unload(async_track_time_interval(hass, _schedule_sync, SYNC_INTERVAL))
    if not data.history_armed:
        _LOGGER.debug("Food log is empty; the sensors' history is left as it is")
    _schedule_sync()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NutritionConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

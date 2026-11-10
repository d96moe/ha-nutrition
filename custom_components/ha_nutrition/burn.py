"""Burn (total calories burned) per day, from whichever source the options name.

Shared by the LLM API (get_nutrition) and the net history (daily_history.py).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from functools import partial
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata, statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_BURN_ENTITY, CONF_BURN_STATISTIC


async def async_daily_burn(hass: HomeAssistant, statistic_id: str | None, start: date, end: date) -> dict[date, float]:
    """Burn per local day from a daily-total statistic (e.g. ha_samsung_health:total_calories)."""
    if not statistic_id:
        return {}
    start_dt = dt_util.start_of_local_day(start)
    end_dt = dt_util.start_of_local_day(end + timedelta(days=1))
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start_dt, end_dt, {statistic_id}, "day", None, {"change"}
    )
    return {
        dt_util.as_local(dt_util.utc_from_timestamp(r["start"])).date(): r["change"]
        for r in rows.get(statistic_id, [])
        if r.get("change") is not None
    }


async def async_burn_source(hass: HomeAssistant, options: Mapping[str, Any]) -> str | None:
    """Statistic id for burn per day: the configured one if it exists, else the burn sensor's own.

    A daily-total sensor with state_class total/total_increasing (as most health
    integrations have) gets long-term statistics from the recorder, which handle
    its midnight reset, so its daily change is the day's burn. A configured
    statistic that no longer exists (e.g. Samsung Health's pre-0.5.0
    ha_samsung_health:total_calories) falls back to the sensor.
    """
    if statistic := options.get(CONF_BURN_STATISTIC):
        metadata = await get_instance(hass).async_add_executor_job(
            partial(get_metadata, hass, statistic_ids={statistic})
        )
        if metadata:
            return statistic
    entity_id = options.get(CONF_BURN_ENTITY)
    state = hass.states.get(entity_id) if entity_id else None
    if state is not None and state.attributes.get("state_class") in ("total", "total_increasing"):
        return entity_id
    return None

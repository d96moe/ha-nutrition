"""The plan's derived values: net 7-day average, deficit since start, expected weight.

Everything is computed from net per day (intake − burn, daily_history.net_truth)
and the plan entities (start date, start weight). Today is never included: the day
isn't over. The expected weight's daily history is written onto its own
statistics as daily means; when the series changes (a corrected day, a new start
date) it is cleared and written again in full, which is safe for a measurement
(no running sum).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import MassConverter

from .sensor_history import async_statistics_unit
from .statistics import compiled_hours_end, day_bucket

_LOGGER = logging.getLogger(__name__)

KCAL_PER_KG = 7700
AVERAGE_DAYS = 7
AVERAGE_MIN_DAYS = 3


@dataclass(slots=True)
class PlanSummary:
    avg_7: float | None = None
    deficit_since_start: float | None = None
    start_weight: float | None = None
    #: expected weight now (through yesterday)
    current_expected: float | None = None
    #: day -> expected weight at the end of that day, start..yesterday
    expected: dict[date, float] = field(default_factory=dict)


def summarize(
    net_by_day: dict[date, float], today: date, start: date | None, start_weight: float | None
) -> PlanSummary:
    window = [net_by_day[d] for d in net_by_day if today - timedelta(days=AVERAGE_DAYS) <= d < today]
    summary = PlanSummary(avg_7=sum(window) / len(window) if len(window) >= AVERAGE_MIN_DAYS else None)
    if start is None:
        return summary
    summary.deficit_since_start = -sum(v for d, v in net_by_day.items() if start <= d < today)
    if not start_weight:
        return summary
    summary.start_weight = start_weight
    running, day = 0.0, start
    while day < today:
        running += net_by_day.get(day, 0.0)
        summary.expected[day] = start_weight + running / KCAL_PER_KG
        day += timedelta(days=1)
    summary.current_expected = start_weight - summary.deficit_since_start / KCAL_PER_KG
    return summary


async def async_start_weight(hass: HomeAssistant, weight_entity: str | None, start: date) -> float | None:
    """The first weighing on or after the start date (in kg), from the recorder's state history.

    Not from hourly statistics: they carry the last value forward every hour, so the
    first row after the start would be an earlier weighing.
    """
    if not weight_entity:
        return None
    states = await get_instance(hass).async_add_executor_job(
        partial(get_significant_states, hass, dt_util.start_of_local_day(start), dt_util.utcnow(),
                [weight_entity], include_start_time_state=False, significant_changes_only=False)
    )
    for state in states.get(weight_entity, []):
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            continue
        unit = state.attributes.get("unit_of_measurement", UnitOfMass.KILOGRAMS)
        if unit == UnitOfMass.KILOGRAMS:
            return value
        if unit in MassConverter.VALID_UNITS:
            return MassConverter.convert(value, unit, UnitOfMass.KILOGRAMS)
        return None
    return None


class ExpectedWeightHistory:
    """Writes the expected weight's daily means, in full whenever the series changed."""

    def __init__(self) -> None:
        self._written: dict[date, float] | None = None

    async def async_write(self, hass: HomeAssistant, entity_id: str, expected: dict[date, float],
                          now: datetime) -> None:
        compiled_before = compiled_hours_end(now)
        rows = {d: round(v, 3) for d, v in expected.items() if day_bucket(d) < compiled_before}
        if rows == self._written:
            return
        existing = await async_statistics_unit(hass, entity_id)
        if existing and existing[0] != "kg":
            _LOGGER.warning("Not writing history for %s: its statistics are in %s", entity_id, existing[0])
            return
        instance = get_instance(hass)
        instance.async_clear_statistics([entity_id])
        if rows:
            async_import_statistics(hass, StatisticMetaData(
                mean_type=StatisticMeanType.ARITHMETIC, has_sum=False, name=None, source="recorder",
                statistic_id=entity_id, unit_class="mass", unit_of_measurement="kg",
            ), [StatisticData(start=day_bucket(d), mean=v, min=v, max=v) for d, v in sorted(rows.items())])
        await instance.async_block_till_done()
        self._written = rows

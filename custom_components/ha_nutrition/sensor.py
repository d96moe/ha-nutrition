"""Sensors for today (local calendar day; they start over at local midnight).

No state_class: history lives in the external statistics (ha_nutrition:*), which
past days can be logged into; the sensors' own statistics would break on that.
Sensors needing a source entity or a goal that is missing or unset are
unavailable rather than guessing.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import CONF_BMR_ENTITY, CONF_BURN_ENTITY, CONF_WEIGHT_ENTITY, SIGNAL_UPDATED
from .entity import NutritionEntity
from .models import DayTotals, FoodEntry, day_totals
from .number import goal

if TYPE_CHECKING:
    from . import NutritionConfigEntry

REFRESH = timedelta(minutes=5)  # budget left moves with the clock


def remaining_day_fraction(now: datetime) -> float:
    """Share of today's local day still ahead (a DST day has 23 or 25 hours)."""
    # In UTC: subtracting datetimes that share a tzinfo is wall-clock arithmetic,
    # which would make every day 24 hours long.
    now_utc = dt_util.as_utc(now)
    start = dt_util.as_utc(dt_util.start_of_local_day(now))
    end = dt_util.as_utc(dt_util.start_of_local_day(dt_util.as_local(now).date() + timedelta(days=1)))
    return max(0.0, min(1.0, (end - now_utc) / (end - start)))


def source_value(hass: HomeAssistant, entity_id: str | None, today: date | None) -> float | None:
    """A source sensor's number, or None. With `today`, a `date` attribute must be today."""
    if not entity_id or (state := hass.states.get(entity_id)) is None:
        return None
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    if today is not None:
        # Yesterday's total before the source's first update after midnight.
        if "date" in state.attributes:  # e.g. Samsung Health says which day it is
            if str(state.attributes["date"]) != today.isoformat():
                return None
        elif state.last_changed < dt_util.start_of_local_day(today):  # any other source
            return None
    try:
        return float(state.state)
    except ValueError:
        return None


def budget_left(burn: float, bmr: float, fraction: float, deficit: float, intake: float) -> float:
    return burn + bmr * fraction - deficit - intake


@dataclass
class Today:
    now: datetime
    day: date
    totals: DayTotals
    week_start: date
    week_alcohol: float
    burn: float | None
    bmr: float | None
    weight: float | None
    goals: Callable[[str], float | None]
    entries: list[FoodEntry] = field(default_factory=list)


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _net(t: Today) -> float | None:
    return None if t.burn is None else t.totals.kcal - t.burn


def _projected_burn(t: Today) -> float | None:
    if t.burn is None or t.bmr is None:
        return None
    return t.burn + t.bmr * remaining_day_fraction(t.now)


def _budget(t: Today) -> float | None:
    if t.burn is None or t.bmr is None or (deficit := t.goals("deficit_target")) is None:
        return None
    return budget_left(t.burn, t.bmr, remaining_day_fraction(t.now), deficit, t.totals.kcal)


def _protein_left(t: Today) -> float | None:
    floor = t.goals("protein_floor")
    return None if floor is None else max(0.0, floor - t.totals.protein_g)


def _to_target(t: Today) -> float | None:
    target = t.goals("target_weight")
    return None if target is None or t.weight is None else t.weight - target


def _intake_attrs(t: Today) -> dict[str, Any]:
    """What today's intake is made of; recorded with the state (a few kB a day)."""
    return {
        "by_category": {c: round(v, 1) for c, v in t.totals.kcal_by_category.items()},
        "entries": [
            {"time": e.time.strftime("%H:%M") if e.time else None, "category": e.category,
             "description": e.description, "kcal": round(e.kcal, 1)}
            for e in t.entries
        ],
    }


def _week_attrs(t: Today) -> dict[str, Any]:
    attrs: dict[str, Any] = {"week_start": t.week_start.isoformat()}
    if (limit := t.goals("alcohol_limit_week")) is not None:
        attrs["limit"] = limit
    return attrs


@dataclass(frozen=True, slots=True)
class Spec:
    key: str
    unit: str
    value: Callable[[Today], float | None]
    attrs: Callable[[Today], dict[str, Any]] = field(default=lambda t: {})
    digits: int = 1
    #: Daily/weekly total: the day its count started (state_class total, last_reset).
    reset: Callable[[Today], date] | None = None


def _day(t: Today) -> date:
    return t.day


SENSORS = (
    Spec("intake_today", "kcal", lambda t: t.totals.kcal, _intake_attrs, digits=0, reset=_day),
    Spec("protein_today", "g", lambda t: t.totals.protein_g, reset=_day),
    Spec("sat_fat_today", "g", lambda t: t.totals.sat_fat_g, reset=_day),
    Spec("alcohol_today", "units", lambda t: t.totals.alcohol_units, reset=_day),
    Spec("alcohol_week", "units", lambda t: t.week_alcohol, _week_attrs, reset=lambda t: t.week_start),
    Spec("net_today", "kcal", _net, digits=0),
    Spec("budget_left_today", "kcal", _budget,
         lambda t: {"forecast": True, "projected_burn": _r(_projected_burn(t), 0)}, digits=0),
    Spec("protein_left_today", "g", _protein_left),
    Spec("to_target_weight", "kg", _to_target, digits=2),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: NutritionConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(NutritionSensor(entry, spec) for spec in SENSORS)


class NutritionSensor(NutritionEntity, SensorEntity):
    def __init__(self, entry: NutritionConfigEntry, spec: Spec) -> None:
        super().__init__(entry, spec.key)
        self.spec = spec
        self._attr_native_unit_of_measurement = spec.unit
        if spec.reset is not None:
            # A total that starts over (at midnight, or Monday for the week), like a
            # "rain today" sensor: the recorder keeps its statistics on this sensor.
            self._attr_state_class = SensorStateClass.TOTAL
        self._today: Today | None = None

    @property
    def last_reset(self) -> datetime | None:
        if self.spec.reset is None or self._today is None:
            return None
        return dt_util.start_of_local_day(self.spec.reset(self._today))

    def _compute(self) -> Today:
        now = dt_util.now()
        today = now.date()
        store = self.entry.runtime_data.store
        week_start = today - timedelta(days=today.weekday())
        options = self.entry.options
        return Today(
            now=now,
            day=today,
            totals=day_totals(today, entries := store.entries_on(today)),
            week_start=week_start,
            week_alcohol=sum(e.alcohol_units or 0.0 for e in store.entries_between(week_start, today)),
            burn=source_value(self.hass, options.get(CONF_BURN_ENTITY), today),
            bmr=source_value(self.hass, options.get(CONF_BMR_ENTITY), None),
            weight=source_value(self.hass, options.get(CONF_WEIGHT_ENTITY), None),
            goals=lambda key: goal(self.entry, key),
            entries=entries,
        )

    @callback
    def _refresh(self, *_: Any) -> None:
        self._today = self._compute()
        value = self.spec.value(self._today)
        self._attr_available = value is not None
        self._attr_native_value = _r(value, self.spec.digits)
        self._attr_extra_state_attributes = {"date": self._today.day.isoformat(), **self.spec.attrs(self._today)}
        self.async_write_ha_state()

    @callback
    def _source_changed(self, event: Event[EventStateChangedData]) -> None:
        self._refresh()

    async def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Renamed in place (HA 2026.11+) instead of removed and re-added. Nothing
        here depends on this entity's own id, so there is nothing to update."""
        if (parent := getattr(super(), "async_entity_id_changed", None)) is not None:
            await parent(old_entity_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self._refresh))
        self.async_on_remove(async_track_time_change(self.hass, self._refresh, hour=0, minute=0, second=0))
        self.async_on_remove(async_track_time_interval(self.hass, self._refresh, REFRESH))
        sources = [e for k in (CONF_BURN_ENTITY, CONF_BMR_ENTITY, CONF_WEIGHT_ENTITY)
                   if (e := self.entry.options.get(k))]
        if sources:
            self.async_on_remove(async_track_state_change_event(self.hass, sources, self._source_changed))
        self._refresh()

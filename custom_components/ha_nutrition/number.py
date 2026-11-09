"""Goals as number entities: changeable from dashboards, automations and the set_goals tool.

0 means "not set" for every goal (a number entity can't hold "no value"); sensors
that depend on an unset goal are unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SIGNAL_UPDATED
from .entity import NutritionEntity

if TYPE_CHECKING:
    from . import NutritionConfigEntry


@dataclass(frozen=True, slots=True)
class GoalSpec:
    key: str
    unit: str
    max: float
    step: float


GOALS: dict[str, GoalSpec] = {
    g.key: g
    for g in (
        GoalSpec("deficit_target", "kcal/d", 2000, 10),
        GoalSpec("protein_floor", "g", 400, 1),
        GoalSpec("protein_ceiling", "g", 400, 1),
        GoalSpec("target_weight", "kg", 300, 0.1),
        GoalSpec("alcohol_limit_week", "units", 100, 0.5),
    )
}


def goal(entry: NutritionConfigEntry, key: str) -> float | None:
    """The goal's value, or None when it isn't set (0)."""
    return entry.runtime_data.goals.get(key) or None


def goal_entity_ids(hass: HomeAssistant, entry: NutritionConfigEntry) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        key: entity_id
        for key in GOALS
        if (entity_id := registry.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_{key}"))
    }


async def async_setup_entry(
    hass: HomeAssistant, entry: NutritionConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(GoalNumber(entry, spec) for spec in GOALS.values())


class GoalNumber(NutritionEntity, RestoreNumber):
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0

    def __init__(self, entry: NutritionConfigEntry, spec: GoalSpec) -> None:
        super().__init__(entry, spec.key)
        self.spec = spec
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_native_max_value = spec.max
        self._attr_native_step = spec.step
        self._attr_native_value = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None and last.native_value is not None:
            self._attr_native_value = float(last.native_value)
        self.entry.runtime_data.goals[self.spec.key] = self._attr_native_value
        # The sensor platform is set up concurrently and may already have computed
        # without this goal; tell it the goal is here.
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = float(value)
        self.entry.runtime_data.goals[self.spec.key] = self._attr_native_value
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

"""The plan's start date: from it, "deficit since start" and the expected weight are counted."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from homeassistant.components.date import DateEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SIGNAL_PLAN_START, SIGNAL_UPDATED
from .entity import NutritionEntity

if TYPE_CHECKING:
    from . import NutritionConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: NutritionConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PlanStartDate(entry)])


class PlanStartDate(NutritionEntity, DateEntity, RestoreEntity):
    def __init__(self, entry: NutritionConfigEntry) -> None:
        super().__init__(entry, "plan_start_date")
        self._attr_native_value = None

    async def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Renamed in place (HA 2026.11+); nothing here depends on this entity's own id."""
        if (parent := getattr(super(), "async_entity_id_changed", None)) is not None:
            await parent(old_entity_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = date.fromisoformat(last.state)
            except ValueError:
                self._attr_native_value = None
        self.entry.runtime_data.plan_start = self._attr_native_value
        # set_goals can also clear the date, which the date.set_value action can't.
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_PLAN_START, self._async_set))
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    @callback
    def _async_set(self, value: date | None) -> None:
        self._attr_native_value = value
        self.entry.runtime_data.plan_start = value
        self.async_write_ha_state()
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    async def async_set_value(self, value: date) -> None:
        self._async_set(value)

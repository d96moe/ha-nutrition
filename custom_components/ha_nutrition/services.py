"""Home Assistant services: the same operations as the MCP tools, for automations."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NUTRIENT_FIELDS, SOURCE_SERVICE
from .models import InvalidInput, parse_date

if TYPE_CHECKING:
    from . import NutritionConfigEntry
    from .store import NutritionStore

_NUMBER_OR_NONE = vol.Any(None, vol.Coerce(float))

LOG_FOOD_SCHEMA = vol.Schema({
    vol.Optional("date"): cv.string,
    vol.Required("entries"): vol.All(cv.ensure_list, [dict]),
    vol.Optional("request_id"): cv.string,
})
UPDATE_ENTRY_SCHEMA = vol.Schema({
    vol.Required("entry_id"): cv.string,
    vol.Optional("date"): cv.string,
    vol.Optional("time"): vol.Any(None, cv.string),
    vol.Optional("category"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("kcal"): vol.Coerce(float),
    **{vol.Optional(f): _NUMBER_OR_NONE for f in NUTRIENT_FIELDS},
})
DELETE_ENTRY_SCHEMA = vol.Schema({vol.Required("entry_id"): cv.string})
SET_DAY_NOTE_SCHEMA = vol.Schema({vol.Required("date"): cv.string, vol.Optional("note"): vol.Any(None, cv.string)})
IMPORT_SCHEMA = vol.Schema({vol.Required("data"): dict, vol.Optional("replace", default=False): cv.boolean})


def loaded_entry(hass: HomeAssistant) -> NutritionConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Nutrition is not set up (or not loaded)")
    return entries[0]


def _date(value: str | None, name: str = "date"):
    return parse_date(value, name, dt_util.now().date())


def async_register_services(hass: HomeAssistant) -> None:
    def register(name: str, schema: vol.Schema, run: Callable[[NutritionStore, dict], Awaitable[Any]],
                 response: SupportsResponse = SupportsResponse.OPTIONAL) -> None:
        async def handler(call: ServiceCall) -> ServiceResponse:
            store = loaded_entry(hass).runtime_data.store
            try:
                return await run(store, dict(call.data))
            except InvalidInput as exc:
                raise ServiceValidationError(str(exc)) from exc

        hass.services.async_register(DOMAIN, name, handler, schema=schema, supports_response=response)

    async def log_food(store: NutritionStore, data: dict) -> dict:
        return await store.async_log_entries(_date(data.get("date")), data["entries"],
                                             source=SOURCE_SERVICE, request_id=data.get("request_id"))

    async def update_entry(store: NutritionStore, data: dict) -> dict:
        entry_id = data.pop("entry_id")
        return await store.async_update_entry(entry_id, data, source=SOURCE_SERVICE)

    async def delete_entry(store: NutritionStore, data: dict) -> dict:
        return await store.async_delete_entry(data["entry_id"])

    async def set_day_note(store: NutritionStore, data: dict) -> dict:
        day = _date(data["date"])
        if day is None:
            raise InvalidInput("date is required")
        return await store.async_set_day_note(day, data.get("note"))

    async def export(store: NutritionStore, data: dict) -> dict:
        return store.export()

    async def import_log(store: NutritionStore, data: dict) -> dict:
        return await store.async_import(data["data"], replace=data["replace"])

    register("log_food", LOG_FOOD_SCHEMA, log_food)
    register("update_entry", UPDATE_ENTRY_SCHEMA, update_entry)
    register("delete_entry", DELETE_ENTRY_SCHEMA, delete_entry)
    register("set_day_note", SET_DAY_NOTE_SCHEMA, set_day_note)
    register("export", vol.Schema({}), export, SupportsResponse.ONLY)
    register("import", IMPORT_SCHEMA, import_log)

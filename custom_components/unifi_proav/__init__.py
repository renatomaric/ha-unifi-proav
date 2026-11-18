"""UniFi Pro AV: choose which source each EAV-Bridge TV shows, from Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_extract_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_SOURCE, DOMAIN, SERVICE_ROUTE
from .coordinator import ProAVConfigEntry, ProAVCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ROUTE_SCHEMA = vol.Schema({vol.Required(ATTR_SOURCE): cv.string}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def route(call: ServiceCall) -> None:
        """Send several TVs to one source in a single pass. No target means every TV."""
        registry = er.async_get(hass)
        wanted = await async_extract_entity_ids(call)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            prefix = f"{entry.entry_id}_"
            macs = [
                ent.unique_id.removeprefix(prefix)
                for ent in er.async_entries_for_config_entry(registry, entry.entry_id)
                if ent.domain == Platform.SELECT
                and ent.unique_id.startswith(prefix)
                and (not wanted or ent.entity_id in wanted)
            ]
            if macs:
                await entry.runtime_data.async_route(macs, call.data[ATTR_SOURCE])

    hass.services.async_register(DOMAIN, SERVICE_ROUTE, route, schema=ROUTE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ProAVConfigEntry) -> bool:
    coordinator = ProAVCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProAVConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import OPTION_OFF
from .coordinator import ProAVConfigEntry, ProAVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProAVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_receivers() -> None:
        new = [mac for mac in coordinator.data.clients if mac not in known]
        known.update(new)
        async_add_entities(ProAVSourceSelect(coordinator, mac) for mac in new)

    add_new_receivers()
    entry.async_on_unload(coordinator.async_add_listener(add_new_receivers))


class ProAVSourceSelect(CoordinatorEntity[ProAVCoordinator], SelectEntity):
    """Which source one TV (an EAV-Bridge acting as receiver) is showing."""

    _attr_has_entity_name = True
    _attr_translation_key = "source"

    def __init__(self, coordinator: ProAVCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{mac}"
        # Attach to the bridge device the UniFi Network integration already created, if any.
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))},
            name=coordinator.data.label(mac),
            manufacturer="Ubiquiti",
            model="EAV-Bridge",
        )

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.clients

    @property
    def options(self) -> list[str]:
        return [OPTION_OFF, *self.coordinator.data.source_labels()]

    @property
    def current_option(self) -> str | None:
        return self.coordinator.data.current_label(self._mac)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_route([self._mac], option)

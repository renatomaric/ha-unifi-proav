from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import ProAVConfigEntry, ProAVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProAVConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_sources() -> None:
        data = coordinator.data
        new = [mac for mac in data.hosts if mac not in known and mac in data.hdmi_inputs]
        known.update(new)
        async_add_entities(ProAVHdmiInput(coordinator, mac) for mac in new)

    add_new_sources()
    entry.async_on_unload(coordinator.async_add_listener(add_new_sources))


class ProAVHdmiInput(CoordinatorEntity[ProAVCoordinator], BinarySensorEntity):
    """On while a device is plugged into the HDMI input of a source bridge."""

    _attr_has_entity_name = True
    _attr_translation_key = "hdmi_input"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: ProAVCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{mac}_hdmi_input"
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))},
            name=coordinator.data.label(mac),
            manufacturer="Ubiquiti",
            model="EAV-Bridge",
        )

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.hdmi_inputs

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.hdmi_inputs[self._mac]

"""Polls the video matrix and applies routing changes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ProAVAuthError, ProAVClient, ProAVError, parse_hdmi_inputs, parse_names
from .const import CONF_SITE, DEFAULT_SITE, DOMAIN, OPTION_OFF, SCAN_INTERVAL_SECONDS
from .planner import plan_route, source_of

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProAVState:
    groups: list[dict] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    hdmi_inputs: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_api(cls, matrix: dict, devices: list[dict]) -> "ProAVState":
        return cls(
            groups=matrix.get("groups", []),
            hosts=[m.lower() for m in matrix.get("hosts", [])],
            clients=[m.lower() for m in matrix.get("clients", [])],
            names=parse_names(devices),
            hdmi_inputs=parse_hdmi_inputs(devices),
        )

    def label(self, mac: str) -> str:
        return self.names.get(mac.lower(), mac)

    def source_labels(self) -> dict[str, str]:
        """Option label to host MAC. Labels are made unique if two devices share a name."""
        labels: dict[str, str] = {}
        for mac in self.hosts:
            label = self.label(mac)
            if label in labels or label == OPTION_OFF:
                label = f"{label} ({mac[-5:]})"
            labels[label] = mac.lower()
        return labels

    def current_label(self, client_mac: str) -> str:
        host = source_of(self.groups, client_mac)
        if host is None:
            return OPTION_OFF
        for label, mac in self.source_labels().items():
            if mac == host:
                return label
        return OPTION_OFF


type ProAVConfigEntry = ConfigEntry[ProAVCoordinator]


class ProAVCoordinator(DataUpdateCoordinator[ProAVState]):
    config_entry: ProAVConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ProAVConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        # A dedicated session: the controller's login cookie must not be shared with other
        # integrations, and the console uses a self-signed certificate.
        session = async_create_clientsession(hass, verify_ssl=False)
        self.client = ProAVClient(
            session,
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_SITE, DEFAULT_SITE),
        )
        self._write_lock = asyncio.Lock()

    async def _async_update_data(self) -> ProAVState:
        try:
            matrix = await self.client.get_matrix()
            devices = await self.client.get_devices()
        except ProAVAuthError as err:
            # Stops polling, so a wrong password cannot lock the account by retrying.
            raise ConfigEntryAuthFailed(str(err)) from err
        except ProAVError as err:
            raise UpdateFailed(str(err)) from err
        return ProAVState.from_api(matrix, devices)

    async def async_route(self, client_macs: list[str], source_label: str) -> None:
        """Make the given receivers show one source, or nothing for ``Off``."""
        async with self._write_lock:
            try:
                # Plan from fresh state, not the last poll, so a change made elsewhere is not undone.
                state = ProAVState.from_api(
                    await self.client.get_matrix(), await self.client.get_devices()
                )
                if source_label == OPTION_OFF:
                    target = None
                else:
                    target = state.source_labels().get(source_label)
                    if target is None:
                        raise HomeAssistantError(f"Unknown source: {source_label}")
                ops = plan_route(state.groups, client_macs, target, state.names)
                _LOGGER.debug("Routing %s to %s with %d operations", client_macs, source_label, len(ops))
                await self.client.apply(ops)
            except ProAVAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except ProAVError as err:
                raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()

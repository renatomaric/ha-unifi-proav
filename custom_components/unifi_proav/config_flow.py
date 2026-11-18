from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import ProAVAuthError, ProAVClient, ProAVError
from .const import CONF_SITE, DEFAULT_SITE, DOMAIN

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_SITE, default=DEFAULT_SITE): str,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class ProAVConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check(self, data: Mapping[str, Any]) -> str | None:
        """Try one login and a matrix read. Returns an error key or None."""
        client = ProAVClient(
            async_create_clientsession(self.hass, verify_ssl=False),
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            data.get(CONF_SITE, DEFAULT_SITE),
        )
        try:
            await client.get_matrix()
        except ProAVAuthError as err:
            if err.code == "AUTHENTICATION_FAILED_ACCOUNT_LOCKED":
                return "account_locked"
            return "invalid_auth"
        except ProAVError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}/{user_input[CONF_SITE]}")
            self._abort_if_unique_id_configured()
            if error := await self._check(user_input):
                errors["base"] = error
            else:
                return self.async_create_entry(title="UniFi Pro AV", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            if error := await self._check(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

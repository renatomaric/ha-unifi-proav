"""Config flow, entity and service tests with a fake controller client."""

import copy
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.unifi_proav.client import ProAVAuthError
from custom_components.unifi_proav.const import CONF_SITE, DOMAIN

DATA = {
    CONF_HOST: "192.168.0.1",
    CONF_USERNAME: "ha",
    CONF_PASSWORD: "secret",
    CONF_SITE: "default",
}

HT = "aa:00:00:00:00:01"
RPI = "aa:00:00:00:00:02"
BINA = "aa:00:00:00:00:03"
TVS = [f"aa:00:00:00:00:1{i}" for i in range(1, 6)]
NAMES = {
    HT: "Ormar HT",
    RPI: "Ormar rpi",
    BINA: "Podrum bina",
    TVS[0]: "Kat - ulaz",
    TVS[1]: "Podrum Veliki TV",
    TVS[2]: "Prizemlje - klavir",
    TVS[3]: "Prizemlje - pec",
    TVS[4]: "Podrum Bina TV",
}


def receiver(mac):
    return {
        "mac": mac,
        "client_type": "VIDEO",
        "mode": "UNICAST",
        "resolution": "AUTO",
        "color_format": "RGB",
        "audio_enabled": True,
        "audio_mode": "PCM",
        "enabled": True,
    }


def initial_matrix():
    return {
        "bridges": [HT, RPI, BINA, *TVS],
        "hosts": [HT, RPI, BINA],
        "clients": list(TVS),
        "groups": [
            {
                "id": "g1",
                "channel": 2,
                "name": "HOME.TV",
                "type": "VIDEO",
                "enabled": True,
                "host": {"mac": HT, "host_type": "VIDEO", "refresh_rate": "AUTO", "enabled": True},
                "clients": [receiver(m) for m in TVS],
            }
        ],
    }


class FakeClient:
    instances: list["FakeClient"] = []
    matrix = initial_matrix()
    login_error: Exception | None = None
    applied: list = []

    def __init__(self, session, host, username, password, site="default"):
        FakeClient.instances.append(self)

    async def get_matrix(self):
        if FakeClient.login_error:
            raise FakeClient.login_error
        return copy.deepcopy(FakeClient.matrix)

    async def get_names(self):
        return dict(NAMES)

    async def apply(self, ops):
        FakeClient.applied.extend(ops)
        groups = FakeClient.matrix["groups"]
        for op in ops:
            if op.method == "DELETE":
                groups[:] = [g for g in groups if g["id"] != op.group_id]
            elif op.method == "PUT":
                groups[:] = [{**op.body, "id": op.group_id} if g["id"] == op.group_id else g for g in groups]
            elif op.method == "POST":
                groups.append({**op.body, "id": f"new{len(groups)}", "channel": 9})


@pytest.fixture(autouse=True)
def fake_client():
    FakeClient.instances = []
    FakeClient.matrix = initial_matrix()
    FakeClient.login_error = None
    FakeClient.applied = []
    with (
        patch("custom_components.unifi_proav.coordinator.ProAVClient", FakeClient),
        patch("custom_components.unifi_proav.config_flow.ProAVClient", FakeClient),
    ):
        yield FakeClient


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="192.168.0.1/default")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def select_states(hass: HomeAssistant) -> dict[str, str]:
    return {s.entity_id: s.state for s in hass.states.async_all("select")}


async def test_each_tv_gets_a_select_with_its_current_source(hass: HomeAssistant):
    await setup_entry(hass)

    states = select_states(hass)

    assert len(states) == 5
    assert set(states.values()) == {"Ormar HT"}
    state = hass.states.get("select.kat_ulaz_source")
    assert state.attributes["options"] == ["Off", "Ormar HT", "Ormar rpi", "Podrum bina"]


async def test_choosing_a_source_moves_only_that_tv(hass: HomeAssistant, fake_client):
    await setup_entry(hass)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.podrum_veliki_tv_source", "option": "Ormar rpi"},
        blocking=True,
    )
    await hass.async_block_till_done()

    states = select_states(hass)
    assert states["select.podrum_veliki_tv_source"] == "Ormar rpi"
    assert states["select.kat_ulaz_source"] == "Ormar HT"
    assert [(o.method, o.group_id) for o in fake_client.applied] == [("PUT", "g1"), ("POST", None)]


async def test_off_removes_the_tv_from_its_group(hass: HomeAssistant):
    await setup_entry(hass)

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.kat_ulaz_source", "option": "Off"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get("select.kat_ulaz_source").state == "Off"
    assert hass.states.get("select.podrum_bina_tv_source").state == "Ormar HT"


async def test_route_service_without_a_target_moves_every_tv_in_one_write(
    hass: HomeAssistant, fake_client
):
    await setup_entry(hass)

    await hass.services.async_call(DOMAIN, "route", {"source": "Podrum bina"}, blocking=True)
    await hass.async_block_till_done()

    assert set(select_states(hass).values()) == {"Podrum bina"}
    assert [(o.method, o.group_id) for o in fake_client.applied] == [("PUT", "g1")]


async def test_route_service_with_targets_moves_only_those_tvs(hass: HomeAssistant):
    await setup_entry(hass)

    await hass.services.async_call(
        DOMAIN,
        "route",
        {
            "source": "Ormar rpi",
            "entity_id": ["select.kat_ulaz_source", "select.prizemlje_klavir_source"],
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    states = select_states(hass)
    assert states["select.kat_ulaz_source"] == "Ormar rpi"
    assert states["select.prizemlje_klavir_source"] == "Ormar rpi"
    assert states["select.podrum_veliki_tv_source"] == "Ormar HT"


async def test_unknown_source_is_rejected_and_nothing_is_written(hass: HomeAssistant, fake_client):
    await setup_entry(hass)

    with pytest.raises(HomeAssistantError, match="Unknown source"):
        await hass.services.async_call(DOMAIN, "route", {"source": "Nowhere"}, blocking=True)

    assert fake_client.applied == []


async def test_a_refused_login_at_startup_starts_reauth_and_stops_polling(hass: HomeAssistant):
    FakeClient.login_error = ProAVAuthError("no", "AUTHENTICATION_FAILED_ACCOUNT_LOCKED")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="192.168.0.1/default")
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_config_flow_creates_an_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (ProAVAuthError("no", "AUTHENTICATION_FAILED_ACCOUNT_LOCKED"), "account_locked"),
        (ProAVAuthError("no", None), "invalid_auth"),
    ],
)
async def test_config_flow_reports_login_problems(hass: HomeAssistant, error, key):
    FakeClient.login_error = error

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

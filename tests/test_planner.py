"""Tests for the routing planner. Uses made-up MACs shaped like a real installation."""

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "unifi_proav_planner", Path(__file__).parents[1] / "custom_components/unifi_proav/planner.py"
)
planner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = planner
spec.loader.exec_module(planner)

Op = planner.Op
plan_route = planner.plan_route
source_of = planner.source_of

HT = "aa:00:00:00:00:01"
RPI = "aa:00:00:00:00:02"
BINA = "aa:00:00:00:00:03"
TV1, TV2, TV3, TV4, TV5 = (f"aa:00:00:00:00:1{i}" for i in range(1, 6))
NAMES = {HT: "Ormar HT", RPI: "Ormar rpi", BINA: "Podrum bina"}


def client(mac: str, **overrides) -> dict:
    base = planner.default_client(mac)
    base.update(overrides)
    return base


def group(gid: str, host: str, macs: list[str], name: str = "HOME.TV") -> dict:
    return {
        "id": gid,
        "channel": 2,
        "name": name,
        "type": "VIDEO",
        "enabled": True,
        "host": {"mac": host, "host_type": "VIDEO", "refresh_rate": "AUTO", "enabled": True},
        "clients": [client(m) for m in macs],
    }


def all_on_ht() -> list[dict]:
    return [group("g1", HT, [TV1, TV2, TV3, TV4, TV5])]


def test_source_of_finds_the_host_a_tv_shows():
    assert source_of(all_on_ht(), TV3) == HT
    assert source_of(all_on_ht(), "aa:00:00:00:00:99") is None


def test_moving_one_tv_to_a_source_without_a_group_creates_it():
    ops = plan_route(all_on_ht(), [TV2], RPI, NAMES)

    assert [o.method for o in ops] == ["PUT", "POST"]
    shrunk, created = ops
    assert shrunk.group_id == "g1"
    assert [c["mac"] for c in shrunk.body["clients"]] == [TV1, TV3, TV4, TV5]
    assert created.group_id is None
    assert created.body["name"] == "Ormar rpi"
    assert created.body["host"]["mac"] == RPI
    assert [c["mac"] for c in created.body["clients"]] == [TV2]


def test_moving_a_tv_into_an_existing_group_edits_both_groups():
    groups = [group("g1", HT, [TV1, TV2]), group("g2", RPI, [TV3], name="RPI")]

    ops = plan_route(groups, [TV2], RPI, NAMES)

    assert [(o.method, o.group_id) for o in ops] == [("PUT", "g1"), ("PUT", "g2")]
    assert [c["mac"] for c in ops[1].body["clients"]] == [TV3, TV2]


def test_moving_the_last_tv_out_of_a_group_deletes_it():
    groups = [group("g1", HT, [TV1]), group("g2", RPI, [TV2], name="RPI")]

    ops = plan_route(groups, [TV1], RPI, NAMES)

    assert [(o.method, o.group_id) for o in ops] == [("DELETE", "g1"), ("PUT", "g2")]


def test_moving_everything_deletes_the_old_group_and_creates_a_new_one():
    # The controller assigns channels itself and refuses to re-point a group to another host
    # ("AV Group channel already exists"), so an emptied group is never reused.
    ops = plan_route(all_on_ht(), [TV1, TV2, TV3, TV4, TV5], RPI, NAMES)

    assert [(o.method, o.group_id) for o in ops] == [("DELETE", "g1"), ("POST", None)]
    body = ops[1].body
    assert body["host"]["mac"] == RPI
    assert body["name"] == "Ormar rpi"
    assert "channel" not in body and "id" not in body
    assert [c["mac"] for c in body["clients"]] == [TV1, TV2, TV3, TV4, TV5]


def test_a_tv_keeps_its_own_settings_when_it_moves():
    groups = [
        {
            **group("g1", HT, [TV1, TV2]),
            "clients": [client(TV1), client(TV2, resolution="1080P", audio_enabled=False)],
        }
    ]

    ops = plan_route(groups, [TV2], RPI, NAMES)

    moved = ops[-1].body["clients"][0]
    assert moved["resolution"] == "1080P"
    assert moved["audio_enabled"] is False


def test_switching_off_removes_the_tv_from_every_group():
    ops = plan_route(all_on_ht(), [TV4], None, NAMES)

    assert [(o.method, o.group_id) for o in ops] == [("PUT", "g1")]
    assert TV4 not in [c["mac"] for c in ops[0].body["clients"]]


def test_switching_off_the_last_tv_deletes_the_group():
    groups = [group("g1", HT, [TV1])]

    assert plan_route(groups, [TV1], None, NAMES) == [Op("DELETE", "g1", None)]


def test_a_tv_with_no_route_yet_gets_a_default_receiver_config():
    ops = plan_route([], [TV1], HT, NAMES)

    assert [o.method for o in ops] == ["POST"]
    assert ops[0].body["clients"] == [planner.default_client(TV1)]


def test_asking_for_the_current_source_changes_nothing():
    assert plan_route(all_on_ht(), [TV2], HT, NAMES) == []
    assert plan_route(all_on_ht(), [TV1, TV2, TV3, TV4, TV5], HT, NAMES) == []


def test_switching_off_a_tv_that_has_no_route_changes_nothing():
    assert plan_route([group("g1", HT, [TV1])], [TV2], None, NAMES) == []


def test_mac_case_does_not_matter():
    ops = plan_route(all_on_ht(), [TV2.upper()], RPI.upper(), NAMES)

    assert ops[-1].body["host"]["mac"] == RPI


def test_the_input_groups_are_not_mutated():
    groups = all_on_ht()
    before = [dict(g) for g in groups]

    plan_route(groups, [TV1], RPI, NAMES)

    assert [c["mac"] for c in groups[0]["clients"]] == [TV1, TV2, TV3, TV4, TV5]
    assert groups[0]["host"]["mac"] == before[0]["host"]["mac"]

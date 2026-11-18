"""Pure logic that turns "these TVs should show this source" into controller API operations.

No Home Assistant or network code lives here so it can be tested on its own.

A group is one source (``host``) feeding a list of receivers (``clients``). A TV shows a
source because its receiver MAC is in that source's group. Routing therefore means moving
receiver MACs between groups.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

Group = dict[str, Any]


@dataclass(frozen=True)
class Op:
    """One write call against ``proav/video/group``."""

    method: str  # POST, PUT or DELETE
    group_id: str | None
    body: dict[str, Any] | None


def default_client(mac: str) -> dict[str, Any]:
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


def new_group(name: str, host_mac: str, clients: list[dict[str, Any]]) -> Group:
    return {
        "name": name,
        "type": "VIDEO",
        "host": {
            "mac": host_mac,
            "host_type": "VIDEO",
            "refresh_rate": "AUTO",
            "enabled": True,
        },
        "clients": clients,
    }


def source_of(groups: list[Group], client_mac: str) -> str | None:
    """Return the host MAC a receiver currently shows, or None."""
    mac = client_mac.lower()
    for group in groups:
        if any(c["mac"].lower() == mac for c in group["clients"]):
            return group["host"]["mac"].lower()
    return None


def plan_route(
    groups: list[Group],
    client_macs: list[str],
    target_host_mac: str | None,
    host_names: dict[str, str] | None = None,
) -> list[Op]:
    """Plan the writes that make ``client_macs`` show ``target_host_mac`` (None = show nothing).

    Receivers keep their own settings when they move. Groups that end up empty are deleted,
    except that an emptied group is re-pointed at the target host when the target has no
    group yet, which is one write and keeps the channel.
    """
    moving = {m.lower() for m in client_macs}
    target = target_host_mac.lower() if target_host_mac else None
    names = {k.lower(): v for k, v in (host_names or {}).items()}

    original = {g["id"]: g for g in groups}
    work = {gid: copy.deepcopy(g) for gid, g in original.items()}

    carried: dict[str, dict[str, Any]] = {}
    for group in work.values():
        for client in group["clients"]:
            if client["mac"].lower() in moving:
                carried[client["mac"].lower()] = client
    for mac in client_macs:
        carried.setdefault(mac.lower(), default_client(mac.lower()))

    for group in work.values():
        group["clients"] = [c for c in group["clients"] if c["mac"].lower() not in moving]

    emptied = [
        gid for gid, g in work.items() if not g["clients"] and original[gid]["clients"]
    ]
    changed = [
        gid
        for gid, g in work.items()
        if g["clients"] and len(g["clients"]) != len(original[gid]["clients"])
    ]

    ordered_carried = [carried[m.lower()] for m in client_macs]
    target_group_id = None
    if target is not None:
        target_group_id = next(
            (gid for gid, g in work.items() if g["host"]["mac"].lower() == target and g["clients"]),
            None,
        )
        if target_group_id is None:
            # A group of the target host may exist but be empty after removals.
            target_group_id = next(
                (gid for gid, g in work.items() if g["host"]["mac"].lower() == target), None
            )

    # The target's own group is never deleted, it is refilled below.
    emptied = [gid for gid in emptied if gid != target_group_id]

    ops: list[Op] = []
    reuse_id: str | None = None

    if target is not None and target_group_id is None and emptied:
        reuse_id = emptied[0]
        group = work[reuse_id]
        group["host"]["mac"] = target
        group["clients"] = ordered_carried
        if group["name"] == _host_name(original[reuse_id], names) or group["name"] == "":
            group["name"] = names.get(target, group["name"])
        emptied = emptied[1:]

    for gid in emptied:
        ops.append(Op("DELETE", gid, None))
    for gid in changed:
        if gid != target_group_id:
            ops.append(Op("PUT", gid, work[gid]))

    if reuse_id is not None:
        ops.append(Op("PUT", reuse_id, work[reuse_id]))
    elif target is not None and target_group_id is not None:
        group = work[target_group_id]
        present = {c["mac"].lower() for c in group["clients"]}
        group["clients"] += [c for c in ordered_carried if c["mac"].lower() not in present]
        if _by_mac(group["clients"]) != _by_mac(original[target_group_id]["clients"]):
            ops.append(Op("PUT", target_group_id, group))
    elif target is not None:
        ops.append(Op("POST", None, new_group(names.get(target, target), target, ordered_carried)))

    return ops


def _by_mac(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(clients, key=lambda c: c["mac"].lower())


def _host_name(group: Group, names: dict[str, str]) -> str:
    return names.get(group["host"]["mac"].lower(), "")

# UniFi Pro AV for Home Assistant

Choose which source each EAV-Bridge TV shows, from Home Assistant.

It uses the internal video matrix endpoint of the UniFi Network application (`/proxy/network/v2/api/site/<site>/proav/video`), found by capturing the controller's own web UI. It is not an official API, so Ubiquiti can change it without notice. Credit for the discovery: the r/Ubiquiti post "EAV-Pro API to Switch groups programmatically".

## What you get

- `select.<tv>_source`: one per receiver. Options are `Off` plus every source. Picking one moves that TV.
- `unifi_proav.route`: send several TVs to one source in one pass. No target means every TV.

```yaml
action: unifi_proav.route
target:
  entity_id:
    - select.kat_kat_ulaz_source
    - select.prizemlje_prizemlje_klavir_source
data:
  source: HOME.tv
```

Source and TV names come from the device names in the UniFi Network app. The entities attach to the bridge devices the UniFi Network integration already created.

A TV that is moved keeps its own receiver settings. A group that becomes empty is deleted and a missing group for the new source is created. The controller assigns channels itself, so groups are never re-pointed to another source.

## Install

1. In UniFi OS, create a local account for Home Assistant (Admins & Users, local access only). A ui.com account does not work.
2. In HACS: Custom repositories, add this repository as an Integration, then download "UniFi Pro AV".
3. Restart Home Assistant, then Settings > Devices & services > Add integration > UniFi Pro AV.

If the controller refuses the login, polling stops and Home Assistant asks for the password again. It never retries a refused login, because repeated failures lock the account.

## Dashboard

`dashboards/tv.yaml` is a ready view with a button per source (all TVs) and a source picker per TV. Adjust the entity ids if your bridge names differ.

## Tests

```bash
uv run --python 3.13 --with aiohttp --with pytest --with pytest-asyncio --no-project pytest tests/test_planner.py tests/test_client.py
uv run --python 3.14 --with pytest-homeassistant-custom-component --no-project pytest tests/ha
```

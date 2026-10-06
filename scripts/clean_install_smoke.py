"""Clean-install smoke test: shipped integration only, fresh HA config, no network.

Stages exactly what HACS installs for this repository (``hacs.json`` with
``content_in_root: false`` means the single ``custom_components/<domain>``
folder) into an empty temporary Home Assistant configuration directory. A
child interpreter started with ``-I`` (so the repository is not importable)
then boots an in-process Home Assistant from that directory, adds config
entries through the real config flows in safe modes only, checks entities,
actions and frontend static paths, and unloads every entry cleanly.

Safe modes: the read-only sensor proxy fed by fictional sensor states and the
read-only OCPP import shadow over fictional OCPP registry entries. The mainnet
form is exercised only up to its acknowledgement boundary; no mainnet entry is
ever created. The removed mock and embedded testnet backends must not be
offered. Outbound sockets to anything but loopback are refused.

This is automated packaging evidence. It is not an operator restore drill, a
HACS download from GitHub, or a production installation.
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
STATIC_PATHS = {
    "/bsv_settlement/operator-card.js": "frontend/operator-card.js",
    "/bsv_settlement/session-review-card.js": "frontend/session-review-card.js",
    "/bsv_settlement/budget-card.js": "frontend/budget-card.js",
    "/bsv_settlement/driver": "frontend/driver",
}
REMOVED_BACKENDS = ("mock", "embedded_testnet")
MAINNET_ACKS = ("acknowledge_key_custody", "acknowledge_mainnet", "enable_broadcast")
OCPP_METRICS = {"import": ("energy_active_import_register", "100", "kWh"),
                "transaction": ("transaction_id", "42", None),
                "status": ("status_connector", "Charging", None)}
FICTIONAL_PROXY = {
    "import_entity": ("sensor.demo_charger_import", "12.345", "MWh"),
    "export_entity": ("sensor.demo_charger_export", "1.234", "MWh"),
    "state_entity": ("sensor.demo_charger_state", "Available", None),
    "import_price_entity": ("sensor.demo_import_price", "0.30", "$/kWh"),
    "export_price_entity": ("sensor.demo_export_price", "0.05", "$/kWh"),
}


def hacs_payload(repo=ROOT):
    """Return (domain, source folder) exactly as HACS selects it."""
    info = json.loads((repo / "hacs.json").read_text())
    if info.get("content_in_root", False):
        raise SystemExit("content_in_root=true is not the supported layout")
    folders = [p for p in (repo / "custom_components").iterdir() if p.is_dir()]
    if len(folders) != 1:
        raise SystemExit(f"HACS expects one integration folder, found {len(folders)}")
    manifest = json.loads((folders[0] / "manifest.json").read_text())
    if manifest["domain"] != folders[0].name:
        raise SystemExit("Folder name must equal manifest domain")
    return manifest["domain"], folders[0], info


def stage(config_dir, repo=ROOT):
    domain, source, _ = hacs_payload(repo)
    target = Path(config_dir) / "custom_components" / domain
    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing install at {target}")
    shutil.copytree(source, target, ignore=IGNORE)
    return domain, target


def version_tuple(text):
    """Leading numeric release only, so 2026.10.0b2 compares as 2026.10.0."""
    return tuple(int(re.match(r"\d*", piece).group() or 0) for piece in text.split(".")[:3])


def block_external_network():
    """Fail loudly on any outbound connection except loopback (test servers)."""
    original = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if sock.family in (socket.AF_INET, socket.AF_INET6) and host not in (
                "127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"Network forbidden in clean-install smoke test: {host}")
        return original(sock, address)

    socket.socket.connect = guarded


async def child(config_dir):
    block_external_network()
    config_dir = str(Path(config_dir).resolve())
    sys.path.insert(0, config_dir)  # As HA startup does for custom_components.
    from aiohttp.test_utils import TestClient, TestServer
    from homeassistant import bootstrap, loader
    from homeassistant.auth import auth_manager_from_config
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
    from homeassistant.const import __version__ as ha_version
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import InvalidData
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from homeassistant.setup import async_setup_component

    hass = HomeAssistant(config_dir)
    hass.config.skip_pip = True
    hass.config.external_url = None
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    report = {"home_assistant": ha_version}
    try:
        custom = await loader.async_get_custom_components(hass)
        assert list(custom) == ["bsv_settlement"], f"unexpected custom integrations {list(custom)}"
        integration = custom["bsv_settlement"]
        assert str(integration.file_path).startswith(config_dir), integration.file_path
        assert await bootstrap.async_load_base_functionality(hass)
        hass.auth = await auth_manager_from_config(hass, [], [])
        assert await async_setup_component(hass, "http", {"http": {}})
        assert await async_setup_component(hass, "bsv_settlement", {})
        module = sys.modules["custom_components.bsv_settlement"]
        assert module.__file__.startswith(config_dir), "repository copy imported, not the install"
        from custom_components.bsv_settlement.const import SERVICES

        flow = hass.config_entries.flow
        user = {"source": "user"}

        # Backend choice: read-only default, no removed backends.
        result = await flow.async_init("bsv_settlement", context=user)
        assert result["type"] == "form" and result["step_id"] == "user"
        key = next(iter(result["data_schema"].schema))
        assert key.default() == "sensor_proxy", key.default()
        choices = set(result["data_schema"].schema[key].container)
        assert choices == {"sensor_proxy", "ocpp_import_shadow", "embedded_mainnet"}, choices
        for removed in REMOVED_BACKENDS:
            try:
                await flow.async_configure(result["flow_id"], {"backend": removed})
            except InvalidData:
                pass
            else:
                raise AssertionError(f"removed backend {removed} accepted")
        flow.async_abort(result["flow_id"])

        # Mainnet: each missing acknowledgement is refused; no entry is ever made.
        for missing in MAINNET_ACKS:
            result = await flow.async_init("bsv_settlement", context=user)
            result = await flow.async_configure(result["flow_id"], {"backend": "embedded_mainnet"})
            assert result["step_id"] == "mainnet"
            result = await flow.async_configure(result["flow_id"], {
                **{k: True for k in MAINNET_ACKS}, missing: False})
            assert result["type"] == "form" and result["errors"] == {"base": "acknowledgement_required"}
            flow.async_abort(result["flow_id"])
        assert not hass.config_entries.async_entries("bsv_settlement")

        # Read-only sensor proxy over fictional states (no recorder: degraded, not failed).
        for entity_id, value, unit in FICTIONAL_PROXY.values():
            hass.states.async_set(entity_id, value, {"unit_of_measurement": unit} if unit else {})
        result = await flow.async_init("bsv_settlement", context=user)
        result = await flow.async_configure(result["flow_id"], {"backend": "sensor_proxy"})
        assert result["step_id"] == "proxy"
        result = await flow.async_configure(result["flow_id"], {
            "name": "Demo charging sessions", **{k: v[0] for k, v in FICTIONAL_PROXY.items()}})
        assert result["type"] == "create_entry", result
        proxy = result["result"]
        await hass.async_block_till_done()

        # Read-only OCPP import shadow over fictional OCPP registry rows (no OCPP integration).
        native = ConfigEntry(version=1, minor_version=1, domain="ocpp", title="Fictional OCPP",
                             data={}, source="user", unique_id=None, options={},
                             discovery_keys=MappingProxyType({}), subentries_data=[])
        hass.config_entries._entries[native.entry_id] = native
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=native.entry_id, identifiers={("ocpp", "fictional")})
        sources = {}
        for key, (metric, value, unit) in OCPP_METRICS.items():
            row = er.async_get(hass).async_get_or_create(
                "sensor", "ocpp", f"ocpp.fictional.{metric}.sensor", config_entry=native,
                device_id=device.id, suggested_object_id="fictional_" + metric)
            hass.states.async_set(row.entity_id, value, {"unit_of_measurement": unit} if unit else {})
            sources[key + "_entity"] = row.entity_id
        result = await flow.async_init("bsv_settlement", context=user)
        result = await flow.async_configure(result["flow_id"], {"backend": "ocpp_import_shadow"})
        assert result["step_id"] == "ocpp_shadow"
        result = await flow.async_configure(result["flow_id"], {"name": "Fictional shadow", **sources})
        assert result["step_id"] == "ocpp_shadow_metadata", result
        result = await flow.async_configure(result["flow_id"], {})
        assert result["type"] == "create_entry", result
        shadow = result["result"]
        await hass.async_block_till_done()

        entries = hass.config_entries.async_entries("bsv_settlement")
        assert {e.data["backend"] for e in entries} == {"sensor_proxy", "ocpp_import_shadow"}
        assert all(e.state is ConfigEntryState.LOADED for e in entries), [e.state for e in entries]
        missing = [name for name in SERVICES if not hass.services.has_service("bsv_settlement", name)]
        assert not missing, f"actions not registered: {missing}"
        assert not hass.services.has_service("bsv_settlement", "request_payment")
        from custom_components.bsv_settlement.grouped_wallet_test import SERVICE as GROUPED_TEST
        registered = set(hass.services.async_services().get("bsv_settlement", {}))
        assert registered == {*SERVICES, GROUPED_TEST}, registered ^ {*SERVICES, GROUPED_TEST}

        registry = er.async_get(hass)
        entities = {}
        for entry in (proxy, shadow):
            rows = er.async_entries_for_config_entry(registry, entry.entry_id)
            assert rows, f"no entities for {entry.data['backend']}"
            for row in rows:
                assert hass.states.get(row.entity_id) is not None, row.entity_id
            entities[entry.data["backend"]] = len(rows)
        recorder = [hass.states.get(r.entity_id) for r in er.async_entries_for_config_entry(
            registry, proxy.entry_id) if r.unique_id.endswith("recorder_status")]
        assert recorder and recorder[0].state not in ("unknown", "unavailable"), recorder

        # Frontend: every registered path resolves to a shipped file in the install.
        installed = Path(config_dir) / "custom_components" / "bsv_settlement"
        for rel in STATIC_PATHS.values():
            assert (installed / rel).exists(), rel
        async with TestClient(TestServer(hass.http.app)) as client:
            for url in ("/bsv_settlement/operator-card.js", "/bsv_settlement/session-review-card.js",
                        "/bsv_settlement/budget-card.js", "/bsv_settlement/driver/index.html"):
                response = await client.get(url)
                body = await response.read()
                assert response.status == 200 and body, (url, response.status)
                expected = installed / ("frontend/driver/index.html" if url.endswith("index.html")
                                        else STATIC_PATHS[url])
                assert body == expected.read_bytes(), url
        report["static_paths"] = sorted(STATIC_PATHS)

        for entry in (proxy, shadow):
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert entry.state is ConfigEntryState.NOT_LOADED
        await hass.async_block_till_done()
        assert not hass.data["bsv_settlement"], "coordinators left behind after unload"
        for entry in (proxy, shadow):
            for row in er.async_entries_for_config_entry(registry, entry.entry_id):
                state = hass.states.get(row.entity_id)
                assert state is None or state.state == "unavailable", (row.entity_id, state)
        report.update(entries=sorted(entities), entities=entities, actions=len(registered),
                      unloaded=True)
    finally:
        await hass.async_stop(force=True)
    return report


def run(config_dir=None, python=sys.executable):
    with tempfile.TemporaryDirectory(prefix="bsv-clean-install-") as tmp:
        config = Path(config_dir or tmp)
        config.mkdir(parents=True, exist_ok=True)
        if any(config.iterdir()):
            raise SystemExit("Clean install requires an empty configuration directory")
        domain, target = stage(config)
        completed = subprocess.run(
            [python, "-I", str(Path(__file__).resolve()), "--child", str(config)],
            cwd=config, capture_output=True, text=True, timeout=600)
        if completed.returncode:
            raise SystemExit(f"Clean-install child failed:\n{completed.stdout}\n{completed.stderr}")
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        report["installed_files"] = sum(1 for p in target.rglob("*") if p.is_file())
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", metavar="CONFIG_DIR", help=argparse.SUPPRESS)
    parser.add_argument("--config-dir", help="Empty directory to use instead of a temporary one")
    args = parser.parse_args()
    if args.child:
        import logging
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        print(json.dumps(asyncio.run(child(args.child)), sort_keys=True))
        return
    _, _, info = hacs_payload()
    from homeassistant.const import __version__ as ha_version
    if version_tuple(ha_version) < version_tuple(info["homeassistant"]):
        raise SystemExit(f"HA {ha_version} is below hacs.json minimum {info['homeassistant']}")
    print(json.dumps(run(args.config_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

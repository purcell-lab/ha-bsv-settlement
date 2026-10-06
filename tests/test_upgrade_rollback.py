"""Upgrade/rollback storage regression over fictional fixture stores. No network.

Fixtures in ``tests/fixtures/upgrade`` were written by earlier code: ``v0.1.2``
by the v0.1.2 release itself and ``main`` by main at fdbcacc (see its
``manifest.json``). Private keys are never committed: each run generates a new,
unfunded identity and substitutes it before loading.

"Upgrade" means the current integration loads those stores. "Rollback
compatibility" means that, after the current code has loaded and re-saved them,
the on-disk store versions are unchanged and every earlier top-level key is
still present, so the previous release can read them again. This is automated
evidence only; it is not the protected restore drill tracked by #4, and it does
not make a downgrade safe after new features have written state (see
docs/release-checklist.md, "Downgrade hazards").
"""
import copy
import json
from pathlib import Path
import re
import socket
from types import MappingProxyType

import pytest

pytest.importorskip("homeassistant")
from homeassistant import bootstrap, loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.embedded import _identity
from custom_components.bsv_settlement.ledger_checkpoint import fingerprint
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "upgrade"
COMPONENT = ROOT / "custom_components" / "bsv_settlement"
TESTNET = "01FIXTUREEMBEDDEDTESTNET00"
MAINNET = "01FIXTUREEMBEDDEDMAINNET00"
PROXY = "01FIXTURESENSORPROXY000000"
MOCK = "01FIXTUREMOCKV012000000000"


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.socket.connect

    def guarded(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if sock.family in (socket.AF_INET, socket.AF_INET6) and host not in ("127.0.0.1", "::1"):
            raise AssertionError(f"Network forbidden: {host}")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession",
                        lambda hass: None)


def materialize(config_dir, entries=(TESTNET, MAINNET, PROXY)):
    """Copy fixture stores into a fresh config dir with new fictional identities."""
    storage = Path(config_dir) / ".storage"
    storage.mkdir(parents=True)
    identities, subs = {}, {}
    for net in ("testnet", "mainnet"):
        ident = identities[net] = _identity(None, net)
        tag = f"__FIXTURE_{net.upper()}_"
        subs.update({tag + "SECRET__": ident["secret_hex"], tag + "PUBLIC_KEY__": ident["public_key"],
                     tag + "ADDRESS__": ident["address"]})
    for source in sorted((FIXTURES / "main").iterdir()):
        text = source.read_text()
        for placeholder, value in subs.items():
            text = text.replace(placeholder, value)
        assert "__FIXTURE_" not in text
        store = json.loads(text)
        if source.name == "core.config_entries":
            store["data"]["entries"] = [e for e in store["data"]["entries"] if e["entry_id"] in entries]
        (storage / source.name).write_text(json.dumps(store))
    witness = json.loads((storage / f"bsv_settlement.ledger_checkpoint.{MAINNET}").read_text())
    ledger = json.loads((storage / f"bsv_settlement.embedded.{MAINNET}").read_text())
    witness["data"]["digest"] = fingerprint(ledger["data"])
    (storage / f"bsv_settlement.ledger_checkpoint.{MAINNET}").write_text(json.dumps(witness))
    for source in (FIXTURES / "v0.1.2").iterdir():
        (storage / source.name).write_text(source.read_text())
    return identities


def read_store(config_dir, name):
    return json.loads((Path(config_dir) / ".storage" / name).read_text())


def assert_rollback_readable(before, after):
    """Same store version and no earlier top-level key dropped or retyped."""
    assert (after["version"], after["minor_version"]) == (before["version"], before["minor_version"])
    assert after["key"] == before["key"]
    for key, value in before["data"].items():
        assert key in after["data"], f"{before['key']}: {key} dropped"
        assert type(after["data"][key]) is type(value), f"{before['key']}: {key} retyped"


@pytest.mark.asyncio
async def test_upgrade_loads_previous_testnet_and_proxy_entries_in_full_ha(tmp_path):
    """HA reads the earlier config entries and stores from disk and sets them up."""
    identities = materialize(tmp_path, entries=(TESTNET, PROXY))
    names = [f"bsv_settlement.{TESTNET}", f"bsv_settlement.embedded.{TESTNET}",
             f"bsv_settlement.operator_key.{TESTNET}", f"bsv_settlement.proxy.{PROXY}"]
    before = {name: read_store(tmp_path, name) for name in names}
    key_bytes = (tmp_path / ".storage" / f"bsv_settlement.operator_key.{TESTNET}").read_bytes()
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    try:
        assert "bsv_settlement" in await loader.async_get_custom_components(hass)
        assert await bootstrap.async_load_base_functionality(hass)
        assert await async_setup_component(hass, "bsv_settlement", {})
        await hass.async_block_till_done()
        entries = {e.entry_id: e for e in hass.config_entries.async_entries("bsv_settlement")}
        assert set(entries) == {TESTNET, PROXY}
        assert all(e.state is ConfigEntryState.LOADED for e in entries.values())

        # Identity: same key, no rotation, same public key in the entry and sensor.
        public_key = identities["testnet"]["public_key"]
        assert entries[TESTNET].data["operator_public_key"] == public_key
        coordinator = hass.data["bsv_settlement"][TESTNET]
        assert coordinator.api.identity["public_key"] == public_key
        registry = er.async_get(hass)
        wallet = next(r for r in er.async_entries_for_config_entry(registry, TESTNET)
                      if r.unique_id.endswith("operator_wallet_status"))
        state = hass.states.get(wallet.entity_id)
        assert state.state == "ready_broadcast_disabled"
        assert state.attributes["operator_public_key"] == public_key

        # Account history: sessions, frozen payloads and wallet records unchanged.
        assert coordinator.saved == before[names[0]]["data"]
        assert coordinator.api.saved == before[names[1]]["data"]

        proxy = hass.data["bsv_settlement"][PROXY]
        for key, rows in before[names[3]]["data"]["observations"].items():
            assert proxy.observations[key][:len(rows)] == rows
        assert proxy.archive[:len(before[names[3]]["data"]["archive"])] == before[names[3]]["data"]["archive"]

        for entry_id in (TESTNET, PROXY):
            assert await hass.config_entries.async_unload(entry_id)
        await hass.async_block_till_done()
    finally:
        await hass.async_stop(force=True)
    assert (tmp_path / ".storage" / f"bsv_settlement.operator_key.{TESTNET}").read_bytes() == key_bytes
    for name in names:
        assert_rollback_readable(before[name], read_store(tmp_path, name))
    assert read_store(tmp_path, names[1])["data"] == before[names[1]]["data"]


@pytest.mark.asyncio
async def test_upgrade_preserves_mainnet_identity_and_unresolved_reservation(tmp_path):
    """An uncertain broadcast stays reserved: no rebroadcast, cancel or new spend."""
    identities = materialize(tmp_path)
    ledger_name = f"bsv_settlement.embedded.{MAINNET}"
    witness_name = f"bsv_settlement.ledger_checkpoint.{MAINNET}"
    before = read_store(tmp_path, ledger_name)
    draft_id = before["data"]["active_payment"]
    payment = before["data"]["payments"][draft_id]
    assert payment["state"] == "broadcast_unknown" and payment["txid"]
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()  # Reads the earlier core.config_entries.
    entry = hass.config_entries.async_get_entry(MAINNET)
    try:
        api = MainnetWalletAPI(hass, entry)
        await api.load()
        assert api.identity["public_key"] == identities["mainnet"]["public_key"]
        assert api.saved["payments"] == before["data"]["payments"]

        class Chain:
            posts = []

            async def broadcast(self, raw):
                self.posts.append(raw)
                raise AssertionError("must not rebroadcast")

            async def fee_policy(self):
                return {"fee_unit": "sat/KB", "fee": 40, "mempool_min_fee": 40}

            async def unspent(self, address):
                return [{"tx_hash": payment["source_txid"], "tx_pos": payment["source_index"],
                         "value": payment["source_value"], "height": 1, "isSpentInMempoolTx": False}]

        api.chain = Chain()
        approval = {k: payment[k] for k in ("draft_id", "recipient_address", "amount_sats", "fee_sats")}
        result = await api.broadcast_payment({**approval, "confirm_mainnet_payment": True}, "admin")
        assert result["state"] == "broadcast_unknown" and api.chain.posts == []
        with pytest.raises(WalletError):
            await api.cancel_payment({"draft_id": draft_id})
        with pytest.raises(WalletError, match="Resolve"):
            await api.prepare_payment({"reference": "credit-fixture-0002",
                                       "amount_sats": 1000, "fee_sats": 100})
        assert api.status()["last_payment"]["state"] == "broadcast_unknown"
    finally:
        await hass.async_stop(force=True)
    after = read_store(tmp_path, ledger_name)
    assert_rollback_readable(before, after)
    assert after["data"]["payments"] == before["data"]["payments"]
    assert read_store(tmp_path, witness_name)["data"]["digest"] == fingerprint(after["data"])


@pytest.mark.asyncio
async def test_split_restore_of_older_mainnet_ledger_fails_closed(tmp_path):
    """Restoring only an older ledger file (witness newer) refuses to load."""
    materialize(tmp_path)
    ledger_path = tmp_path / ".storage" / f"bsv_settlement.embedded.{MAINNET}"
    older = json.loads(ledger_path.read_text())
    older["data"]["payments"] = {}
    older["data"]["active_payment"] = None
    ledger_path.write_text(json.dumps(older))
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()  # Reads the earlier core.config_entries.
    entry = hass.config_entries.async_get_entry(MAINNET)
    try:
        with pytest.raises(WalletError, match="restore its matching backup"):
            await MainnetWalletAPI(hass, entry).load()
    finally:
        await hass.async_stop(force=True)
    assert json.loads(ledger_path.read_text()) == older  # Left intact for operator review.


@pytest.mark.asyncio
async def test_upgrade_from_v012_release_store_keeps_history_and_open_session(tmp_path):
    """The v0.1.2 mock coordinator store loads, refreshes and accepts the open session."""
    materialize(tmp_path, entries=())
    name = f"bsv_settlement.{MOCK}"
    before = read_store(tmp_path, name)
    sessions = before["data"]["sessions"]
    done = next(s for s in sessions.values() if s.get("payload"))
    open_session = next(s for s in sessions.values() if not s.get("payload"))

    class StubMockService:
        """Returns the stored settlement; any other call would be a new payment path."""
        mode = "mock"
        calls = []

        async def call(self, method, path, data=None):
            self.calls.append((method, path))
            if (method, path) == ("GET", "/v1/health"):
                return {"status": "ok", "mode": "mock"}
            if (method, path) == ("GET", f"/v1/settlements/{done['settlement_id']}"):
                return copy.deepcopy(done["remote"])
            raise AssertionError(f"unexpected wallet call {method} {path}")

    entry = ConfigEntry(version=1, minor_version=1, domain="bsv_settlement",
                        title="BSV Settlement (Mock)",
                        data={"service_url": "http://wallet-mock.invalid:8091",
                              "api_token": "fictional-token"},
                        source="user", unique_id="http://wallet-mock.invalid:8091", options={},
                        discovery_keys=MappingProxyType({}), subentries_data=[], entry_id=MOCK)
    hass = HomeAssistant(str(tmp_path))
    try:
        coord = SettlementCoordinator(hass, entry, StubMockService())
        await coord.load()
        assert coord.saved == before["data"]
        data = await coord._async_update_data()
        assert data["sessions"][done["session_id"]]["remote"]["receipt_id"] == done["remote"]["receipt_id"]
        assert data["latest"] == before["data"]["latest"]
        interval = {**open_session["intervals"][0],
                    "start": open_session["intervals"][0]["end"],
                    "end": open_session["intervals"][0]["end"].replace(":30:", ":59:")}
        await coord.execute("add_interval", {"session_id": open_session["session_id"],
                                             "interval": interval})
        assert len(coord.saved["sessions"][open_session["session_id"]]["intervals"]) == 2
    finally:
        await hass.async_stop(force=True)
    after = read_store(tmp_path, name)
    assert_rollback_readable(before, after)
    assert after["data"]["sessions"][done["session_id"]] == done


def test_store_versions_are_pinned_for_rollback_review():
    """A store version bump makes older releases refuse the file: review the checklist."""
    from custom_components.bsv_settlement import ocpp_shadow, recorder_reconciliation
    sources = "\n".join(p.read_text() for p in COMPONENT.glob("*.py"))
    versions = re.findall(r"(?<!\w)Store\(\s*hass,\s*(\w+),", sources)
    assert versions and set(versions) == {"1"}, versions
    assert ocpp_shadow.STORE_VERSION == 3
    assert recorder_reconciliation.SCHEMA == 1
    checklist = (ROOT / "docs" / "release-checklist.md").read_text()
    for marker in ("Downgrade hazards", "ocpp_shadow", "ledger_checkpoint", "monthly"):
        assert marker in checklist


def test_fixtures_hold_no_private_key_material():
    for path in FIXTURES.rglob("*"):
        if path.is_file():
            text = path.read_text()
            for match in re.findall(r'"secret_hex":\s*"([^"]*)"', text):
                assert match.startswith("__FIXTURE_"), path.name

"""Shared operator-wallet base for MainnetWalletAPI; not a backend on its own.

Key custody, the offline self-test and the frozen settlement-draft emulation.
No broadcaster or payment endpoint exists here; mainnet.py adds the guarded
payment paths. Never use the self-test as a receipt.
"""
import copy
import hashlib
import secrets

from bsv import PrivateKey, P2PKH, Transaction, TransactionInput, TransactionOutput
from bsv.constants import Network
from bsv.script.spend import Spend

from .api import WalletError
from .records import VersionedStore, check_keys


def _identity(secret, network):
    key = PrivateKey(bytes.fromhex(secret) if secret else None,
                     network=Network.MAINNET if network == "mainnet" else Network.TESTNET)
    if not key._is_valid_secret(key.serialize()):
        raise ValueError("Invalid key")
    return {
        "secret_hex": key.hex(),
        "public_key": key.public_key().hex(),
        "address": key.address(),
        "network": network,
    }


def _self_test(secret):
    """Real SDK signatures against a nonce and an explicitly fictional source.

    The testnet key encoding is historical and only affects the fictional,
    never-broadcast transaction below; it must not change mainnet behaviour.
    It does sign with the live mainnet key: the signed bytes stay local and only
    their SHA-256 is persisted (#113, informational; deliberately unchanged).
    """
    key = PrivateKey(bytes.fromhex(secret), network=Network.TESTNET)
    challenge = b"ha-bsv-settlement:offline-self-test:" + secrets.token_bytes(32)
    signature = key.sign(challenge)
    verified = key.public_key().verify(signature, challenge)
    # This source is not a UTXO and has never been broadcast. The signed child
    # cannot settle anything. Neither source nor signed bytes leave this method.
    source = Transaction(
        [TransactionInput(source_txid="00" * 32, source_output_index=0)],
        [TransactionOutput(P2PKH().lock(key.address()), satoshis=10000)],
    )
    tx = Transaction(
        [TransactionInput(source_transaction=source, source_output_index=0,
                          unlocking_script_template=P2PKH().unlock(key))],
        [TransactionOutput(P2PKH().lock(key.address()), satoshis=9900)],
    )
    tx.sign()
    script_verified = Spend({
        "sourceTXID": source.txid(), "sourceOutputIndex": 0,
        "sourceSatoshis": 10000, "lockingScript": source.outputs[0].locking_script,
        "transactionVersion": tx.version, "otherInputs": [],
        "outputs": tx.outputs, "inputIndex": 0,
        "unlockingScript": tx.inputs[0].unlocking_script,
        "inputSequence": tx.inputs[0].sequence, "lockTime": tx.locktime,
    }).validate()
    if not verified or not script_verified:
        raise ValueError("SDK signing self-test failed")
    return {"identity_signature_verified": True,
            "synthetic_transaction_signed": True,
            "synthetic_script_verified": True,
            "signed_bytes_sha256": hashlib.sha256(bytes.fromhex(tx.hex())).hexdigest(),
            "source": "fictional_unbroadcast_transaction",
            "network_checked": False, "broadcast": False, "txid": None}


class EmbeddedWalletAPI:
    """Local implementation of only the safe subset of the settlement API.

    Subclasses set mode and network; the base refuses to load without them.
    """

    mode = None
    network = None

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.key_store = VersionedStore(hass, "operator_key", entry.entry_id)
        self.store = VersionedStore(hass, "wallet_ledger", entry.entry_id)
        self.identity = None
        self.saved = {"records": {}, "last_self_test": None}

    async def load(self):
        if (self.network is None or self.entry.data.get("network") != self.network
                or self.entry.data.get("acknowledge_key_custody") is not True):
            raise WalletError("Operator wallet requires its network and key-custody acknowledgement")
        try:
            stored = await self.key_store.async_load()
            expected = self.entry.data.get("operator_public_key")
            if stored is None and expected:
                raise ValueError("Missing key; restore backup rather than rotate")
            if stored is not None:
                if stored.get("network") != self.network:
                    raise ValueError("Unexpected wallet network")
                identity = await self.hass.async_add_executor_job(_identity, stored["secret_hex"], self.network)
                if identity != stored:
                    raise ValueError("Corrupt wallet identity")
            else:
                identity = await self.hass.async_add_executor_job(_identity, None, self.network)
            if expected and identity["public_key"] != expected:
                raise ValueError("Wallet identity changed")
            if stored is None:
                await self.key_store.async_save(identity)
            if not expected:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data,
                                      "operator_public_key": identity["public_key"]})
            self.identity = identity
            self.saved = await self.store.async_load() or self.saved
            # A namespace from a newer release must not be ignored and overwritten.
            check_keys("wallet_ledger", self.saved)
        except Exception:
            # SDK exceptions and persisted values must never leak key material.
            raise WalletError("Operator wallet could not load safely; restore its matching backup") from None

    def status(self):
        return {
            "mode": self.mode, "network": self.network, "backend": "bsv-sdk",
            "state": "ready_broadcast_disabled", "broadcast_enabled": False,
            "operator_public_key": self.identity["public_key"],
            "receive_address": self.identity["address"],
            "balance_sats": None, "balance_verified": False,
            "budget_gate_implemented": False, "driver_wallet_external": True,
            "last_self_test": copy.deepcopy(self.saved["last_self_test"]),
        }

    async def self_test(self):
        try:
            result = await self.hass.async_add_executor_job(_self_test, self.identity["secret_hex"])
            self.saved["last_self_test"] = result
            await self.store.async_save(self.saved)
            return copy.deepcopy(result)
        except Exception:
            raise WalletError("Offline wallet self-test failed") from None

    async def call(self, method, path, data=None):
        if method == "GET" and path == "/v1/health":
            return self.status()
        if method == "GET" and path == "/v1/wallet-bindings/driver-external":
            return {"verification_status": "external_unverified_draft_only"}
        prefix = "/v1/settlements/"
        if path.startswith(prefix):
            sid = path[len(prefix):]
            if "/" in sid:
                raise WalletError("Payment disabled: budget gate, driver approval and live settlement are not implemented")
            if method == "GET" and sid in self.saved["records"]:
                return copy.deepcopy(self.saved["records"][sid]["record"])
            if method == "PUT":
                old = self.saved["records"].get(sid)
                if old:
                    if old["payload"] != data:
                        raise WalletError("Frozen settlement cannot be changed")
                    return copy.deepcopy(old["record"])
                if any(r["payload"]["session_id"] == data["session_id"]
                       for r in self.saved["records"].values()):
                    raise WalletError("Session already has a settlement draft")
                amount = data["net_amount_minor"]
                record = {
                    "mode": self.mode, "network": self.network, "settlement_id": sid,
                    "session_id": data["session_id"], "net_amount_minor": amount,
                    "state": "no_payment_due" if amount == 0 else "blocked_live_settlement",
                    "direction": "none" if amount == 0 else (
                        "operator_to_driver" if amount < 0 else "driver_to_operator"),
                    "amount_sats": None, "quote": None, "receipt_id": None, "txid": None,
                    "broadcast_enabled": False,
                }
                self.saved["records"][sid] = {"payload": copy.deepcopy(data), "record": record}
                await self.store.async_save(self.saved)
                return copy.deepcopy(record)
        raise WalletError("Unsupported embedded-wallet operation")

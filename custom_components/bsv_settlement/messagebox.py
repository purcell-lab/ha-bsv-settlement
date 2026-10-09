"""Opt-in push delivery of confirmed operator credits (PeerPay-compatible).

A confirmed credit is already a BRC-29 payment to the driver: output 0 pays
the key derived from the operator and driver identities under protocol
[2, "3241645161d8"] and the signed ``credit_receiving`` prefix and suffix.
This module drops that same payment, as AtomicBEEF with its Merkle proof, into
the driver's MessageBox ``payment_inbox``, so a PeerPay-compatible wallet can
accept it without the driver opening the portal.

It never builds, signs, funds, broadcasts or changes a payment. It sends only
evidence of a credit that is already provider-confirmed. Driver-page receipt
sync is unchanged; an inbox acceptance is not reported back here.

Disabled by default. When enabled, only credits created after enablement are
sent automatically; an administrator can send one older credit explicitly.
The message body is encrypted to the driver (BRC-2, [1, "messagebox"], key
"1"). The MessageBox host still sees both identity keys and the timing.
"""
import asyncio
import base64
import contextlib
import copy
import hashlib
import hmac
import io
import json
import re
from datetime import datetime, timedelta

from bsv import PrivateKey, PublicKey, Transaction
from bsv.merkle_path import MerklePath
from bsv.script import P2PKH
from bsv.utils import Writer

from .api import WalletError
from .session_review import now

DEFAULT_HOST = "https://message-box-us-1.bsvb.tech"
BOX = "payment_inbox"
MESSAGE_PROTOCOL = (1, "messagebox")
MESSAGE_KEY_ID = "1"
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(hours=1)
PER_RUN = 2
SEND_TIMEOUT = 75
HOST = re.compile(r"^https://[a-z0-9.-]{1,253}(:[0-9]{1,5})?$")
TXID = re.compile(r"^[0-9a-f]{64}$")
ATOMIC_BEEF = 0x01010101
BEEF_V1 = 0xEFBE0001


class _Result(dict):
    """Wallet results readable as attributes, as bsv.auth.Peer expects."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class OperatorAuthWallet:
    """The minimal BRC-100 surface that bsv.auth Peer/AuthFetch call.

    BRC-42 derivations come from the SDK's KeyDeriver. Unlike ProtoWallet it
    has no prompts and prints nothing. Spending (create_action) and
    certificates are not supported, so payment-gated requests fail closed.
    """

    def __init__(self, private_key):
        from bsv.wallet.key_deriver import KeyDeriver
        self.private_key = private_key
        self.public_key = private_key.public_key()
        self.deriver = KeyDeriver(private_key)

    @staticmethod
    def _protocol(value):
        from bsv.wallet.key_deriver import Protocol
        if isinstance(value, dict):
            return Protocol(value.get("securityLevel", value.get("security_level")), value["protocol"])
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return Protocol(value[0], value[1])
        raise ValueError("Unsupported protocol ID")

    @staticmethod
    def _counterparty(value):
        from bsv.wallet.key_deriver import Counterparty, CounterpartyType
        if isinstance(value, dict):
            inner = value.get("counterparty")
            kind = value.get("type", CounterpartyType.SELF)
            if inner is not None and not isinstance(inner, PublicKey):
                inner = PublicKey(inner)
            return Counterparty(kind, inner)
        if value in (None, "self"):
            return Counterparty(CounterpartyType.SELF)
        if value == "anyone":
            return Counterparty(CounterpartyType.ANYONE)
        if isinstance(value, PublicKey):
            return Counterparty(CounterpartyType.OTHER, value)
        return Counterparty(CounterpartyType.OTHER, PublicKey(value))

    def _args(self, args, default_counterparty):
        inner = args.get("encryption_args")
        if inner is not None:
            return (self._protocol(inner["protocol_id"]), inner["key_id"],
                    self._counterparty(inner.get("counterparty", default_counterparty)))
        return (self._protocol(args["protocolID"]), args["keyID"],
                self._counterparty(args.get("counterparty", default_counterparty)))

    @staticmethod
    def _bytes(value):
        return bytes(value) if isinstance(value, (list, bytearray)) else value

    def get_public_key(self, args=None, originator=None):
        args = args or {}
        if args.get("identityKey"):
            return _Result(public_key=self.public_key, publicKey=self.public_key.hex())
        protocol, key_id, cp = self._args(args, "self")
        key = self.deriver.derive_public_key(protocol, key_id, cp, for_self=bool(args.get("forSelf")))
        return _Result(public_key=key, publicKey=key.hex())

    def create_signature(self, args, originator=None):
        protocol, key_id, cp = self._args(args, "anyone")
        digest = hashlib.sha256(self._bytes(args.get("data", b""))).digest()
        key = self.deriver.derive_private_key(protocol, key_id, cp)
        return _Result(signature=key.sign(digest, hasher=lambda m: m))

    def verify_signature(self, args, originator=None):
        try:
            protocol, key_id, cp = self._args(args, "self")
            key = self.deriver.derive_public_key(protocol, key_id, cp, for_self=bool(args.get("forSelf")))
            digest = hashlib.sha256(self._bytes(args.get("data", b""))).digest()
            valid = key.verify(self._bytes(args["signature"]), digest, hasher=lambda m: m)
        except (KeyError, ValueError, TypeError):
            valid = False
        return _Result(valid=bool(valid))

    def symmetric_key(self, protocol, key_id, counterparty):
        return self.deriver.derive_symmetric_key(self._protocol(protocol), key_id, self._counterparty(counterparty))

    def create_hmac(self, args, originator=None):
        protocol, key_id, cp = self._args(args, "self")
        key = self.deriver.derive_symmetric_key(protocol, key_id, cp)
        return _Result(hmac=hmac.new(key, self._bytes(args["data"]), hashlib.sha256).digest())

    def verify_hmac(self, args, originator=None):
        try:
            expected = self.create_hmac(args)["hmac"]
            valid = hmac.compare_digest(expected, self._bytes(args["hmac"]))
        except (KeyError, ValueError, TypeError):
            valid = False
        return _Result(valid=valid)

    def encrypt_for(self, protocol, key_id, counterparty, plaintext):
        from bsv.primitives.symmetric_key import SymmetricKey
        return SymmetricKey(self.symmetric_key(protocol, key_id, counterparty)).encrypt(plaintext)

    def create_action(self, args, originator=None):
        return _Result(error="This operator wallet does not pay for message delivery")

    def list_certificates(self, args, originator=None):
        return _Result(certificates=[], totalCertificates=0)


def merkle_path(receipt):
    """TSC proof to MerklePath, exactly as the driver page builds it."""
    p, b = receipt["proof"], receipt["block"]
    txid = receipt["txid"]
    if (p.get("txOrId") != txid or p.get("target") != b.get("hash") or type(p.get("index")) is not int
            or p["index"] < 0 or not isinstance(p.get("nodes"), list) or not 1 <= len(p["nodes"]) <= 40
            or type(b.get("height")) is not int or b["height"] < 1
            or not TXID.fullmatch(str(b.get("merkleroot", "")))):
        raise WalletError("Invalid credit Merkle proof")
    path = [[{"offset": p["index"], "hash_str": txid, "txid": True}]]
    index = p["index"]
    for level, node in enumerate(p["nodes"]):
        if node != "*" and not (isinstance(node, str) and TXID.fullmatch(node)):
            raise WalletError("Invalid credit Merkle proof")
        if level >= len(path):
            path.append([])
        sibling = index + 1 if index % 2 == 0 else index - 1
        path[level].append({"offset": sibling, "duplicate": True} if node == "*"
                           else {"offset": sibling, "hash_str": node})
        path[level].sort(key=lambda leaf: leaf["offset"])
        index //= 2
    merkle = MerklePath(b["height"], path)
    if merkle.compute_root(txid) != b["merkleroot"]:
        raise WalletError("Credit Merkle root mismatch")
    return merkle


def atomic_beef(receipt):
    """BRC-95 AtomicBEEF of one mined transaction with its BRC-74 path."""
    tx = Transaction.from_hex(receipt["raw_tx"])
    if tx is None or tx.txid() != receipt["txid"]:
        raise WalletError("Credit transaction does not match its receipt")
    bump = merkle_path(receipt).to_binary()
    writer = Writer()
    writer.write_uint32_le(ATOMIC_BEEF)
    writer.write(bytes.fromhex(receipt["txid"])[::-1])
    writer.write_uint32_le(BEEF_V1)
    writer.write_var_int_num(1)
    writer.write(bump)
    writer.write_var_int_num(1)
    writer.write(bytes.fromhex(receipt["raw_tx"]))
    writer.write_uint8(1)
    writer.write_var_int_num(0)
    return tx, writer.to_bytes()


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def payment_request(wallet, recipient, receipt):
    """The exact /sendMessage request a PeerPay sender makes for this credit."""
    remit = receipt["remittance"]
    tx, beef = atomic_beef(receipt)
    expected = P2PKH().lock(receipt["recipient_address"]).hex()
    if (not tx.outputs or tx.outputs[0].satoshis != receipt["amount_sats"]
            or tx.outputs[0].locking_script.hex() != expected):
        raise WalletError("Credit output does not match its receipt")
    token = {"customInstructions": {"derivationPrefix": remit["derivationPrefix"],
                                    "derivationSuffix": remit["derivationSuffix"]},
             "transaction": list(beef), "amount": receipt["amount_sats"], "outputIndex": 0}
    body = compact(token)
    message_id = hmac.new(wallet.symmetric_key(MESSAGE_PROTOCOL, MESSAGE_KEY_ID, recipient),
                          body.encode(), hashlib.sha256).hexdigest()
    ciphertext = wallet.encrypt_for(MESSAGE_PROTOCOL, MESSAGE_KEY_ID, recipient, body.encode())
    wire = compact({"encryptedMessage": base64.b64encode(ciphertext).decode()})
    return message_id, compact({"message": {"recipient": recipient, "messageBox": BOX,
                                            "messageId": message_id, "body": wire}})


def send(secret_hex, host, payload):
    """Blocking BRC-103 authenticated POST; run in an executor."""
    from bsv.auth.clients.auth_fetch import AuthFetch, SimplifiedFetchRequestOptions
    wallet = OperatorAuthWallet(PrivateKey(bytes.fromhex(secret_hex)))
    # The SDK's auth helpers print nonce diagnostics; keep them out of HA logs.
    with contextlib.redirect_stdout(io.StringIO()):
        response = AuthFetch(wallet, None).fetch(host + "/sendMessage", SimplifiedFetchRequestOptions(
            method="POST", headers={"Content-Type": "application/json"}, body=payload.encode()))
    try:
        body = json.loads(response.text[:4096])
    except ValueError:
        body = None
    return response.status_code, body


class MessageBoxDelivery:
    def __init__(self, api, sender=send):
        self.api = api
        self.sender = sender
        self.running = False
        api.saved.setdefault("messagebox_delivery_policy", {"enabled": False, "host": DEFAULT_HOST})

    @property
    def policy(self):
        return self.api.saved["messagebox_delivery_policy"]

    async def configure(self, data, user_id):
        enabled, host = data.get("enabled"), data.get("host", self.policy.get("host", DEFAULT_HOST))
        if not user_id or type(enabled) is not bool or not isinstance(host, str) or not HOST.fullmatch(host):
            raise WalletError("An administrator must set the MessageBox delivery policy with an HTTPS host")
        if enabled and not self.policy.get("enabled"):
            self.api.saved["messagebox_delivery_policy"] = {
                "enabled": True, "host": host, "enabled_at": now().isoformat(), "authorised_by": user_id}
        else:
            self.policy.update(enabled=enabled, host=host)
        await self.api.store.async_save(self.api.saved)
        return self.summary()

    def summary(self):
        return {k: self.policy.get(k) for k in ("enabled", "host", "enabled_at")} | {"message_box": BOX}

    def credits(self):
        """(credit_id, receiving row, item, driver identity) for every credit."""
        rows, items = self.api.saved["session_budgets"], self.api.saved["automatic_credits"]
        for credit_id, item in items.items():
            if credit_id.startswith("ongoing:"):
                route = self.api.ongoing_credits.routes.get(credit_id)
                row = rows.get(route["recipient"]["budget_id"]) if route else None
                identity = route["recipient"].get("driver_identity") if route else None
            else:
                row = rows.get(credit_id)
                identity = (row.get("receipt") or {}).get("driver_identity") if row else None
            if row and identity and (row.get("receipt") or {}).get("driver_identity") == identity:
                yield credit_id, row, item, identity

    def due(self, item, *, requested=False):
        delivery = item.get("messagebox_delivery") or {}
        if (not self.policy.get("enabled") or item.get("state") != "provider_confirmed"
                or item.get("wallet_receipt_ack") or delivery.get("state") == "sent"):
            return False
        if requested:
            return bool(delivery.get("requested_at"))
        if (not item.get("created_at") or item["created_at"] < self.policy["enabled_at"]
                or delivery.get("attempts", 0) >= MAX_ATTEMPTS):
            return False
        last = delivery.get("last_attempt_at")
        return not last or datetime.fromisoformat(last) + RETRY_AFTER <= now()

    async def request(self, credit_id, user_id):
        if not user_id or not isinstance(credit_id, str):
            raise WalletError("An administrator must request a specific credit")
        if not self.policy.get("enabled"):
            raise WalletError("Enable MessageBox delivery first")
        match = next((c for c in self.credits() if c[0] == credit_id), None)
        if match is None:
            raise WalletError("Credit not found")
        item = match[2]
        delivery = item.setdefault("messagebox_delivery", {})
        if delivery.get("state") == "sent":
            raise WalletError("This credit was already delivered to the inbox")
        if item.get("state") != "provider_confirmed" or item.get("wallet_receipt_ack"):
            raise WalletError("Only a provider-confirmed credit the wallet has not reported can be sent")
        delivery.update(requested_at=now().isoformat(), requested_by=user_id, attempts=0)
        await self.api.store.async_save(self.api.saved)
        return {"credit_id": credit_id, "queued": True}

    def schedule(self, lock):
        """Called from the coordinator tick; network work runs outside the lock."""
        if self.running or not any(
                self.due(i) or self.due(i, requested=True) for _, _, i, _ in self.credits()):
            return None
        self.running = True
        return self.api.hass.async_create_background_task(
            self.run(lock), "bsv_settlement messagebox delivery")

    async def run(self, lock):
        try:
            async with lock:
                prepared = await self.prepare()
            for credit_id, item, host, message_id, payload in prepared:
                try:
                    status, body = await asyncio.wait_for(self.api.hass.async_add_executor_job(
                        self.sender, self.api.identity["secret_hex"], host, payload), SEND_TIMEOUT)
                    ok = status == 200 and isinstance(body, dict) and body.get("status") == "success"
                    error = None if ok else f"MessageBox refused delivery (HTTP {status})"
                except Exception as exc:  # noqa: BLE001 - recorded as an attempt, never raised
                    ok, error = False, f"MessageBox delivery failed: {type(exc).__name__}"
                async with lock:
                    delivery = item["messagebox_delivery"]
                    if ok:
                        delivery.update(state="sent", sent_at=now().isoformat(), error=None)
                        delivery.pop("requested_at", None)
                    else:
                        delivery.update(state="failed", error=error)
                        if delivery.get("attempts", 0) >= MAX_ATTEMPTS:
                            delivery.pop("requested_at", None)
                    await self.api.store.async_save(self.api.saved)
        finally:
            self.running = False

    async def prepare(self):
        """Under the coordinator lock: choose due credits and freeze their requests."""
        result = []
        wallet = OperatorAuthWallet(PrivateKey(bytes.fromhex(self.api.identity["secret_hex"])))
        for credit_id, row, item, identity in list(self.credits()):
            if len(result) >= PER_RUN:
                break
            if not (self.due(item, requested=True) or self.due(item)):
                continue
            delivery = item.setdefault("messagebox_delivery", {})
            delivery.update(attempts=delivery.get("attempts", 0) + 1, last_attempt_at=now().isoformat(),
                            recipient=identity, host=self.policy["host"], state="sending")
            try:
                receipt = await self.api.auto_credits.receipt_for_item(row, item)
                if (receipt["sender_identity"] != self.api.identity["public_key"]
                        or receipt["recipient_address"] != self.api.auto_credits.destination(row).address()):
                    raise WalletError("Credit recipient does not match the registered driver wallet")
                message_id, payload = payment_request(wallet, identity, receipt)
            except (WalletError, KeyError, ValueError, TypeError) as exc:
                delivery.update(state="failed", error=str(exc)[:200] if isinstance(exc, WalletError)
                                else "Credit evidence could not be prepared")
                await self.api.store.async_save(self.api.saved)
                continue
            delivery.update(message_id=message_id)
            await self.api.store.async_save(self.api.saved)
            result.append((credit_id, item, self.policy["host"], message_id, payload))
        return result

    @staticmethod
    def public(item):
        delivery = item.get("messagebox_delivery")
        if not delivery:
            return {}
        return {"inbox_delivery": {k: copy.deepcopy(delivery.get(k)) for k in (
            "state", "attempts", "last_attempt_at", "sent_at", "error", "host")}}

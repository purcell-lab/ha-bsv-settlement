"""Bounded provider reads and shared confirmation evidence validation."""
import asyncio

from bsv import Transaction

from .api import WalletError

READ_TIMEOUT_SECONDS = 5


async def read_transaction(chain, txid):
    """Two GETs only, with a combined deadline; never retry or submit."""
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            raw = await chain.request("GET", f"/tx/{txid}/hex", raw=True)
            details = await chain.details(txid)
            return raw, details
    except TimeoutError:
        raise WalletError("Chain confirmation check timed out; evidence unavailable") from None


def confirmation_count(raw, details, txid):
    """Accept an explicit count or complete matching unmined metadata."""
    try:
        tx = Transaction.from_hex(raw) if isinstance(raw, str) else None
        if (not tx or tx.hex() != raw.lower() or tx.txid() != txid
                or not isinstance(details, dict) or details.get("txid") != txid):
            raise ValueError()
        count = details.get("confirmations")
        if "confirmations" not in details:
            if (any(k in details for k in ("blockhash", "blockheight", "blocktime"))
                    or details.get("hash") != txid
                    or type(details.get("version")) is not int or details["version"] != tx.version
                    or type(details.get("locktime")) is not int or details["locktime"] != tx.locktime
                    or type(details.get("size")) is not int or details["size"] != len(raw) // 2
                    or not isinstance(details.get("vin"), list) or len(details["vin"]) != len(tx.inputs)
                    or not isinstance(details.get("vout"), list) or len(details["vout"]) != len(tx.outputs)):
                raise ValueError()
            count = 0
        if type(count) is not int or count < 0:
            raise ValueError()
        return count
    except Exception:
        raise WalletError("Invalid provider transaction or confirmation evidence") from None

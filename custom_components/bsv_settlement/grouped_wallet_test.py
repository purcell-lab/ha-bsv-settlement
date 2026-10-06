"""Opt-in BRC-73 manifest publication. No wallet or settlement operations."""
import asyncio
import copy
import logging
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .records import VersionedStore

KEY = DOMAIN + "_grouped_wallet_test"
SERVICE = "configure_grouped_wallet_test"
LIMIT = 30_000
DECLARATION = {
    "schemaVersion": 1,
    "groupPermissions": {
        "description": "EV charging budget",
        "spendingAuthorization": {
            "amount": LIMIT,
            "description": "Monthly EV charging payments and network fees",
        },
    },
}


def exact_origin(value):
    if not isinstance(value, str):
        raise HomeAssistantError("An exact HTTPS origin is required")
    try:
        url = urlsplit(value)
        valid = (url.hostname and value == "https://" + url.hostname
                 and not url.username and not url.password and not url.port)
    except ValueError:
        valid = False
    if not valid:
        raise HomeAssistantError("Use an HTTPS hostname without a path, port or credentials")
    return value


class GroupedWalletTest:
    def __init__(self, store, manifest, update, external_url):
        self.store, self.manifest, self.update = store, manifest, update
        self.external_url = external_url
        self.settings = {"enabled": False, "origin": None}
        self.lock = asyncio.Lock()

    def can_publish(self):
        # Never overwrite another integration's modern or legacy declaration.
        if self.manifest.manifest.get("babbage") is not None:
            raise HomeAssistantError("Existing legacy wallet manifest must be reviewed")
        current = self.manifest.manifest.get("metanet")
        if current is not None and not (
                self.settings["enabled"] and current == DECLARATION):
            raise HomeAssistantError("Existing wallet manifest must be reviewed")

    async def load(self):
        saved = await self.store.async_load()
        if saved is None:
            return
        if (not isinstance(saved, dict) or set(saved) != {"enabled", "origin"}
                or type(saved["enabled"]) is not bool
                or (saved["enabled"] and exact_origin(saved["origin"]) != self.external_url())
                or (not saved["enabled"] and saved["origin"] is not None)):
            raise HomeAssistantError("Invalid grouped-wallet test configuration")
        if saved["enabled"]:
            self.can_publish()
            self.update("metanet", copy.deepcopy(DECLARATION))
        self.settings = copy.deepcopy(saved)

    async def configure(self, *, enabled, origin=None, confirm_shared_origin=False):
        async with self.lock:
            if type(enabled) is not bool:
                raise HomeAssistantError("enabled must be boolean")
            if enabled:
                origin = exact_origin(origin)
                if confirm_shared_origin is not True or origin != self.external_url():
                    raise HomeAssistantError("Confirm the shared configured external HTTPS origin")
                self.can_publish()
                settings = {"enabled": True, "origin": origin}
            else:
                settings = {"enabled": False, "origin": None}
            # Save first; a failed storage write cannot publish new authority.
            await self.store.async_save(settings)
            if enabled:
                self.update("metanet", copy.deepcopy(DECLARATION))
            elif self.settings["enabled"] and self.manifest.manifest.get("metanet") == DECLARATION:
                # HA's public extension API has no remove operation. Null withdraws
                # our request while preserving all other HA/PWA metadata.
                self.update("metanet", None)
            self.settings = settings
            return self.status()

    def status(self):
        active = (self.settings["enabled"]
                  and self.settings["origin"] == self.external_url()
                  and self.manifest.manifest.get("metanet") == DECLARATION
                  and self.manifest.manifest.get("babbage") is None)
        return {"enabled": bool(active), "origin": self.settings["origin"] if active else None,
                "monthly_limit_sats": LIMIT, "spending_permission": "not_verified"}


class GroupedWalletTestView(HomeAssistantView):
    url = "/api/bsv_settlement/grouped-test"
    name = "api:bsv_settlement:grouped-test"
    requires_auth = False

    def __init__(self, controller):
        self.controller = controller

    async def get(self, request):
        return web.json_response(self.controller.status(), headers={"Cache-Control": "no-store"})


async def install(hass):
    if KEY in hass.data:
        return
    from homeassistant.components.frontend import MANIFEST_JSON, add_manifest_json_key
    controller = GroupedWalletTest(
        VersionedStore(hass, "grouped_wallet_test"), MANIFEST_JSON, add_manifest_json_key,
        lambda: (hass.config.external_url or "").rstrip("/"),
    )
    try:
        await controller.load()
    except Exception:
        # Optional diagnostics must not stop the existing settlement integration.
        # Leave corrupt/conflicting storage intact for operator review.
        logging.getLogger(__name__).warning("Grouped wallet test disabled: saved configuration could not be applied")
    hass.data[KEY] = controller
    hass.http.register_view(GroupedWalletTestView(controller))

    async def handle(call):
        user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        if user is None or not user.is_admin:
            raise HomeAssistantError("An authenticated HA administrator is required")
        result = await controller.configure(**call.data)
        return result if call.return_response else None

    hass.services.async_register(DOMAIN, SERVICE, handle, schema=vol.Schema({
        vol.Required("enabled"): bool,
        vol.Optional("origin"): str,
        vol.Optional("confirm_shared_origin", default=False): bool,
    }), supports_response=SupportsResponse.OPTIONAL)

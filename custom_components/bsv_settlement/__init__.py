"""Mock settlement and isolated, broadcast-disabled embedded operator wallet."""
from pathlib import Path
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.importlib import async_import_module

from .api import WalletAPI
from .const import DOMAIN, SERVICES, SESSION_REVIEW_SERVICES, BUDGET_SERVICES, COLLECTION_RECOVERY_SERVICES, CLOSURE_SERVICES, CREDIT_RECOVERY_SERVICES
from .coordinator import SettlementCoordinator

PLATFORMS = [Platform.SENSOR, Platform.TEXT]


async def async_setup(hass, config):
    hass.data.setdefault(DOMAIN, {})
    if getattr(hass, "http", None) is not None and not hass.data.get(DOMAIN + "_frontend"):
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([StaticPathConfig(
            "/bsv_settlement/operator-card.js",
            str(Path(__file__).parent / "frontend" / "operator-card.js"),
            cache_headers=False), StaticPathConfig(
            "/bsv_settlement/session-review-card.js",
            str(Path(__file__).parent / "frontend" / "session-review-card.js"),
            cache_headers=False), StaticPathConfig(
            "/bsv_settlement/driver",
            str(Path(__file__).parent / "frontend" / "driver"),
            cache_headers=False), StaticPathConfig(
            "/bsv_settlement/budget-card.js",
            str(Path(__file__).parent / "frontend" / "budget-card.js"),
            cache_headers=False)])
        hass.data[DOMAIN + "_frontend"] = True
        from .driver_http import DriverBudgetView
        hass.http.register_view(DriverBudgetView(hass))
        from .portal import DriverPortalView
        hass.http.register_view(DriverPortalView(hass))
        from .grouped_wallet_test import install as install_grouped_wallet_test
        await install_grouped_wallet_test(hass)
        from homeassistant.const import EVENT_HOMEASSISTANT_STOP
        from .pairing import KEY, PairingHub, PairingDiscoveryView, PairingSocketView
        hub = hass.data[KEY] = PairingHub(hass)
        hass.http.register_view(PairingDiscoveryView(hass))
        hass.http.register_view(PairingSocketView(hass))
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, hub.close_all)
    common = {vol.Required("config_entry_id"): str}
    per_session = {**common, vol.Required("session_id"): vol.All(str, vol.Length(min=1, max=200))}
    schemas = {
        "get_credit_receipt_link": {
            **common, vol.Required("credit_id"): vol.All(str, vol.Length(min=1, max=300)),
            vol.Required("expected_txid"): vol.Match(r"^[0-9a-f]{64}$"),
            vol.Required("expected_recipient_address"): str,
            vol.Required("confirm_private_link_disclosure"): vol.In([True]),
        },
        "prepare_operator_credit_recovery": {**common, vol.Required("credit_id"): str,
            vol.Optional("parent_credit_id"): str,
            vol.Optional("replace_expired_review_hash"): str},
        "broadcast_operator_credit_recovery": {
            **common, vol.Required("credit_id"): str,
            vol.Required("expected_review_hash"): str,
            vol.Required("recipient_address"): str,
            vol.Required("amount_sats"): vol.All(int, vol.Range(min=1, max=999)),
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1)),
            vol.Required("confirm_mainnet_payment"): vol.In([True])},
        "prepare_collection_recovery": {**common, vol.Required("budget_id"): str},
        "inspect_driver_collection": {
            **common, vol.Required("budget_id"): vol.All(str, vol.Length(min=1, max=200))},
        "get_reviewed_collection_link": {
            **common, vol.Required("budget_id"): str,
            vol.Required("expected_quote_hash"): str,
            vol.Required("confirm_private_link_disclosure"): vol.In([True]),
        },
        "recover_driver_collection": {
            **common, vol.Required("budget_id"): str,
            vol.Required("expected_quote_hash"): str, vol.Required("expected_claimed_at"): str,
            vol.Required("evidence_reference"): str,
            vol.Required("confirm_driver_wallet_checked"): vol.In([True]),
            vol.Required("confirm_recipient_history_checked"): vol.In([True]),
            vol.Required("confirm_unsigned_draft_cancelled_or_absent"): vol.In([True]),
            vol.Required("confirm_old_driver_pages_closed"): vol.In([True]),
        },
        "configure_automatic_credit": {**common, vol.Required("enabled"): bool},
        "configure_ongoing_credit": {**common, vol.Required("enabled"): bool,
            vol.Optional("proxy_config_entry_id"): str, vol.Optional("conversion_rate_entity"): str,
            vol.Optional("initial_session_id"): str, vol.Optional("expected_budget_id"): str,
            vol.Optional("expected_recipient_address"): str,
            vol.Optional("confirm_ongoing_mainnet_credits"): bool},
        "bind_session": {**per_session, vol.Required("started_at"): str,
                         vol.Required("driver_binding_id", default="driver-demo-01"): vol.In(
                             ["driver-demo-01", "driver-external"])},
        "add_interval": {**per_session, vol.Required("interval"): dict},
        "prepare_session": {**per_session, vol.Required("ended_at"): str,
                            vol.Required("final_import_wh"): vol.All(int, vol.Range(min=0)),
                            vol.Required("final_export_wh"): vol.All(int, vol.Range(min=0))},
        "request_payment": per_session,
        "refresh": common,
        "wallet_status": common,
        "wallet_self_test": common,
        "wallet_refresh_chain": common,
        "prepare_operator_payment": {
            **common, vol.Required("reference"): vol.All(str, vol.Length(min=6, max=100)),
            vol.Required("amount_sats"): vol.All(int, vol.Range(min=1, max=100000)),
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000)),
        },
        "broadcast_operator_payment": {
            **common, vol.Required("draft_id"): str,
            vol.Required("recipient_address"): str,
            vol.Required("amount_sats"): vol.All(int, vol.Range(min=1, max=100000)),
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000)),
            vol.Required("confirm_mainnet_payment"): vol.In([True]),
        },
        "cancel_operator_payment": {**common, vol.Required("draft_id"): str},
    }
    review = {**common, vol.Required("review_id"): str}
    hashed = {**review, vol.Required("terms_hash"): str}
    exact = {
        **hashed, vol.Required("recipient_address"): vol.Any(str, None),
        vol.Required("amount_sats"): vol.All(int, vol.Range(min=0, max=100000)),
    }
    schemas.update({
        "create_session_budget": {
            **common, vol.Optional("session_id"): vol.All(str,vol.Length(min=1,max=200)),
            vol.Required("proxy_config_entry_id"): str,
            vol.Required("conversion_rate_entity"): str,
            vol.Optional("operator_name",default="Charging operator"): vol.All(str,vol.Length(max=100)),
            vol.Optional("operator_contact",default=""): vol.All(str,vol.Length(max=200)),
            vol.Optional("max_total_sats",default=1000): vol.All(int, vol.Range(min=1, max=100000)),
            vol.Optional("max_fee_sats",default=1000): vol.All(int, vol.Range(min=0, max=1000)),
            vol.Optional("replace_pending_budget_id"): str,
            vol.Optional("expected_invitation_hash"): str,
            vol.Optional("confirm_replace_pending"): vol.In([True]),
            vol.Optional("multi_session",default=False): bool,
            vol.Optional("initial_session_id"): vol.All(str,vol.Length(min=1,max=200)),
            vol.Optional("valid_minutes"): vol.All(int, vol.Range(min=1, max=10080))},
        "bind_session_budget": {
            **common, vol.Required("budget_id"): str, vol.Required("session_id"): str,
            vol.Required("confirm_driver_present"): vol.In([True])},
        "accept_session_budget": {
            **common, vol.Required("budget_id"): str, vol.Required("receipt"): dict},
        "revoke_session_budget": {**common, vol.Required("budget_id"): str},
        "session_budget_status": {**common, vol.Optional("budget_id"): str},
        "open_public_registration": {
            **common, vol.Required("budget_id"): str,
            vol.Required("expected_invitation_hash"): str,
            vol.Required("expected_context_hash"): str,
            vol.Required("confirm_public_registration"): vol.In([True])},
        "close_public_registration": {
            **common, vol.Required("budget_id"): str,
            vol.Required("expected_invitation_hash"): str,
            vol.Required("confirm_public_registration"): vol.In([True])},
        "prepare_session_review": {
            **per_session, vol.Required("proxy_config_entry_id"): str,
            vol.Required("conversion_rate_entity"): str},
        "prepare_energy_adjustment": {
            **common, vol.Required("proxy_config_entry_id"): str,
            vol.Required("conversion_rate_entity"): str,
            vol.Required("energy_direction"): vol.In(["import", "export"]),
            vol.Required("request_id"): str},
        "prepare_adjustment_credit": hashed,
        "pay_energy_adjustment": {
            **common, vol.Required("proxy_config_entry_id"): str,
            vol.Required("conversion_rate_entity"): str,
            vol.Required("energy_direction"): vol.In(["import", "export"]),
            vol.Required("request_id"): str,
            vol.Required("confirm_mainnet_payment"): vol.In([True])},
        "approve_session_review": {
            **exact, vol.Required("confirm_account_review"): vol.In([True]),
            vol.Required("confirm_driver_details"): vol.In([True])},
        "prepare_session_credit": {
            **hashed, vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000))},
        "broadcast_session_credit": {
            **exact, vol.Required("draft_id"): str,
            vol.Required("fee_sats"): vol.All(int, vol.Range(min=1, max=1000)),
            vol.Required("confirm_mainnet_payment"): vol.In([True])},
        "verify_session_driver_payment": {
            **review, vol.Required("txid"): vol.Match(r"^[0-9a-f]{64}$"),
            vol.Required("output_index"): vol.All(int, vol.Range(min=0)),
            vol.Required("confirm_driver_payment_reference"): vol.In([True])},
        "cancel_session_review": review,
        "session_review_status": {**common, vol.Optional("review_id"): str,
                                  vol.Optional("include_tariff_provenance"): bool},
    })

    closure_base = {**common, vol.Required("session_id"): str,
                    vol.Required("proxy_config_entry_id"): str,
                    vol.Required("conversion_rate_entity"): str}
    closure_confirm = {**closure_base, vol.Required("expected_review_hash"): str,
                       vol.Required("reason"): vol.All(str, vol.Length(min=8, max=300)),
                       vol.Required("confirm_account_review"): vol.In([True]),
                       vol.Optional("confirm_provisional_metering", default=False): bool}
    schemas["prepare_session_closure"] = closure_base
    schemas["waive_session_charge"] = {**closure_confirm, vol.Required("confirm_no_payment"): vol.In([True])}
    schemas["request_closed_session_consent"] = {
        **closure_confirm,
        **{k: v for k, v in schemas["create_session_budget"].items()
           if str(k) in ("operator_name", "operator_contact", "max_total_sats", "max_fee_sats", "valid_minutes")},
        vol.Optional("confirm_replace_pending", default=False): bool}
    owned_waiver = {**common, vol.Optional("review_id"): str, vol.Optional("budget_id"): str,
                   vol.Optional("received_txid"): vol.Match(r"^[0-9a-f]{64}$"),
                   vol.Optional("received_output_index"): vol.All(int, vol.Range(min=0))}
    schemas["prepare_existing_charge_waiver"] = owned_waiver
    schemas["waive_existing_charge"] = {
        **owned_waiver, vol.Required("expected_review_hash"): str,
        vol.Required("expected_amount_sats"): vol.All(int, vol.Range(min=1, max=100000)),
        vol.Required("reason"): vol.All(str, vol.Length(min=8, max=300)),
        vol.Required("confirm_waive_charge"): vol.In([True]),
        vol.Required("confirm_no_refund"): vol.In([True]),
        vol.Required("confirm_external_payments_need_separate_accounting"): vol.In([True]),
        vol.Optional("confirm_received_funds_unallocated", default=False): bool}

    async def handle(call):
        if call.service in ("get_credit_receipt_link", "prepare_operator_payment", "broadcast_operator_payment",
                            "configure_automatic_credit", "configure_ongoing_credit",
                            "cancel_operator_payment", "wallet_refresh_chain", *SESSION_REVIEW_SERVICES,
                            *BUDGET_SERVICES, *COLLECTION_RECOVERY_SERVICES, *CLOSURE_SERVICES,
                            *CREDIT_RECOVERY_SERVICES):
            # Unlike the generic admin wrapper, refuse context-free automation.
            user = (await hass.auth.async_get_user(call.context.user_id)
                    if call.context.user_id else None)
            if user is None or not user.is_admin:
                raise HomeAssistantError("An authenticated HA administrator must perform this wallet action")
        coordinator = hass.data[DOMAIN].get(call.data["config_entry_id"])
        if coordinator is None:
            raise HomeAssistantError("The selected BSV config entry is not loaded")
        result = await coordinator.execute(call.service, call.data, call.context.user_id)
        return result if call.return_response else None

    for name in SERVICES:
        hass.services.async_register(DOMAIN, name, handle, schema=vol.Schema(schemas[name]),
                                     supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass, entry):
    if entry.data.get("backend") == "ocpp_import_shadow":
        from .ocpp_shadow import OCPPShadowCoordinator
        coordinator = OCPPShadowCoordinator(hass, entry)
        try:
            await coordinator.load()
            await coordinator.async_config_entry_first_refresh()
            hass.data[DOMAIN][entry.entry_id] = coordinator
            await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
        except Exception:
            # Do not overwrite a rejected/unknown store during failed loading.
            if coordinator.cancel_listener:
                await coordinator.close()
            hass.data[DOMAIN].pop(entry.entry_id, None)
            raise
        return True
    if entry.data.get("backend") == "sensor_proxy":
        from .proxy import ProxyCoordinator
        coordinator = ProxyCoordinator(hass, entry)
        await coordinator.load()
        try:
            await coordinator.async_config_entry_first_refresh()
            hass.data[DOMAIN][entry.entry_id] = coordinator
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except Exception:
            await coordinator.close()
            hass.data[DOMAIN].pop(entry.entry_id, None)
            raise
        return True
    if entry.data.get("backend") in ("embedded_testnet", "embedded_mainnet"):
        from .api import WalletError
        is_mainnet = entry.data["backend"] == "embedded_mainnet"
        module = await async_import_module(
            hass, f"custom_components.{DOMAIN}.{'mainnet' if is_mainnet else 'embedded'}")
        api = (module.MainnetWalletAPI if is_mainnet else module.EmbeddedWalletAPI)(hass, entry)
        try:
            await api.load()
        except WalletError as exc:
            raise ConfigEntryNotReady(str(exc)) from None
    else:
        api = WalletAPI(async_get_clientsession(hass), entry.data["service_url"], entry.data["api_token"])
    coordinator = SettlementCoordinator(hass, entry, api)
    await coordinator.load()
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    platforms = [Platform.SENSOR] if entry.data.get("backend") == "ocpp_import_shadow" else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        from .pairing import KEY
        hub = hass.data.get(KEY)
        if hub:
            for topic, item in list(hub.sessions.items()):
                if item["coord"] is coordinator:
                    await hub.close(topic)
        if coordinator is not None and coordinator.mode in ("sensor_proxy", "ocpp_import_shadow"):
            await coordinator.close()
    return unloaded

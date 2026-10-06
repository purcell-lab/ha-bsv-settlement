"""Constants."""
DOMAIN = "bsv_settlement"
CREDIT_RECOVERY_SERVICES = ("prepare_operator_credit_recovery", "broadcast_operator_credit_recovery")
CLOSURE_SERVICES = ("prepare_session_closure", "request_closed_session_consent", "waive_session_charge",
                    "prepare_existing_charge_waiver", "waive_existing_charge")
BUDGET_SERVICES = ("create_session_budget", "accept_session_budget",
                   "revoke_session_budget", "session_budget_status", "bind_session_budget",
                   "open_public_registration", "close_public_registration")
SESSION_REVIEW_SERVICES = (
    "prepare_energy_adjustment", "prepare_adjustment_credit", "pay_energy_adjustment",
    "prepare_session_review", "approve_session_review", "prepare_session_credit",
    "broadcast_session_credit", "verify_session_driver_payment",
    "cancel_session_review", "session_review_status",
)
COLLECTION_RECOVERY_SERVICES = ("prepare_collection_recovery", "recover_driver_collection",
                                "get_reviewed_collection_link", "inspect_driver_collection")
SERVICES = ("get_credit_receipt_link", "bind_session", "add_interval", "prepare_session", "request_payment", "refresh",
            "configure_automatic_credit", "configure_ongoing_credit",
            "wallet_status", "wallet_self_test", "wallet_refresh_chain",
            "prepare_operator_payment", "broadcast_operator_payment", "cancel_operator_payment",
            *SESSION_REVIEW_SERVICES, *BUDGET_SERVICES, *COLLECTION_RECOVERY_SERVICES,
            *CLOSURE_SERVICES, *CREDIT_RECOVERY_SERVICES)

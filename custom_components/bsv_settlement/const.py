"""Constants."""
DOMAIN = "bsv_settlement"
BUDGET_SERVICES = ("create_session_budget", "accept_session_budget",
                   "revoke_session_budget", "session_budget_status", "bind_session_budget")
SESSION_REVIEW_SERVICES = (
    "prepare_session_review", "approve_session_review", "prepare_session_credit",
    "broadcast_session_credit", "verify_session_driver_payment",
    "cancel_session_review", "session_review_status",
)
COLLECTION_RECOVERY_SERVICES = ("prepare_collection_recovery", "recover_driver_collection",
                                "get_reviewed_collection_link")
SERVICES = ("bind_session", "add_interval", "prepare_session", "request_payment", "refresh",
            "configure_automatic_credit", "configure_ongoing_credit",
            "wallet_status", "wallet_self_test", "wallet_refresh_chain",
            "prepare_operator_payment", "broadcast_operator_payment", "cancel_operator_payment",
            *SESSION_REVIEW_SERVICES, *BUDGET_SERVICES, *COLLECTION_RECOVERY_SERVICES)

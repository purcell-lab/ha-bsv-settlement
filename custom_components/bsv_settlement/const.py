"""Constants."""
DOMAIN = "bsv_settlement"
SESSION_REVIEW_SERVICES = (
    "prepare_session_review", "approve_session_review", "prepare_session_credit",
    "broadcast_session_credit", "verify_session_driver_payment",
    "cancel_session_review", "session_review_status",
)
SERVICES = ("bind_session", "add_interval", "prepare_session", "request_payment", "refresh",
            "wallet_status", "wallet_self_test", "wallet_refresh_chain",
            "prepare_operator_payment", "broadcast_operator_payment", "cancel_operator_payment",
            *SESSION_REVIEW_SERVICES)

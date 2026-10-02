# BSV Wallet Mock add-on

This app runs only the mock settlement API. It does not control a charger, hold a real wallet or move real funds.

## Setup

1. Set `api_token` and `approval_token` to distinct random strings of at least 24 characters.
2. Start the app. It stores its SQLite database under persistent `/data`.
3. Configure the HACS integration with `http://<app-hostname>:8091` and the API token.
4. Keep the approval token outside the integration; it is used only to simulate payer decisions.

The app hostname is shown by Supervisor. Use the actual installed hostname, not `localhost`.

The host port is disabled by default. Home Assistant and management tools can reach the app on the internal Supervisor network; no reverse proxy is required. Do not expose this HTTP mock API to an untrusted network.

## Recovery

Stopping or restarting the app preserves the ledger database. Back up the app before uninstalling it, because uninstalling can remove its persistent data.

Source is pinned to the audited service commit in the Dockerfile. The add-on is a separate distribution component from the HACS integration; updating one does not automatically update the other.

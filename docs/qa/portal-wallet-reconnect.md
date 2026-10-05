# Portal wallet status and reconnect

## Scope and QA inventory

- Restored authenticated history must show an unverified wallet connection and a visible reconnect action above session details. It must not initiate wallet discovery without a user gesture or an available injected connection.
- Explicit reconnect checks the same wallet identity and mainnet, then resumes existing confirmed-credit receipt sync without selecting a session.
- Connection checking disables repeat clicks and has a 20-second timeout. Identity mismatch, network mismatch, permission refusal and expired private access must not become connected.
- A successful connection is distinct from successful receipt acceptance. Reporting failure must retain the import cache, show receipt sync paused, and retry reporting without another import.
- Reload, sign-out and pairing disconnect must not preserve a false connected status. Returning to a visible page reclassifies an idle connection as unverified until checked again.
- Inspect mobile and desktop layouts, light and dark modes, primary-action visibility and horizontal overflow.

All interactive checks use fictional offline fixtures. No live wallet sign-in, permissions, receipt import, spending approval, payment or Home Assistant deployment is authorised by this PR.

## Results

- Driver suite: 134 tests passed, including nine new status/connection tests.
- Restored history without an injected wallet: visible unverified status and reconnect button; zero receipt imports or acknowledgements before the click.
- Reconnect with the same mainnet wallet: one import and one acknowledgement, followed by “Wallet connected” and “Credit received”. The receipt scan covered both pages of the fixture's history.
- Repeating reconnect after success did not import or acknowledge again.
- Wrong-network fixture: “Wallet not connected”; zero imports and acknowledgements. Identity mismatch, refusal, private-access changes, timeout and late-response cancellation also covered by unit tests.
- Interrupted acknowledgement: connection remained separately identified, retry action was visible, and retry resulted in one total import and two acknowledgement attempts.
- Reload restored history but did not claim a verified connection. Sign-out hid the private account.
- Fresh sign-in automatically connected and received the fixture credit without a second click.
- Inspected 390-pixel mobile and 1,365-pixel desktop screenshots, including light and dark themes. No horizontal overflow; status and action appear before the latest session.

These checks establish mock-browser behaviour, not acceptance by the live BSV Browser application. Production deployment and a driver-led wallet test remain separate.

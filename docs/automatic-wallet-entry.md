# Automatic wallet entry and settlement

This change replaces the normal portal Sign in button with automatic, serial wallet setup on a visible, top-level page. Identity proof, an available operator-issued budget signature, receiving registration and settlement remain separate cryptographic operations governed by wallet-native permissions.

## Behaviour

- **Initial visit:** load and display the exact public invitation, then request fresh wallet identity proof. A history cookie alone never restores another wallet's private data or enables payment.
- **Existing driver:** reuse the signed approval without increasing or renewing it. Receive eligible confirmed credit receipts and collect eligible completed-session charges automatically.
- **New driver:** request the exact operator-issued budget through the wallet, then register its credit destination. Do not substitute another invitation if the scanned invitation changes or expires.
- **Authenticated new-wallet offer:** when no usable weekly approval exists, the backend derives a private offer from the enabled operator-credit site's signed weekly template. The total is at most 1,000 sat including fees, never above the template, and the duration is at most seven days. Client-selected limits, addresses and session IDs are ignored. No monthly grant is created.
- **Current/unassigned session:** the private offer may explicitly include the latest retained session if it has no prior owner, route, collection, review or closure. The driver signs its exact scope through the wallet before receiving registration assigns the route. An offer alone does not assign or pay. Arbitrary older history is not silently adopted.
- **Previously assigned session:** retain the original wallet, including when a provider submission is uncertain. Existing weekly coverage counts as ownership even if the background route has not yet been materialised.
- **Wallet transport:** use the existing injected/SDK transport. A definite missing transport opens BSV Browser QR pairing. A wallet rejection must not trigger a different transport.
- **Paired wallet:** a successful wallet scan continues setup without another portal sign-in button.
- **Expiry:** request fresh identity proof when private access expires. Do not renew the monetary budget.
- **Refusal or uncertain result:** pause rather than repeatedly opening wallet prompts. Retry wallet connection is an exception control, not a normal journey step.
- **Sign out or pause:** stop automatic entry in the current page. Do not silently sign the driver back in on a poll or visibility event.
- **Financial safety:** retain budget/fee checks, recipient binding, per-session deduplication, held-payment refusal and existing receipt acceptance evidence. No monthly authority is enabled.

Wallet prompts are not bypassed, and their number remains wallet-dependent. A closed or disconnected browser does not provide offline debit authority. Receipt acceptance imports an existing payment; it does not broadcast another credit.

## Wallet status metadata

The collapsible metadata section shows the verified identity public key, identity verification time, connection method, wallet-reported network, connection check time, private-access expiry, application origin and exposed API methods. Method availability is not evidence of granted spending permission.

Addresses are authenticated, owner-scoped evidence grouped by approval. Receiving addresses identify operator credits to the driver; payment addresses identify charges to the operator. Receiving proofs are checked before an address is labelled verified. Missing or invalid proof produces “Not verified”. Identity public keys are not presented as payment addresses, and wallet funding/change addresses are not guessed. No capability token, signing key, private link or raw proof is exposed.

## QA inventory

| Claim / interaction | Required check |
|---|---|
| No normal Sign in button | Load mobile and desktop; automatic verified state without clicking |
| Existing approval | Proof followed by receipt/debit polling; no new budget signature |
| New registration | One saved budget and receiving registration; no page handoff |
| Credit receipts | Automatically imported and acknowledged; no duplicate credit |
| Debit jobs | Automatic discovery; held jobs never claimed |
| Native refusal | One attempt only; repeated visibility/polls cannot reopen prompts |
| Pause during prompt | Late response cannot advance setup |
| Private-access expiry | New identity proof without budget renewal |
| Cookie present | Fresh live wallet proof still required |
| Network mismatch | No connected state, receipt import or debit claim |
| Connected-wallet routing | Private current-plus-future offer; limits inherited from operator; signature and receiving proof required; prior owners untouched |
| Assignment race | Another owner or registration appearing during the wallet prompt blocks acceptance |
| QR connection | No automatic fallback on refusal; pairing continues setup |
| Sign out | Private data cleared; no automatic sign-in until explicit retry/new visit |
| Metadata | Full keys/addresses wrap on mobile; verified owner-only fields; no secrets |
| Disclosures/theme | History and metadata expand/collapse; light/dark, no horizontal overflow |
| Real wallet | Separate native-wallet acceptance test; no real funds used in offline QA |

## Protocol reference

The wallet API exposes permissioned identity, signing, action and receipt operations; it leaves permission decisions with the wallet. See [Project Babbage's wallet integration guidance](https://docs.projectbabbage.com/docs/quickstarts/getting-a-wallet) and [BRC-116 wallet permissions](https://bsv.brc.dev/wallet/0116). This change does not assume that a wallet implements grouped permissions or grants unattended spending.

## Validation evidence

Local validation on 7 October 2026 used fictional wallets and a network-disabled preview. The full Python suite passed 1,604 tests before the final template-binding and expired-registration regressions were added. The final focused backend run passed 100 tests, and the frontend run passed all 219 tests. The production bundle rebuilt successfully; GitHub CI must validate the final commit before merge.

Desktop (1280 px) and mobile (375 px) checks covered automatic entry, new registration, existing approval, automatic receipt import and acknowledgement, debit-job polling, an uncertain held job without a claim, rejection without repeated prompts, explicit retry, pause during a pending signature, network mismatch, sign out, identity renewal and fresh proof with an existing cookie. Without a wallet, the cookie did not expose private history. History and metadata disclosures, light/dark presentation and address wrapping were checked; no JavaScript page errors were observed.

These checks are not native-wallet acceptance or live payment evidence. No Home Assistant deployment, wallet prompt, real credit, real debit or historical recovery was performed. Native BSV Browser acceptance and production verification remain deployment gates.

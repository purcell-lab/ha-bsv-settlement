# BSV wallets for OCPP charging and credit

> SUPERSEDED: the initial prepaid/refund recommendation below is not the current design. The current proof of concept has no top-up, budget process or refund cycle. See [current interface](../settlement-interface.md).

Technical exploration for Mark Purcell | 2 October 2026

## Recommendation

Proceed with a small, optional **wallet-funded charging and refund demonstration**, separate from the core CSIP-AUS-to-OCPP interoperability demonstration. Treat BSV as a payment method, not as the charging controller, tariff authority, meter, or credit provider.

The proposed architecture is consistent with an established OCA pattern: payment occurs outside OCPP, and the charging management system starts the session and manages its spending limit. OCA documents that pattern for UPI mobile payments; applying it to BSV is an engineering proposal, not an OCA-endorsed BSV integration. ([OCA mobile-payment whitepaper](https://openchargealliance.org/wp-content/uploads/2025/11/OCA-Whitepaper-OCPP-UPI-mobile-payments.pdf))

Start with prepaid funds and refunds. Add a separately funded export/flexibility reward demonstration next; defer real consumer lending. This exploration has not created a wallet, connected to the supplied site's wallet interface, moved funds, installed its software, or validated an end-to-end charger/payment implementation.

## What the supplied link contributes

The linked “Wallet Challenges” page invites users to connect a BSV wallet and claim rewards in satoshis for wallet-executed challenges. It links to a self-hosted wallet and supporting payment, authentication, messaging and storage projects; it does not describe an OCPP product or charging credit facility. ([Wallet Challenges](https://todriguez.com/cfb/))

The useful building blocks are:

- **BRC-100 wallet interface:** Provides transaction creation, signing, incoming-payment internalisation and transaction/output queries. Applications can use `createAction` and `internalizeAction` without holding the user's private keys themselves. ([BRC-100](https://hub.bsvblockchain.org/brc/wallet/0100))
- **BRC-29 payments:** Defines recipient-derived payment outputs and the remittance information needed to receive them, using BEEF transaction/proof data. It does not define charging tariffs, session balances or lending. ([BRC-29](https://bsv.brc.dev/payments/0029))
- **HTTP payment middleware:** Can issue `402 Payment Required` and accept a paid retry. This can gate a top-up API, but payment of an HTTP request does not establish that a charger started or delivered energy. ([Express payment middleware](https://github.com/bitcoin-sv/payment-express-middleware))
- **Self-hosted operator wallet:** `bsv-wallet-cli` documents a Rust wallet server, local SQLite storage, transaction monitoring and BRC-100 endpoints. It is a candidate for evaluation, not an audited or commissioned component of your stack. ([Wallet repository](https://github.com/Calhooon/bsv-wallet-cli))

The Cloudflare middleware also documents payment verification, optional partial refunds and persisted replay checks. Its documentation explicitly identifies residual replay concerns associated with eventually consistent KV storage; Cloudflare itself states that KV is not suitable for operations requiring atomic transactions. ([Middleware repository](https://github.com/Calhooon/bsv-middleware-cloudflare), [Cloudflare KV consistency](https://developers.cloudflare.com/kv/concepts/how-kv-works/))

**Design consequence:** Keep the authoritative charging balance, payment-consumption record and session reservation in an atomic transactional store. Do not use an ordinary KV read-modify-write counter as the money ledger.

## What “credit” could mean

The following are proposed product choices, not equivalent functions of a wallet.

| Model | What happens | Recommendation |
|---|---|---|
| Prepaid charging balance | Driver pays first; the operator records a liability for unused charging funds. | First prototype. Prefer session-specific funding to a general-purpose stored-value account. |
| Refund | Unused prepayment is returned after the session's final bill is reconciled. | Include from the outset, including failed starts. |
| Earned credit | An identified operator or sponsor pays for verified export or a contracted flexibility service. | Second stage; name the payer and verification rules. |
| Fleet/postpaid account | Operator or employer permits charging before collecting payment. | Separate credit policy, exposure limits and settlement arrangement. |
| Consumer lending | A lender finances charging and later recovers a debt. | Not part of the Plugfest prototype; obtain specialist advice and a suitable licensed partner. |

A wallet signature proves control of a key, not identity, creditworthiness, delivery of electricity or entitlement to a network-service payment. A token saying “AUD 10 credit” is only as useful as the issuer's enforceable promise and ability to redeem it; no new token is needed for the proposed prototype.

## Proposed architecture

```text
Driver wallet
     |
     | signed quote acceptance + BSV payment
     v
Payment adapter ---- Operator wallet / transaction monitor
     |
     | verified funding event, unique payment reference
     v
Session ledger and tariff engine
     |
     | reserve budget; authorise start; request stop
     v
Home Assistant / OCPP CSMS <------> Charging station
     ^                                  |
     |                                  | measured transaction events
     +----------- Metering adapter <----+

DSO / SEP2 test server --> ODE --> Home Assistant / HAEO
                                      |
                                      +--> permitted charging schedule
```

The commercial service decides whether there is sufficient funding to authorise energy delivery. The HEMS and charger still enforce device constraints, site limits and the CSIP-AUS envelope. A payment must never authorise bypassing those constraints.

For your direct Home Assistant integration, add a separate local service rather than another OCPP server competing for the charger connection. Keep the wallet signing service isolated from ordinary Home Assistant automations and browser code, with narrow permissions and a small funded balance.

### Session lifecycle

1. **Quote:** Driver scans a QR code identifying a specific station/EVSE. The application returns an expiring quote with tariff version, currency, maximum spend, refund terms and unique session-intent ID.
2. **Fund:** Driver explicitly approves a BSV payment. The server verifies the intended recipient, amount, network, transaction/output and invoice binding; it does not trust a client-side success flag.
3. **Reserve:** Atomically record the payment once and reserve funds for that session. Concurrent sessions cannot each spend the same balance.
4. **Start:** Issue the OCPP start request and await the actual transaction-start event. A command acknowledgement is not evidence that energy flowed.
5. **Meter:** Accumulate validated meter increments, apply the agreed tariff and persist the running balance. HAEO's forecast or power setpoint is not billing evidence.
6. **Limit:** Request a top-up or stop before the funded allowance is exhausted. Prefer a charger-enforced budget where the actual charger and CSMS both support it.
7. **Reconcile:** Use the final transaction evidence, resolve delayed readings and calculate the final charge.
8. **Refund or pay:** Return unused funds, or pay a verified export reward, through a separately recorded outbound transaction. A refund is a new payment, not cancellation of the original transfer.

Proposed state machine:

```text
QUOTED → FUNDING_PENDING → FUNDED → START_REQUESTED → ACTIVE
       → STOP_REQUESTED → RECONCILING → SETTLED
                                     → REFUND_PENDING → REFUNDED
```

Include explicit failed-start, payment-rejected and disputed-meter states. Retrying a request must return the existing result rather than create another payment, start command or refund.

## OCPP fit and gaps in the current stack

| Version | Available approach | Important boundary |
|---|---|---|
| OCPP 1.6 | Payment outside OCPP; `RemoteStartTransaction`, transaction/meter reporting and `RemoteStopTransaction`. | Backend monitoring introduces an overshoot window; bespoke extensions are not portable by default. |
| OCPP 2.0.1 | Payment outside OCPP; `RequestStartTransaction`, `TransactionEvent` and `RequestStopTransaction`. | Do not assume standard native monetary-limit support. |
| OCPP 2.1 | Adds explicit prepaid/ad hoc payment features and transaction limits by cost, energy, time or SoC. | Cost enforcement requires local tariff calculation; support must be established in both endpoints. |

These distinctions follow the OCA's mobile-payment guidance and OCPP 2.1 overview. OCA discusses `customData` or `DataTransfer` approaches for earlier versions, but those require a mutually implemented extension. ([OCA mobile-payment whitepaper](https://openchargealliance.org/wp-content/uploads/2025/11/OCA-Whitepaper-OCPP-UPI-mobile-payments.pdf), [OCPP 2.1 overview](https://openchargealliance.org/protocols/ocpp-protocols/ocpp-2-1/))

For 2.x, use `TransactionEvent` terminology rather than carrying over 1.6's `StartTransaction` wording. The detailed 2.1 documentation places a prepaid `transactionLimit.maxCost` in the response to the started transaction event; exact payloads, units and behaviours should be tested against the selected version's normative schemas. ([OCPP 2.1 authorization reference](https://tzi.app/developers/ocpp/2.1/authorization))

The inspected Home Assistant integration exposes session start/stop and charging-rate controls. Its 2.x remote-start path uses a configured central token and `remote_start_id=1`; the inspected authorization and transaction-event handlers do not demonstrate a payment-balance check or native monetary-limit enforcement. This is source inspection, not a test of your installed version. ([Integration API](https://github.com/lbbrhzn/ocpp/blob/main/custom_components/ocpp/api.py), [2.x implementation](https://github.com/lbbrhzn/ocpp/blob/main/custom_components/ocpp/ocppv201.py))

Consequently, the prototype needs:

- **Payment-aware admission:** Gate every enabled start path, including RFID, local/free-vend and automatic starts; gating only the app button is insufficient.
- **Reliable correlation:** Persist station, EVSE, connector, intent, actual OCPP transaction and payment identifiers. Serialize starts on an EVSE; consider an upstream change for unique remote-start references.
- **Durable metering:** Capture raw transaction evidence rather than relying only on current Home Assistant sensor values.
- **Budget enforcement:** First prove server-monitored stop with an explicit risk margin. Add native OCPP 2.1 transaction-limit support only after confirming its implementation.

`TxProfile` is a power schedule, not a money balance. Integrating its target power over time is not evidence of actual delivered energy.

## Pricing, settlement and bounded risk

Recommended first design: quote the tariff and budget in AUD, accept BSV at a disclosed rate for each funding event, and retain both AUD accounting and satoshi amounts. Record quote expiry, fees, rounding and who bears exchange-rate movement. This is a proposed accounting policy, not a claim that a suitable conversion provider is already available.

For the first demonstration, use a fixed illustrative tariff. Dynamic tariffs require timestamped pricing intervals and a stated interpolation policy where meter samples straddle a price change; a one-time money-to-kWh conversion is not sufficient for changing prices.

Define refund conversion explicitly. One defensible demonstration policy is returning unused prepaid AUD value at the original funding rate, with no deduction for network fees; the operator then bears the associated fees and currency risk.

### Illustrative example, not live prices

- Prepayment: AUD 10 equivalent in BSV.
- Charging: 6 kWh at AUD 0.40/kWh = AUD 2.40.
- Unused prepayment returned: AUD 7.60 equivalent under the disclosed conversion policy.
- Separate optional export reward: 2 kWh at AUD 0.20/kWh = AUD 0.40, payable only under an agreed, funded export arrangement.

Show refunds and export earnings separately in the receipt even if they are combined into one wallet payout. An export measurement at the EVSE is not necessarily export at the grid connection point, and must not automatically be paid as a retailer feed-in credit or DNSP service.

For a monitored-stop design, a minimum reserve can be estimated as:

\[
R_{\mathrm{AUD}} \geq P_{\max,\mathrm{kW}}
\times \frac{T_{\mathrm{meter+decision+stop},\mathrm{s}}}{3600}
\times p_{\max,\mathrm{AUD/kWh}}
+ R_{\mathrm{other}}
\]

At 22 kW, 90 seconds total delay and AUD 0.40/kWh, energy exposure alone is AUD 0.22. Add tariff changes, other fees and uncertainty; this calculation does not bound an indefinite communications outage.

Distinguish wallet receipt, network acceptance and mined confirmation. BRC-100 exposes unproven/sending/failed states, and the candidate wallet separately tracks network visibility and mined proofs; an API success response is not equivalent to irreversible settlement. ([BRC-100](https://hub.bsvblockchain.org/brc/wallet/0100), [Wallet monitoring](https://github.com/Calhooon/bsv-wallet-cli))

For unconfirmed payments, impose an explicit low-value risk cap and rejection/double-spend policy. Do not claim instant risk-free finality or a specific transaction cost without measuring the selected wallet, broadcaster and funding route.

## Failure, privacy and security requirements

These are proposed acceptance requirements:

| Failure | Required behaviour |
|---|---|
| Payment received, start fails | Preserve liability; reconcile any late start before refunding. |
| Duplicate paid request | One ledger credit and one session start, enforced atomically. |
| Two sessions use one balance | Reserve atomically; reject the second if funds are insufficient. |
| Wallet or internet unavailable | No new unfunded session; continue only within a previously enforceable allowance. |
| OCPP disconnects | Charger-local energy/time cap or tested timeout is needed for bounded exposure; otherwise stop the real-money trial. |
| Meter resets, duplicates or arrives late | Deduplicate, detect discontinuities and reconcile; do not invent billable energy. |
| Home Assistant restarts | Recover session mapping and reservations from durable records. |
| Refund broadcast fails | Retain a refund-pending liability and retry idempotently. |
| DSO envelope tightens | Reduce or stop charging regardless of prepaid balance; refund unused funds. |
| Export reward is claimed twice | Deduplicate by verified metering interval and contracted service. |

Wallet identity must not become a public identifier for a vehicle's charging history. Keep personal information, location traces and granular readings off-chain; use opaque references and disclose what transaction metadata remains linkable.

Your direct OCPP transport and wallet application authentication are separate security domains. Adding a wallet does not secure charger commands or establish an OCPP security profile. Use an isolated demonstration network and do not extend the present test configuration to public commercial charging without a separate security assessment.

## Australian deployment boundary

This is a technical feasibility assessment, not legal advice. Classification depends on the actual parties, contractual rights, funds flow and whether the system merely accepts payment for its own services or operates a broader wallet/payment/credit business.

- **Payments and stored value:** ASIC states that digital-asset wallet arrangements may be non-cash payment facilities, including some non-custodial arrangements. Single-payee and incidental-product exclusions may be relevant, but are not automatic approvals for this design. ([ASIC INFO 225](https://www.asic.gov.au/regulatory-resources/digital-transformation/digital-assets-financial-products-and-services))
- **AML/CTF:** AUSTRAC requires registration for relevant remittance and virtual-asset services, while noting that incidental receipt/payment in another business generally does not require registration. Operating custody, exchange or customer-transfer services needs a separate assessment. ([AUSTRAC registration guidance](https://www.austrac.gov.au/new-austrac/register-us/register-us-remittance-or-virtual-asset-service-provider))
- **Lending:** Providing regulated credit generally requires an Australian credit licence or appropriate authorisation; Australia's BNPL licensing requirements have applied since 10 June 2025. Whether a particular fleet account or deferred charging bill falls within the regime is a legal question, not something avoided by denominating it in BSV. ([ASIC credit licensing](https://www.asic.gov.au/for-finance-professionals/credit-licensees/do-you-need-a-credit-licence), [ASIC BNPL guidance](https://www.asic.gov.au/regulatory-resources/credit/buy-now-pay-later-credit-contracts-credit-licensing))

Before commercial use, also commission advice on GST and crypto accounting, refunds and consumer terms, privacy, metering and the applicable electricity-sale arrangement. Do not represent “testnet”, “open source”, “non-custodial” or small transaction size as a blanket legal exemption.

## Plugfest prototype and decision gates

Keep this as an optional commercial overlay, so wallet availability cannot derail the CSIP-AUS/OCPP demonstration.

1. **Simulation:** Use a fake wallet and deterministic tariffs. Prove session correlation, duplicate handling, budget exhaustion and refund accounting.
2. **Wallet interoperability:** Connect isolated developer wallets on a supported test network. Prove payment construction, receipt validation, transaction-status recovery and return payments.
3. **Controlled charger demonstration:** Test prepaid start, measured billing, early stop, failed start, unused-balance refund and a tightening network envelope. Use no customer money.
4. **Export/credit illustration:** Simulate a funded export reward and a capped postpaid allowance as distinct ledger entries. Do not call a simulated allowance an available lending product.
5. **Real-value pilot:** Only after security review, legal classification, reconciled accounting, an agreed payment-acceptance policy and measured end-to-end tests.

For a single local demonstration, use one transactional database and one operator wallet. Cloudflare Workers can host the user interface or payment endpoint later, but are not required; if adopted, use a serialized transactional authority for payment consumption and balances, not eventually consistent KV alone.

### Questions to resolve before implementation

- Does “credit” mean prepaid charging funds, payment for export/flexibility, or borrowing?
- Who is the merchant and, separately, who funds export rewards or credit?
- Which exact wallet, version and network will participants use?
- Which charger can enforce a local budget during communications loss?
- Can your integration expose a reliable transaction-event stream and unique session correlation?
- Will the organiser accept a wallet-payment side demonstration without expanding the core interoperability scope?

**Bottom line:** BSV can plausibly supply the payment transport. The substantive engineering is the metered-session ledger, authorisation adapter, failure recovery and accounting; genuine credit adds an underwriting and regulatory workstream. Build prepaid charging plus refunds first, without modifying the existing briefing or promising a live financial product.

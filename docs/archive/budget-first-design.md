# BSV wallets for OCPP charging and credit

> SUPERSEDED: retained for design history only. The current proof of concept removes budget approval and settles payment or credit at session end. See [current interface](../settlement-interface.md).

Revised target design | 2 October 2026

This revision supersedes the prepaid-first recommendation in the earlier feasibility note. The target is **wallet-authorised session budget, metered charging and discharging, dynamic-price calculation in Home Assistant, then settlement of the actual debit or credit to the bound wallet**. No charging balance is topped up with the operator, and there is no routine refund cycle.

## Division of responsibilities

| Component | Responsibility | Must not be confused with |
|---|---|---|
| BSV wallet and budget-authorisation service | Bind wallet identity; obtain spending consent; enforce wallet spending policy; issue a verifiable session authorisation; execute the final debit or receive a credit. | The BSV blockchain itself evaluating electricity tariffs or deciding whether a charger is safe. |
| OCPP integration and charging station | Start, control and end the session; report transaction identity and actual import/export measurements. | Establishing the monetary value or funding an export payment. |
| Home Assistant settlement component | Bind the authorisation to the OCPP transaction, calculate running and final amounts from measured energy and dynamic prices, request settlement and retain a reconciled audit record. | Using a forecast or charging setpoint as a billable measurement. |
| HAEO and the local EMS | Optimise energy use subject to device, site and CSIP-AUS constraints, and the remaining authorised spending allowance. | Overriding network or electrical limits because funds are authorised. |

This is a proposed application architecture, not a capability already established across the selected wallet and charger. OCA's external-payment pattern supports separating payment from session control, while OCPP 2.1 adds transaction limits and local cost-calculation features. ([OCA payment guidance](https://openchargealliance.org/wp-content/uploads/2025/11/OCA-Whitepaper-OCPP-UPI-mobile-payments.pdf), [OCPP 2.1](https://openchargealliance.org/protocols/ocpp-protocols/ocpp-2-1/))

## What last night's demonstration establishes

The linked application's code describes “Give an agent a budget”: a 50,000-satoshi allowance for one session, with the agent checking each service quote against the remaining allowance before asking the wallet to pay. This is evidence of an application-policy budget; the code description does not establish reserved funds, a charging-session mandate or unattended end-of-session settlement. ([Demonstration application](https://todriguez.com/cfb/app.js))

There is also a wallet-level permission mechanism. BRC-116 describes originator-scoped standing spending authorisations in satoshis, tracked by calendar month, as well as one-time approvals and revocation; BRC-73 describes the corresponding manifest declaration. Neither is automatically a one-session, one-merchant monetary hold. ([BRC-116](https://bsv.brc.dev/wallet/0116), [BRC-73](https://bsv.brc.dev/wallet/0073))

**Design consequence:** Combine the selected wallet's permission mechanism with an explicit session mandate. Verify which limits the wallet itself enforces and which are enforced only by the application; do not label an application-side check “wallet-enforced”.

## Target session flow

```text
Driver approves a session budget
             |
             v
Wallet/budget service issues authorisation bound to the session
             |
             v
Home Assistant validates it and asks OCPP to start
             |
             v
OCPP reports measured imports and exports
             |
             v
Home Assistant calculates running cost or credit using dynamic prices
             |
             +--> budget service checks headroom; EMS limits/stops if needed
             |
             v
OCPP session ends; Home Assistant reconciles the final statement
             |
             v
Wallet settles the actual net debit, or operator pays the net credit
```

No payment occurs merely because the driver authorises a budget. Unused spending authority is closed or expires; there is nothing to refund because no prepayment was collected.

The wallet should settle only once for the final statement. Persist the mandate, station/EVSE/connector, actual OCPP transaction ID, meter evidence, price versions, statement hash and settlement transaction reference.

### Sequence diagram

[Open the rendered sequence diagram](https://www.perplexity.ai/computer/a/budget-approval-dynamic-settle-uYTiPzgpR8KrKpsWpxSniQ).

The following is the target application flow, not a claim that the selected wallet already implements a charging-session mandate. Wallet consent and budget approval do not, by themselves, guarantee reserved funds or unattended final signing.

```mermaid
sequenceDiagram
    autonumber
    actor D as Driver
    participant W as Driver wallet / budget service
    participant H as Home Assistant / billing
    participant C as OCPP / charging station
    participant O as Operator wallet

    rect rgb(235,245,245)
    Note over D,O: Budget approval: no funds transferred
    D->>H: Select EVSE and dynamic pricing terms
    H-->>D: Tariff, maximum debit, FX and fee policy
    D->>W: Approve session budget and bind wallet
    W-->>H: Verifiable session authorisation
    Note over W,H: Bind session, merchant, limits and settlement window
    alt Authorisation valid
        H->>C: Request session start
        C-->>H: Started event and actual transaction ID
        H->>W: Bind authorisation to OCPP transaction
    else Denied, expired or invalid
        H-->>D: Do not start; no charge
    end
    end

    rect rgb(245,247,249)
    Note over D,O: Active session: repeated dynamic valuation
    loop Each metered pricing interval
        C-->>H: Timestamped import and export readings
        H->>H: Validate readings and apply interval prices
        H->>W: Running debit exposure plus stop margin
        W-->>H: Remaining authority or stop decision
        opt Budget exhausted or authority withdrawn
            H->>C: Stop or suspend within safe limits
        end
    end
    Note over H,C: HEMS and charger enforce CSIP-AUS and device limits independently
    C-->>H: Ended event and final meter readings
    H->>H: Reconcile and freeze signed cost or credit
    end

    rect rgb(235,245,245)
    Note over D,O: Final settlement: one debit or credit
    alt Net cost C greater than zero
        H->>W: Request actual debit with statement hash
        W->>W: Check mandate, budget, fees and spendability
        W->>O: Sign and submit BSV payment of C
    else Net credit C less than zero
        H->>O: Request funded payout of absolute C
        O->>O: Check payout authority and funds
        O->>W: Sign and submit BSV credit of absolute C
    else Net amount C equals zero
        H->>H: Record zero settlement; no transfer
    end
    Note over W,O: Settle only if the relevant wallet authorises and can pay
    W-->>H: Driver-wallet payment status / reference
    O-->>H: Operator-wallet payment status / reference
    alt Settlement accepted under declared policy
        H->>W: Close unused session authority
        H-->>D: Itemised receipt and settlement status
    else Wallet unavailable, rejected or insufficient funds
        H->>H: Persist settlement pending; no duplicate payment
        H-->>D: Explain pending settlement and recovery
    end
    end
```

Import and export are measured separately; the final amount is netted using the agreed pricing formula. A net credit is paid from the operator wallet into the driver wallet. Unused authority is closed without a refund, and an accepted payment remains distinguishable from mined confirmation.

### Proposed session mandate

The application-level record should bind:

- **Parties:** Driver wallet identity, authorised application origin and receiving operator identity.
- **Session:** Unique intent ID, station and EVSE; attach the actual OCPP transaction ID after start.
- **Allowance:** Maximum debit, denomination, treatment of network fees and a separate satoshi ceiling if the customer-facing budget is AUD.
- **Price consent:** Dynamic import/export pricing formula or identified price feed, markups, other fees, rounding and correction rules.
- **Lifetime:** Start-by time, session end condition, bounded settlement window and cancellation/revocation behaviour.
- **Settlement:** Whether wallet availability or another approval is required; exactly which final statement the payment settles.

These fields are a proposed application contract, not a claim that BRC-100 exposes a native `authoriseChargingSession` method. BRC-100 supplies transaction and wallet operations including `createAction` and `internalizeAction`. ([BRC-100](https://hub.bsvblockchain.org/brc/wallet/0100))

## Dynamic cost and credit

Use a signed amount payable by the driver:

\[
C_{\mathrm{AUD}} =
\sum_i
\left(
\Delta E_{\mathrm{import},i}p_{\mathrm{import},i}
-
\Delta E_{\mathrm{export},i}p_{\mathrm{export},i}
\right)
+ F
\]

Here, interval energy is in kWh, prices are AUD/kWh and \(F\) contains the agreed session, time or other fees. Positive \(C\) is payable by the driver; negative \(C\) is payable by the contracted operator or sponsor to the driver.

This formula can also accommodate negative prices, provided the agreed commercial tariff actually passes them through. Import and export use separate registers and prices; retain gross quantities in the receipt even when settling one net amount.

Illustrative calculation, not live prices:

| Item | Calculation | Amount |
|---|---|---:|
| Driver authorises | Maximum net debit | AUD 10.00 |
| Imported energy, interval A | 4 kWh × AUD 0.20 | AUD 0.80 |
| Imported energy, interval B | 2 kWh × AUD 0.50 | AUD 1.00 |
| Exported energy | 2 kWh × AUD 0.60 | −AUD 1.20 |
| Final driver debit | 0.80 + 1.00 − 1.20 | AUD 0.60 |

Only AUD 0.60 equivalent is transferred at settlement. The remaining AUD 9.40 of authority is unused, not refunded.

Specify the AUD/BSV conversion policy before consent. If settlement-time conversion is chosen, enforce both the AUD session ceiling and any satoshi ceiling; a changed exchange rate must not silently expand the original wallet permission.

OCPP-reported EVSE export is not necessarily export at the grid connection point. The contract must define whether payment is for energy delivered to the site, grid export or a separate flexibility service; the wallet cannot create an entitlement to retailer or DNSP revenue.

## Budget enforcement during charging

Home Assistant calculates the running bill, while the wallet/budget service remains the authority for permitted spending. The EMS must check remaining authority while energy is being delivered, rather than discovering at session end that the final debit exceeds the mandate.

For the initial prototype:

- Maintain an atomic reservation of spending authority for every active session. This prevents two sessions consuming the same permitted budget; it is not necessarily a reservation of spendable coins.
- Deduct accrued debit and a conservative meter/stop-delay margin from the session ceiling.
- Do not count forecast export revenue as spendable credit. Initially, do not let accrued export credits replenish the authorised debit ceiling either; relax that only with explicit consent and tested accounting.
- On revocation or loss of authorisation-service connectivity, stop or continue only within a previously authorised, locally enforceable allowance.
- Do not permit automatic restarting or reconnection to reset the budget.

OCPP 2.1 supports charger-side limits, but monetary enforcement requires local cost calculation. Because this design makes Home Assistant authoritative for dynamic pricing, native cost limits should be used only if the charger receives and correctly implements the same tariff; otherwise use tested conservative energy/time limits and running-bill stop logic. ([OCA payment guidance](https://openchargealliance.org/wp-content/uploads/2025/11/OCA-Whitepaper-OCPP-UPI-mobile-payments.pdf))

## The important financial distinction

**Spending permission is not necessarily reserved money or a payment guarantee.** The documented wallet permission scope does not establish that funds cannot be spent elsewhere or that a disconnected wallet can sign a future payment. ([BRC-116](https://bsv.brc.dev/wallet/0116))

The preferred prototype remains budget-authorised post-session settlement. Before describing it as guaranteed collection, establish one of these arrangements:

- **Permission-only:** Driver retains the funds; operator accepts bounded settlement risk.
- **Wallet reservation:** Wallet prevents reserved funds from being used elsewhere for the life of the session; actual support must be verified.
- **Guaranteed authorisation:** A payment provider guarantees the authorised amount, under an explicit arrangement.

This is distinct from offering the driver a general-purpose lending product. Nevertheless, deferring settlement does not eliminate the operator's exposure if funds become unavailable, consent is revoked or signing fails.

Incoming credits also need a payer. The operator wallet requires its own payout authority and sufficient funds; a credit is an operator-to-driver transaction, not a negative-value driver payment.

## Validation plan

Demonstrate the desired experience first with a simulated wallet:

1. Approve one session budget; start without transferring money.
2. Replay import/export measurements and dynamic price changes.
3. Show Home Assistant's running signed amount and remaining debit headroom.
4. End the OCPP session; freeze the reconciled statement.
5. Execute one debit or one operator-funded credit.
6. Close unused authority without a refund.

Then verify the selected live wallet's behaviour for permission scope, unattended signing, disconnected devices, revocation, concurrent sessions, insufficient funds and failed settlement. Replay the final statement and confirm that it cannot trigger a second payment.

Also test budget exhaustion, network-envelope tightening, charger disconnection, late meter values, price corrections and both Home Assistant and wallet restarts. If safe budget enforcement depends on continuous connectivity, disclose that limitation rather than presenting an untested hard cap.

**Agreed direction:** BSV provides wallet binding, budget authorisation and settlement; OCPP provides the metered charging session; Home Assistant calculates dynamic cost or credit. The remaining investigation is how the selected wallet turns a session mandate into reliable, bounded final settlement without prepayment or routine refunds.

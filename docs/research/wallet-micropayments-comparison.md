# Wallet micropayments for bidirectional EV charging

> Historical research context. This report was prepared against the earlier budget-authorised concept. The current proof of concept explicitly removes budgets, prepayment and charging-start payment gates. Use [the current interface](../settlement-interface.md) and [the repository README](../../README.md) for implementation requirements; the research findings below are retained without rewriting their original conclusions.

Research assessment for a budget-authorised OCPP and Home Assistant demonstration. Evidence reviewed on 2 October 2026.

## Executive assessment

The closest practical precedent is SWTCH’s Canadian work combining bidirectional charging, blockchain accounting and a wallet history of debits and credits; the closest post-payment research is P6V2G, which accumulates charging costs and flexibility rewards in a cryptographic wallet before periodic billing ([SWTCH pilot description](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/); [P6V2G paper](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)). These precedents support the proposed separation of energy delivery from financial accounting, but neither establishes a production implementation of the complete target: a wallet-approved session budget, OCPP import/export telemetry, dynamic Home Assistant pricing and automatic net wallet settlement.

Two findings materially affect the design. First, the completed Canadian government project record reports reliable V2G operation but concludes that blockchain’s incremental transaction-fee savings, after infrastructure operating costs, were insignificant compared with EV-versus-petrol savings; it records no follow-on project ([Natural Resources Canada](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency)). Second, Alby already documents app-specific wallet permissions and budgets, while BSV’s BRC-181 now specifies a more detailed autonomous-spending policy; these are useful authorisation comparators, not demonstrated V2G integrations ([Alby Hub](https://guides.getalby.com/user-guide/alby-hub/app-connections); [BRC-181](https://bsv.brc.dev/wallet/0181)).

**Recommended direction:** retain BSV as the initial settlement adapter, but make the session ledger and budget contract payment-network independent. Demonstrate bounded authorisation, independently priced import/export intervals and an actual operator-to-driver credit; benchmark the same workflow against Lightning/Nostr Wallet Connect and a conventional ledger rather than claiming blockchain is inherently cheaper.

## Scope and evidence standard

The reference design is:

1. The driver binds an existing wallet and approves a session spending ceiling.
2. The wallet or budget service authorises the session without a merchant top-up or full-session prepayment.
3. OCPP controls the charging session and supplies measured energy data.
4. Home Assistant calculates interval charges and credits from agreed dynamic tariffs.
5. The payment adapter settles the actual amount: driver pays operator for a positive net charge; operator pays driver for a negative net charge.

This assessment distinguishes four questions that are frequently conflated: whether an EV physically exports electricity; whether money moves in both directions; whether a wallet enforces delegated spending; and whether a deployment is operating commercially. A refund, a blockchain record or a bidirectional payment channel is not, by itself, evidence of V2G settlement.

The review covered named EV payment products, hardware demonstrations, energy-sector pilots, academic systems and adjacent wallet-authorisation standards. Primary project records, official documentation and selected public implementation files were prioritised; the comparison is not an exhaustive patent search, security audit or certification assessment. In the tables, **n.a. means not established in the reviewed evidence, not necessarily unsupported by the product**.

## Closest EV and V2G comparators

### Comparison at a glance

“Fit” is an analytical judgement against the reference design, not a vendor claim. Historical demonstrations are not presented as currently available services.

| System | Maturity established by the evidence | Energy and payment evidence | Budget-first fit |
|---|---|---|---|
| **SWTCH / Opus One Canadian project family** | Completed Canadian technology demonstration; separate SWTCH material describes a condo V2G pilot ([government results](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency); [pilot](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/)). | V2G reliability and grid services reported; pilot description explicitly links charge/discharge to wallet debits and credits. Public details of export payouts, currency and settlement cadence remain n.a. ([results](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency); [wallet description](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/)). | **Closest practical V2G precedent**, but per-session budget mandate is n.a. and the commercial wallet must not be assumed to use the pilot’s blockchain design. |
| **Satimoto** | Published charging app and inspectable implementation; current service coverage was not independently tested ([app listing](https://apps.apple.com/bj/app/satimoto/id6444064066); [repository](https://github.com/satimoto/satimoto)). | Lightning micropayments during charging; OCPI session handling, fiat-to-millisatoshi invoicing and rebate workflows are visible. Physical V2G/export credits are n.a. ([app](https://apps.apple.com/bj/app/satimoto/id6444064066); [OCPI code](https://github.com/satimoto/go-ocpi/blob/main/internal/session/v2.1.1/process.go); [invoice code](https://github.com/satimoto/go-lnm/blob/main/internal/session/invoice.go); [rebate code](https://github.com/satimoto/go-lnm/blob/main/internal/cdr/invoice.go)). | **Best inspectable charging-payment comparator**; a wallet-enforced per-session mandate is n.a. |
| **Distributed Charge** | Alpha hardware prototype and initial demonstration ([developer](http://andyschroder.com/DistributedCharge/alpha/InitialDemonstration/)). | Car-to-charger Lightning micropayments; energy delivery stops if payments stop. Physical export and OCPP integration are n.a. ([developer](http://andyschroder.com/DistributedCharge/alpha/InitialDemonstration/)). | Useful exposure-control pattern, but streaming payment is different from approval followed by net settlement. |
| **ElaadNL IOTA Charging Station** | Completed working proof of concept ([ElaadNL](https://elaad.nl/en/projects/iota-charging-station/)). | Autonomous data/value exchange, meter-value storage and charging payment; physical EV export is n.a. ([ElaadNL](https://elaad.nl/en/projects/iota-charging-station/)). | Strong machine-wallet precedent; not an OCPP-preserving architecture or verified budget-first implementation. |
| **ElaadNL / Enexis self-balancing grid** | Laboratory proof of concept, 2019 ([ElaadNL](https://elaad.nl/en/proof-of-concept-of-autonomous-self-balancing-power-grid-using-iota/)). | IOTA incentives for charging more slowly or off-peak; this is demand reduction, not demonstrated vehicle export ([ElaadNL](https://elaad.nl/en/proof-of-concept-of-autonomous-self-balancing-power-grid-using-iota/)). | Useful precedent for a separate flexibility reward, rather than only energy-payment reversal. |
| **P6V2G** | Cryptographic protocol, formal analysis and runtime estimates; no physical deployment reported ([paper](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)). | Wallet accumulates charging debt minus rewards, cleared periodically; neither a cryptocurrency nor an operational V2G payout rail is specified ([paper](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)). | **Closest post-payment accounting design**; budget enforcement and payment guarantees remain separate. |
| **IOTA Flash Channel EAV study** | Small proof of concept using a temperature sensor, explicitly without cars or charging stations ([paper](https://arxiv.org/pdf/1804.08964v2)). | Bidirectional payment channel with equal deposits from both parties; proposed dynamic energy pricing, but no measured EV export ([paper](https://arxiv.org/pdf/1804.08964v2)). | Poor match to the no-prefunding objective if adopted literally. |
| **Hyperledger EV payment study** | Local prototype/simulation, 2021 ([paper](https://www.mdpi.com/2071-1050/13/14/7962)). | Electronic-wallet billing and proposed prosumer-to-station energy trading; cryptocurrency is future work and physical V2G is n.a. ([paper](https://www.mdpi.com/2071-1050/13/14/7962)). | Useful ledger separation; not evidence of blockchain money settling a real bidirectional session. |
| **Share&Charge / Oslo2Rome** | Historical roaming demonstrations; the reviewed Phase 1 settlement design was forward-looking ([IRENA](https://www.irena.org/Innovation-landscape-for-smart-electrification/Power-to-mobility/10-Blockchain-enabled-transactions); [Phase 1 design](https://medium.com/share-charge/share-charge-platform-phase-1-a65c7f8fc27f)). | Proposed CPO/MSP settlement on Energy Web Chain, with drivers invoiced or prepaid; physical V2G is n.a. ([design](https://medium.com/share-charge/share-charge-platform-phase-1-a65c7f8fc27f)). | Strong lesson on off-chain communication and aggregated settlement; not a direct driver-wallet export system. |
| **moveID / MOBIX Park & Charge** | Functional IAA 2023 demonstrator ([consortium recap](https://moveid.org/2023/11/03/embracing-the-future-of-mobility-a-recap-of-the-iaa-mobility-showcase-moveid/)). | Decentralised identities and credentials, but the recap identifies Google Pay for charging/parking fees. Autonomous car wallets are a future vision; V2G is n.a. ([recap](https://moveid.org/2023/11/03/embracing-the-future-of-mobility-a-recap-of-the-iaa-mobility-showcase-moveid/)). | Identity comparator, not demonstrated cryptocurrency export settlement. |
| **MOBI DRIVES / Citopia** | Pilot simulating driver/station/service interactions; dummy battery SOC/SOH data ([MOBI](https://dlt.mobi/evtransactions/)). | Reservation, charging-payment and credential workflows; physical export, wallet budget enforcement and export payouts are n.a. ([MOBI](https://dlt.mobi/evtransactions/)). | Useful identity and verifiable-record design, not an end-to-end payment precedent. |

### SWTCH: the most important practical comparison

SWTCH’s 2022 condo-pilot description says its platform tracks energy charged into and discharged from an EV, creating credits and debits in a third-party distributed ledger that participants can monitor on their phones ([SWTCH](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/)). That is close to the proposed accounting outcome, but the announcement does not establish the currency, custody arrangements, payout mechanism, dynamic tariff calculation, session mandate or settlement frequency ([SWTCH](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/)).

The completed NRCan project record is stronger outcome evidence: it describes V2G chargers, Level 2 chargers, networking and blockchain integration at Toronto sites, and reports reliable V2G operation capable of grid services ([Natural Resources Canada](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency)). It identifies battery temperature, idle consumption, inverter capability and battery reserve as factors affecting service quality ([Natural Resources Canada](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency)).

Its commercial conclusion is more restrained than the launch publicity: blockchain was computationally efficient at scale, but its incremental fee savings after operating costs were insignificant relative to the savings from EVs over petrol vehicles ([Natural Resources Canada](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency)). This does not prove that all wallet systems are uneconomic; it does invalidate using nominal transaction fees alone as the business case.

The sources describe related projects with different partners and stages, so the completed NRCan record should not be presented as a line-by-line final evaluation of every claim in the later condo-pilot announcement. In particular, the public completion record does not report an independently auditable number of driver-wallet export settlements ([NRCan results](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency); [SWTCH announcement](https://swtchenergy.com/blog/technology-spotlight/swtch-launches-an-innovative-v2g-e-mobility-pilot/)).

A further distinction matters: SWTCH’s public commercial documentation describes a wallet-based credit-card system, and its support guide describes an initial wallet funding amount and automatic top-ups, not the proposed no-top-up mandate ([SWTCH technology](https://swtchenergy.com/technology/); [wallet support](https://support.swtchenergy.com/hc/en-us/articles/5914453827085-How-can-I-see-my-current-wallet-balance)). Therefore, the commercial service should not be cited as proof that a non-custodial blockchain V2G wallet is generally available.

**Design implication:** borrow the two-way energy ledger, but request the actual settlement architecture and operating-cost breakdown before treating SWTCH as a validated commercial blueprint. The highest-value questions concern how export credits became spendable money, not whether a blockchain recorded them.

### Satimoto: inspectable micropayment implementation

Satimoto’s published app describes adding bitcoin and streaming Lightning micropayments while an EV charges ([app listing](https://apps.apple.com/bj/app/satimoto/id6444064066)). Its public code separates OCPI session data from Lightning invoices: session processing handles currency, kWh, charging periods and total cost, while invoice creation records fiat amounts, exchange rates, millisatoshi amounts, estimated energy and metered energy ([OCPI session code](https://github.com/satimoto/go-ocpi/blob/main/internal/session/v2.1.1/process.go); [invoice code](https://github.com/satimoto/go-lnm/blob/main/internal/session/invoice.go)).

This is useful architectural evidence for keeping energy accounting and payment conversion separate. However, currency conversion is not proof of dynamic electricity tariffs, and the reviewed files do not establish OCPP-controlled V2G or session-level export remuneration ([session invoice code](https://github.com/satimoto/go-lnm/blob/main/internal/session/invoice.go)).

The rebate code is especially instructive: it can reduce an unsettled invoice or create a separate rebate request associated with the session ([rebate implementation](https://github.com/satimoto/go-lnm/blob/main/internal/cdr/invoice.go)). That demonstrates accounting for a reverse financial adjustment, not that a vehicle exported electricity or earned an export payment.

**Design implication:** use Satimoto as a code-reading and reconciliation comparator, not a drop-in V2G solution. Confirm current operation directly before relying on historical charging-location counts; this research did not perform a live charging transaction.

### Distributed Charge and ElaadNL: machine payments without complete V2G settlement

Distributed Charge’s alpha demonstration integrated Lightning payment modules into a Tesla Model 3 and Gen 2 Wall Connector, with payment requests over the existing cable and energy stopping when payments stop ([developer demonstration](http://andyschroder.com/DistributedCharge/alpha/InitialDemonstration/)). It is credible hardware-level evidence for coupling continued delivery to successful micropayments, but the documented system is car-to-charger payment, not measured export followed by wallet credit ([developer demonstration](http://andyschroder.com/DistributedCharge/alpha/InitialDemonstration/)).

ElaadNL’s IOTA station provides a different precedent: a working autonomous proof of concept used IOTA for user communication, meter-value storage and payment, while intentionally taking a different approach from a conventional OCPP/back-office system ([ElaadNL project](https://elaad.nl/en/projects/iota-charging-station/)). Its separate self-balancing-grid proof of concept paid IOTA incentives for reducing or delaying charging, rather than demonstrating electricity discharged from an EV ([ElaadNL grid experiment](https://elaad.nl/en/proof-of-concept-of-autonomous-self-balancing-power-grid-using-iota/)).

Historical IOTA claims also need version discipline: IOTA announced completion of its Rebased mainnet launch on 5 May 2025, while current documentation discusses transaction fees and a changed wallet environment ([IOTA launch](https://blog.iota.org/builders-welcome-rebase-complete/); [IOTA documentation](https://docs.iota.org/about-iota/FAQ)). The older “feeless Tangle” demonstrations should not be treated as evidence that their original software and economics remain unchanged.

**Design implication:** preserve the existing OCPP and CSIP-AUS control path rather than replacing it with a payment network. Treat charging curtailment rewards, exported-energy credits and refunds as different ledger entries.

### P6V2G and academic systems: useful concepts, limited deployment evidence

P6V2G is unusually close to the desired post-payment model: a wallet accumulates session cost minus rewards, and the operator clears the balance at the end of a billing period, with monthly billing as an example ([P6V2G](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)). The wallet is a cryptographic accounting object, not necessarily a cryptocurrency wallet, and settlement between operators is outside the paper’s scope ([P6V2G](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)).

Its security model also exposes a relevant limitation: double spending in the semi-online setting is detected after the fact, and collusion that records wallet updates unrelated to a physical charging session is outside its proof ([P6V2G](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1)). A mathematically protected wallet balance does not establish that the underlying energy was delivered.

The 2018 IOTA Flash Channel study is often a tempting micropayment citation, but its actual proof of concept used temperature readings, not EVs or charging stations, and required both sides to deposit equal amounts into a jointly controlled address ([study](https://arxiv.org/pdf/1804.08964v2)). Its bidirectional channel is financial infrastructure, not evidence of bidirectional EV charging.

More recent research does not remove this evidence gap: a blockchain V2G study uses historical SUSTech EV routines and simulated grid-service demand, while a December 2025 cross-chain EMS paper reports simulated charging/discharging and optimisation outcomes rather than field wallet settlements ([V2G trading study](https://arxiv.org/pdf/2407.16180v4); [cross-chain EMS paper](https://www.tandfonline.com/doi/full/10.1080/17445760.2025.2591036)). These are relevant to scheduling and incentive design, but not evidence that an interoperable payment product is ready to connect to the demonstration.

## Adjacent systems worth borrowing from

### Budget authorisation is a distinct capability

The most useful budget comparators are not necessarily EV-specific. They help define what “the wallet approves the budget” must mean, independently of charger control.

| Mechanism | What is established | Important boundary | Recommended use |
|---|---|---|---|
| **Alby Hub / Nostr Wallet Connect** | App connections can make and receive payments with permissions, budgets, renewal periods and expiry; budgets restrict what the connection may access ([Alby Hub](https://guides.getalby.com/user-guide/alby-hub/app-connections)). | No EV integration or reservation of the entire future session cost is established by these instructions ([Alby Hub](https://guides.getalby.com/user-guide/alby-hub/app-connections)). | Best functional benchmark for user-controlled delegated wallet spending. |
| **BSV BRC-116** | One-time spend approval or standing originator-scoped monthly authorisation ([BRC-116](https://bsv.brc.dev/wallet/0116)). | A monthly app limit is not inherently a one-session budget or reserved funds ([BRC-116](https://bsv.brc.dev/wallet/0116)). | Baseline permissions; add an explicit session-level contract and enforcement. |
| **BSV BRC-181** | Signed policy, isolated account, transaction/period/lifetime caps, destinations, expiry, revocation and atomic check-reserve-debit semantics ([BRC-181](https://bsv.brc.dev/wallet/0181)). | Transaction-level coin reservation is not a guarantee that a future charging session’s full liability is reserved; public production-wallet support is not established ([BRC-181](https://bsv.brc.dev/wallet/0181)). | Most relevant BSV specification to assess with the wallet developer. |
| **Open Payments / Interledger** | Grants can limit the cumulative amount of multiple outgoing payments, with receiver and interval constraints; incoming payments and quotes are separate resources ([grant API](https://openpayments.dev/apis/auth-server/operations/post-request/)). | Protocol support by the actual account provider is required; the reviewed material is not an EV deployment ([Interledger](https://interledger.org/tech/open-payments/)). | Strong payment-neutral model for consent, amount limits and role reversal. |
| **Conventional card authorisation** | Estimated amount is held, then adjusted to actual charging cost; Visa separately describes post-payment and prepaid models ([Visa EV payments guide](https://corporate.visa.com/content/dam/VCOM/corporate/solutions/documents/visa-ev-charging-brochure.pdf)). | An authorisation hold is different from prepaid money; releasing a hold is not an earned export payout ([Visa guide](https://corporate.visa.com/content/dam/VCOM/corporate/solutions/documents/visa-ev-charging-brochure.pdf)). | Commercial benchmark for risk and user experience, not a complete V2G payout design. |

Alby is the closest established documentation of the desired user experience: connect a wallet to an app, set its permitted spend and let that app make and receive payments without transferring the whole budget to the app first ([Alby Hub](https://guides.getalby.com/user-guide/alby-hub/app-connections); [Alby explanation](https://blog.getalby.com/introducing-alby-hubs-superpower-app-connections-2/)). This remains a design analogy; no evidence reviewed establishes that Satimoto uses this particular budget mechanism for V2G.

### BRC-181 changes the BSV implementation questions

BRC-181 adds materially more precise controls than a generic budget prompt: a signed policy binds the agent to an isolated account and sets `per_tx_cap`, `period_cap`, `total_budget`, `max_fee`, permitted destinations and expiry ([BRC-181](https://bsv.brc.dev/wallet/0181)). It also specifies concurrent-spend accounting, persistent counters and conservative treatment of uncertain broadcasts so a timeout does not simply restore the spending allowance ([BRC-181](https://bsv.brc.dev/wallet/0181)).

This is relevant to the proposed charging-session gate, but the specification describes an isolated funded account and per-transaction reservations, not an irrevocable guarantee to pay all energy delivered under a future EV session ([BRC-181](https://bsv.brc.dev/wallet/0181)). Funding an account within the driver’s own wallet should therefore be distinguished from prepaying the charging operator.

The document states that a reference implementation exists and has testnet/concurrency test coverage, but identifies it as “not a public path”; those are author-reported tests, not an independent audit or evidence of support in the wallet used at the demonstration ([BRC-181](https://bsv.brc.dev/wallet/0181)). It also explicitly identifies limitations when a payment service returns fresh, payer-unverifiable receiving addresses under BRC-166 ([BRC-181](https://bsv.brc.dev/wallet/0181)).

BSV’s BRC-29 describes authenticated payment construction and receipt using derived keys, `createAction` and `internalizeAction`, but does not define EV tariffs or session mandates ([BRC-29](https://bsv.brc.dev/payments/0029)). The recommended next check is therefore the actual combination of wallet policy enforcement, verified payee derivation, session binding and receipt handling, not simply whether a wallet implements BRC-100.

### Roaming, identity and flexibility platforms solve different problems

Share&Charge’s Phase 1 design deliberately moved ordinary communication off-chain and proposed one blockchain settlement transaction per charging process after encountering latency, cost and event-handling problems with more extensive smart-contract use ([Share&Charge design](https://medium.com/share-charge/share-charge-platform-phase-1-a65c7f8fc27f)). That is a useful precedent for keeping meter samples and control traffic out of the payment rail.

The moveID recap says charging and parking fees were paid through Google Pay while decentralised identifiers and credentials supported the demonstrator; MOBI’s DRIVES page describes simulated interactions and dummy battery data ([moveID](https://moveid.org/2023/11/03/embracing-the-future-of-mobility-a-recap-of-the-iaa-mobility-showcase-moveid/); [MOBI](https://dlt.mobi/evtransactions/)). Neither should be promoted as proof that decentralised identity automatically provides a cryptocurrency payment mandate or export payout.

Equigy is relevant to device-level flexibility records and market access, but the reviewed material does not establish a driver-wallet micropayment system; Powerledger’s current EV page focuses on renewable matching, traceability and charging incentives rather than a documented two-way wallet settlement implementation ([Equigy platform](https://equigy.com/); [Equigy technical/policy paper](https://equigy.com/wp-content/uploads/2020/09/Equigy-A-multi-TSO-initiative.pdf); [Powerledger EV offering](https://powerledger.io/solutions/need/ev-solutions/)). These are adjacent energy-market functions, not substitutes for session authorisation and payment reconciliation.

## Implications for the OCPP, Home Assistant and BSV design

The following are proposed engineering choices, not claims that the existing integration already implements them. They preserve the requested division of responsibility and avoid introducing merchant prepayment merely because several historical examples used it.

### Separate permission, solvency and settlement

Three different promises must be made explicit:

- **Permission:** the driver authorises the application to spend up to a defined limit for this session.
- **Ability to pay:** funds and payment connectivity are available when a charge falls due.
- **Payment assurance:** the operator has a guarantee, reserve or accepted exposure policy for energy already delivered.

A budget approval answers the first question, not automatically the other two. The same distinction appears in conventional post-payment risk and in wallet authorisation documentation that defines spending limits without promising a full-session hold ([Visa guide](https://corporate.visa.com/content/dam/VCOM/corporate/solutions/documents/visa-ev-charging-brochure.pdf); [BRC-116](https://bsv.brc.dev/wallet/0116); [Alby Hub](https://guides.getalby.com/user-guide/alby-hub/app-connections)).

For a controlled demonstration, use a funded wallet, an explicit small operator exposure limit and a tested stop margin. If production operation later requires guaranteed payment, assess a wallet-side reserve or another guarantee separately; do not silently recast a revocable spending permission as secured credit.

### Keep three clocks independent

The proposed implementation should separate the control/metering clock, the tariff/accounting clock and the payment clock. For example, rapid control and meter updates can feed a five-minute tariff ledger while actual transfers occur at an agreed threshold or at session completion.

Recommend starting with **continuous calculation and final net settlement**, matching the intended no-charge/refund workflow. Add rolling settlement only if testing shows it is needed to bound operator exposure; label it clearly, because settling every import interval and later paying export earnings is economically different from a single net session payment.

Lightning supports frequent payments but has inbound and outbound capacity constraints, so receiving export earnings requires a working receiving path as well as permission to spend ([Lightning Labs](https://docs.lightning.engineering/the-lightning-network/liquidity/how-to-get-inbound-capacity-on-the-lightning-network)). BSV can use its own payment construction and receipt mechanisms, but that does not remove the need to reconcile payment state against the session ledger ([BRC-29](https://bsv.brc.dev/payments/0029)).

### Use signed tariff amounts, not an assumed energy-to-money direction

Recommended session calculation:

\[
C_{\mathrm{session}}
=\sum_i\left(E_{\mathrm{import},i}p_{\mathrm{import},i}
-E_{\mathrm{export},i}p_{\mathrm{export},i}\right)+F
\]

Here, energy quantities are non-negative measured interval kWh, prices are signed currency/kWh amounts, and \(F\) contains agreed session fees and adjustments. A positive result means the driver owes the operator; a negative result means the operator owes the driver.

This formula intentionally permits negative import prices and negative export prices. It avoids assuming that every import interval costs money or that every export interval earns money.

| Illustrative interval, not a market observation | Ledger effect |
|---|---:|
| Import 2 kWh at AUD 0.20/kWh | AUD 0.40 driver charge |
| Export 1 kWh at AUD 0.70/kWh | AUD 0.70 driver credit |
| Net session, excluding fees | AUD 0.30 operator-to-driver payment |
| Import 1 kWh at AUD −0.05/kWh | AUD 0.05 driver credit |
| Export 1 kWh at AUD −0.10/kWh | AUD 0.10 driver charge |

Retain import and export registers separately; net kWh alone loses the information needed when intervals and rates differ. Define the metering boundary explicitly: energy exported from the EV at the charger is not automatically equal to energy exported through the premises’ grid connection.

Store meter timestamps, units, tariff version, price validity and quality flags with each interval. Use deterministic decimal or integer accounting, an explicit rounding rule and a final reconciliation against authoritative session meter values.

### Define exactly what the budget caps

For the first demonstration, define a **maximum positive net session charge in AUD**, plus a wallet-authorised maximum outgoing amount in satoshis including permitted payment fees. Stop, pause or request fresh consent when either constraint would be breached; do not raise one ceiling merely because the other still has room.

Keep three counters separate: the calculated net session charge, gross outgoing wallet payments and the wallet policy’s remaining allowance. An earned credit should not automatically replenish delegated spending authority unless the policy explicitly permits it.

The operator also needs its own payout authority and funded wallet. Receiving the driver’s approval to spend does not authorise the operator’s wallet to pay export earnings, and does not establish who owes rewards funded by a retailer, network or aggregator.

Define the AUD-to-BSV conversion policy before the session starts: when the rate is fixed, how long a quote remains valid, who bears conversion fees and how rate changes interact with both budget ceilings. Record the agreed rate on the receipt rather than retrospectively applying whatever rate is available at finalisation.

### Preserve OCPP’s role and test the actual feature set

OCPP 2.1 adds bidirectional power-transfer support, local cost calculation and additional transaction/payment features, but protocol publication is not evidence that a particular charger or Home Assistant integration implements every feature ([Open Charge Alliance](https://openchargealliance.org/protocols/ocpp-protocols/ocpp-2-1/)). OCA’s UPI paper also distinguishes payment processing from session control and notes that backend-only prepaid stopping can overshoot because meter values arrive at intervals; local cost limits depend on charger capability ([OCA payment integration paper](https://openchargealliance.org/wp-content/uploads/2025/11/OCA-Whitepaper-OCPP-UPI-mobile-payments.pdf)).

Recommend leaving the direct Home Assistant/OCPP connection unchanged and placing the billing/authorisation gate alongside it. Keep grid envelopes, electrical protection, departure requirements and minimum battery state of charge authoritative even when a wallet would permit further spending or a tariff offers a reward.

Before claiming a physical bidirectional demonstration, verify the charger’s export capability, the negotiated protocol, the actual telemetry registers and the local stop behaviour. A replayed export trace is useful for testing accounting, but should be labelled simulated rather than presented as measured V2G.

## Recommended demonstration and acceptance gates

### Minimum credible demonstration

The initial demonstrator should be deliberately small: one driver wallet, one operator wallet, one identified charging session and a durable Home Assistant-side ledger. Use test funds or an explicitly bounded real-money amount until both energy and payment failure paths have been exercised.

| Test | Acceptance condition |
|---|---|
| Budget approved | Wallet/session binding, tariff policy, expiry and ceilings are recorded before charging is permitted. |
| Budget rejected or revoked | No new authorised energy delivery; any existing delivery follows a defined safe stop path. |
| Import-only session | Final wallet debit equals independently recomputed interval cost and agreed fees. |
| Import followed by higher-value export | Final result is an operator-funded payment to the driver, not a refund of a prepaid deposit. |
| Negative import or export price | Direction of payment follows the signed tariff calculation, not energy direction alone. |
| Budget exhaustion | Session stops with a measured and documented maximum overshoot under the tested operating conditions. |
| Missing tariff or stale meter data | Billing does not silently invent a price or energy quantity; the session enters the defined restricted state. |
| Duplicate or out-of-order events | Replayed events do not duplicate kWh, invoices or wallet transfers. |
| Restart during settlement | Session and payment state recover without resetting budget counters or paying twice. |
| Broadcast/payment timeout | Status remains unresolved until reconciled; no blind second payment is issued. |
| Concurrent sessions | Shared wallet authority cannot be exceeded through simultaneous reservations or approvals. |
| Payout wallet unavailable | Export earnings remain an explicit payable; receipt does not falsely say “paid”. |

The receipt should separate **energy measured**, **amount calculated**, **amount authorised**, **payment submitted** and **payment confirmed under the selected rail’s criteria**. A transaction identifier alone should not collapse all these states into “settled”.

### Comparative trial design

Run the same recorded session through three adapters: BSV, Lightning/NWC and an ordinary internal ledger with one final payment. Keep metering, tariffs, session identity and acceptance tests identical so the comparison measures payment behaviour rather than different charging algorithms.

Measure total service cost, not just network fees: integration and hosting, funding/liquidity, currency conversion, failed payments, reconciliation, support, receipt production and operator exposure. The completed Canadian pilot makes this a necessary test rather than an optional business-case refinement ([Natural Resources Canada](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency)).

Suggested decision criteria are: correct debit and credit outcomes; enforceable consent; bounded unpaid exposure; restart-safe reconciliation; acceptable user effort; and a cost advantage or functional benefit that survives operating costs. Avoid selecting a chain on advertised throughput when a single final session settlement may meet the need.

## Highest-value follow-up enquiries

The following are proposed enquiries, not messages sent or commitments from the organisations. They focus on uncertainties that would change the implementation decision.

- **SWTCH and Opus One project participants:** request the final technical architecture, wallet currency/custody model, evidence of completed export payouts, payment cadence, OCPP integration and the operating-cost components behind the NRCan conclusion.
- **Satimoto maintainers:** confirm current service status, charging-session invoice timing, treatment of estimated versus final CDR amounts, reverse payments, export support and whether a bounded external-wallet mandate is planned.
- **BSV wallet developer from the demonstration:** establish which component enforces the budget, whether BRC-181 is implemented, what remains possible when the wallet is offline, and how an unattended final debit and incoming export payment are handled.
- **Alby/NWC implementers:** test a dedicated expiring app connection for both directions of settlement and clarify budget accounting, revocation, concurrency and receiving liquidity.
- **OCA and the charger/integration maintainers:** confirm which bidirectional metering, transaction-limit and local tariff features are implemented and independently testable in the actual equipment.

The first two enquiries seek deployment evidence; the next two test the payment mandate. The final enquiry verifies that the financial layer is being attached to a known, testable energy-control interface.

## Evidence gaps and final judgement

No source in this review establishes a currently production-verified implementation of the entire target combination: non-prepaid session-budget consent, OCPP-controlled physical import/export, Home Assistant dynamic pricing and automatic net BSV wallet settlement. That is a bounded finding about the reviewed public evidence, not a claim that no private implementation exists.

The strongest direct outcome evidence is the Canadian government’s completed V2G/blockchain demonstration record; the strongest inspectable EV payment implementation is Satimoto; the most relevant post-payment wallet model is P6V2G; and the clearest adjacent budget-authorisation references are Alby/NWC, Open Payments and BRC-181 ([NRCan](https://natural-resources.canada.ca/funding-partnerships/decreasing-transactional-ev-charging-costs-enhancing-grid-efficiency); [Satimoto invoice code](https://github.com/satimoto/go-lnm/blob/main/internal/session/invoice.go); [P6V2G](https://energyinformatics.springeropen.com/articles/10.1186/s42162-019-0075-1); [Alby](https://guides.getalby.com/user-guide/alby-hub/app-connections); [Open Payments](https://openpayments.dev/apis/auth-server/operations/post-request/); [BRC-181](https://bsv.brc.dev/wallet/0181)). These sources support a carefully bounded integration demonstration, not an assertion of an already solved commercial product.

The review did not execute payments, test hardware or audit the wallet implementations. The ScienceDirect result titled “Blockchain-based inter-operator settlement system” yielded only cookie material and was excluded from substantive findings; several older projects lacked current operational confirmation, and academic simulation results were not treated as field evidence.

**Overall judgement:** proceed with a payment-neutral, budget-authorised demonstration and make BSV the first adapter rather than the foundation of the energy ledger. The defensible proposition is transparent consent and auditable two-way settlement across open interfaces; lower fees, guaranteed collection and production readiness remain hypotheses to test.

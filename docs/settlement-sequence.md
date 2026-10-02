# Session-end wallet settlement

Current design | 2 October 2026

No budget is approved or reserved, and no payment gate is added to charging start. Home Assistant prices the actual session and the payer approves the final payment.

## Target flow

```mermaid
sequenceDiagram
    participant OCPP as OCPP integration
    participant HA as Home Assistant ledger
    participant Service as Settlement service
    participant Payer as Payer wallet
    participant Recipient as Recipient wallet
    HA->>HA: Bind known driver wallet to session
    OCPP->>HA: Timestamped directional meter readings
    HA->>HA: Apply interval import/export prices
    OCPP->>HA: End session and final meter readings
    HA->>HA: Reconcile, freeze ledger and net account
    HA->>Service: Prepare immutable settlement
    Service-->>HA: Exact amount and expiring conversion quote
    alt Net cost
        Note over Payer,Recipient: Driver pays operator
    else Net credit
        Note over Payer,Recipient: Operator pays driver
    else Zero account
        Service-->>HA: No payment due
    end
    opt Nonzero account
        HA->>Service: Request payment approval
        Service->>Payer: Present recipient, amount, quote and fee policy
        alt Approved
            Payer->>Service: Approved signed payment or wallet action
            Service->>Recipient: Deliver payment and remittance/proof
            Recipient-->>Service: Verified acceptance
            HA->>Service: Poll settlement
            Service-->>HA: Receipt and transaction state
            Note over Service,Recipient: Track chain confirmation separately
        else Declined or expired
            Service-->>HA: Account remains outstanding
        end
    end
```

The sequence is a proposed logical exchange, not a claim that every selected wallet exposes the same transport. The eventual adapter must verify approval, submission, receipt and recovery capabilities.

## What v0.1.0 actually runs

```mermaid
sequenceDiagram
    participant Input as Demo or future OCPP adapter
    participant HA as HA scaffold
    participant Mock as Mock service and SQLite
    participant CLI as Operator mock CLI
    Input->>HA: Bind session and add intervals
    HA->>HA: Reconcile final Wh and freeze ledger
    HA->>Mock: PUT settlement with durable UUID
    Mock-->>HA: Prepared synthetic quote
    HA->>Mock: Request payment
    Mock-->>HA: Awaiting simulated approval
    CLI->>Mock: Approve with separate mock approval token
    Mock->>Mock: Persist a single MOCK receipt
    HA->>Mock: Poll
    Mock-->>HA: mock_received, txid null
```

No private key, real wallet signature, BSV transaction, chain confirmation or physical charger operation occurs in this mock. An operator credit is funded only conceptually until a live wallet adapter is implemented.

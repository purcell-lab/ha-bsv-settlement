# Separate 5 kWh adjustments

The operator's Owner credits and Driver credits panels both offer two preparation buttons:

- Credit 5 kWh export at current export price.
- Debit 5 kWh import at current import price.

These create separate real-value manual accounts, not charging sessions and not simulated transfers. Pressing either button freezes an account for review; it never signs or broadcasts. Measured energy, charger state, existing session accounts and charging-budget authority are unchanged.

## Calculation and review

The server resolves the latest registered driver for the selected recorder and verifies the receiving registration. A revoked or invalid latest registration cannot silently select an older driver. The displayed receiving address and public identity are frozen. Before approval and unsigned credit submission, the current registration must still match.

The configured recorder supplies the tariff entity. The service accepts only a finite AUD/kWh rate with a currently valid, timezone-aware tariff interval. It accepts a finite positive sat/AUD conversion sensor. Both are frozen, with source and timestamps. Estimated current rates are explicitly disclosed. A tariff changing after preparation does not silently change the frozen account.

Import: net AUD = 5 × import rate. Export: net AUD = −5 × export rate. Positive net means driver pays operator; negative net means operator pays driver. Negative tariffs reverse the normal direction. Convert the unrounded absolute AUD amount to satoshis once, rounding half up. Zero, sub-satoshi and over-limit amounts are refused.

The adjustment uses an `adjustment:` reference, `manual_energy_adjustment` classification and an explicit 5 kWh adjustment basis. Measured import/export fields remain null, never fabricated 5 kWh readings. It is not an OCPP transaction.

## Payment paths

For a driver debit, the administrator first reviews the separate account. Approval issues a manual payment request with exact amount, operator address and expiry. The driver sends it in their wallet and supplies the transaction ID/output for exact-output verification. This does not use weekly charging consent, automatically debit the driver, or initiate an integrated BRC spending action. Never pay a request that is expired or has uncertain payment evidence.

For an operator credit, approve the account, select “Quote fee and prepare unsigned credit”, then review the exact amount, recipient and fee. A separate checkbox and broadcast action authorise real funds. A fresh provider fee quote is checked at preparation and again before signing. Recipient amount plus operator fee cannot exceed 1,000 sat. This flow shares existing funding exclusion, signed-byte persistence and no-rebroadcast safeguards.

The credit goes to the verified registration's derived address. The original wallet can retrieve its BRC receipt through the static driver portal and acknowledge acceptance. Original registration ownership is retained even after a new driver registers. Provider confirmation and wallet receipt acceptance remain distinct.

## Retry and access controls

Both new services are administrator-only and reject context-free automations. A caller-supplied UUID makes preparation retries stable across reloads/restarts. A new request UUID also returns an existing unresolved adjustment with the same driver, direction, recorder and conversion source rather than creating another debt. Expired unresolved accounts require explicit cancellation or reconciliation; refresh never creates a replacement.

No signed charging budget, driver collection record or recorder archive is modified. No migration of existing financial records is required. The new buttons require the matching frontend and backend revision.

## Validation

Automated fictional-provider coverage includes both signs of import/export tariffs; exact 5 kWh calculation; no metering changes; request replay and restart; stale/invalid tariff and unit rejection; zero/sub-satoshi/limit rejection; revoked current driver; separate manual debit; explicit fee-aware credit; repeated broadcast without resend; receipt ownership and wallet acknowledgement; and context-free service rejection.

Browser QA must check both preparation buttons, visible frozen terms, review checkboxes, credit fee preparation, final send gating, manual debit instructions, disabled viewer controls and stale-price error handling. No live adjustment is prepared or paid by development validation.

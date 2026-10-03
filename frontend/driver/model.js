import { PublicKey, Signature, KeyDeriver } from "@bsv/sdk";

export const protocol = [2, "ha ev session budget"];
export const spendingProtocol = [2, "ev session spending"];
export const spendingScope = "one_session_capped_spending_no_charger_authority";
export const canonical = x => JSON.stringify(x, (_, v) =>
  v && !Array.isArray(v) && typeof v === "object"
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
export const bytes = s => Array.from(new TextEncoder().encode(s));
export const hex = a => Array.from(a).map(x => x.toString(16).padStart(2, "0")).join("");
export async function hash(s) {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))));
}
export const paymentAuthority = t => ({
  trigger: "after_bound_session_ends",
  collection_mode: "automatic_once_when_wallet_available",
  direction: "driver_to_operator_if_net_account_positive",
  recipient_address: t.operator_address,
  operator_identity: t.operator_identity,
  session_id: t.session_id,
  max_payments: 1,
  max_total_sats_including_fees: t.max_total_sats,
  max_fee_sats: t.max_fee_sats,
  satoshis_per_aud: t.satoshis_per_aud,
  expires_at: t.expires_at,
  revocable_before_submission: true,
  wallet_transaction_permission_required: true,
  operator_credit_requires_separate_authority: true,
  funds_reserved: false,
  charger_control: false
});
export function parseInvitation(text, clock = Date.now(), allowExpired = false) {
  if (text.length > 20000) throw Error("The invitation is too large.");
  const invitation = JSON.parse(text);
  if (invitation.version !== 1 || typeof invitation.payload !== "string" ||
      !/^[0-9a-f]{16,144}$/.test(invitation.signature)) throw Error("Invalid invitation format.");
  const t = JSON.parse(invitation.payload);
  const legacy = t.version === 1 && t.scope === "one_session_consent_only_no_payment_or_charger_authority";
  const spending = t.version === 2 && t.scope === spendingScope &&
    canonical(t.payment_authority) === canonical(paymentAuthority(t));
  if ((!legacy && !spending) || t.network !== "BSV mainnet" ||
      !/^[0-9a-f-]{36}$/.test(t.budget_id) ||
      !/^(02|03)[0-9a-f]{64}$/.test(t.operator_identity) ||
      !Number.isSafeInteger(t.max_total_sats) || t.max_total_sats < 1 || t.max_total_sats > 100000 ||
      !Number.isSafeInteger(t.max_fee_sats) || t.max_fee_sats < 0 ||
      t.max_fee_sats > 1000 || t.max_fee_sats > t.max_total_sats ||
      !Number.isFinite(Number(t.satoshis_per_aud)) || Number(t.satoshis_per_aud) <= 0 ||
      Number(t.satoshis_per_aud) > 100000000 ||
      !Number.isFinite(Date.parse(t.created_at)) || Date.parse(t.created_at) > clock + 60000 ||
      !Number.isFinite(Date.parse(t.expires_at)) || (!allowExpired && Date.parse(t.expires_at) <= clock) ||
      Date.parse(t.expires_at) - Date.parse(t.created_at) > 86401000 ||
      typeof t.session_id !== "string" || !t.session_id || t.session_id.length > 200 ||
      typeof t.transaction_id !== "string" || typeof t.pricing_rule !== "string" ||
      typeof t.import_price_entity !== "string" || typeof t.export_price_entity !== "string" ||
      typeof t.account_scope !== "string") throw Error("Invalid or expired session terms.");
  const operator = PublicKey.fromString(t.operator_identity);
  if (operator.toAddress() !== t.operator_address ||
      !operator.verify(bytes(invitation.payload), Signature.fromDER(invitation.signature, "hex"))) {
    throw Error("The operator signature or receiving address does not match.");
  }
  return { invitation, terms: t };
}
export async function signConsent(wallet, checked, identity) {
  // Revalidate immediately before and after wallet interaction, including expiry.
  const { invitation, terms } = parseInvitation(JSON.stringify(checked.invitation));
  if (terms.version !== 2) throw Error("This old invitation cannot approve spending. Ask the operator for a new invitation.");
  const current = (await wallet.getPublicKey({ identityKey: true })).publicKey;
  if (current !== identity) throw Error("The connected wallet changed. Reconnect before approving.");
  const payload = canonical({
    action: "authorise_one_session_spending", budget_id: terms.budget_id,
    driver_identity: identity, invitation_hash: await hash(invitation.payload),
    payment_authority: terms.payment_authority, version: 2
  });
  const { signature } = await wallet.createSignature({
    protocolID: spendingProtocol, keyID: terms.budget_id, counterparty: "anyone",
    data: bytes(payload),
    description: `Approve one final EV session debit up to ${terms.max_total_sats} sat including fees (fee cap ${terms.max_fee_sats} sat).`
  });
  const key = new KeyDeriver("anyone").derivePublicKey(spendingProtocol, terms.budget_id, identity);
  if (!key.verify(bytes(payload), Signature.fromDER(signature))) throw Error("The wallet signature did not verify.");
  parseInvitation(JSON.stringify(invitation));
  return { version: 2, budget_id: terms.budget_id, driver_identity: identity,
           payload, signature: hex(signature) };
}

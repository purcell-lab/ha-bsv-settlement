import { PublicKey, Signature, KeyDeriver } from "@bsv/sdk";

export const protocol = [2, "ha ev session budget"];
export const spendingProtocol = [2, "ev session spending"];
export const spendingScope = "one_session_capped_spending_no_charger_authority";
export const multiScope = "multi_session_aggregate_spending_until_expiry_or_new_driver";
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
  charger_control: false,
  ...(t.version===3?{trigger:"each_closed_session_opened_after_receiving_registration",
    max_payments:null,aggregate_limit:true,terminates_on_new_driver_registration:true,
    credits_replenish_budget:false,receiving_expires_at:t.expires_at,
    ...(t.included_session?{trigger:"explicit_current_session_and_future_sessions_after_registration",
      included_session_id:t.included_session.session_id}:{})}:{})
});
export function parseInvitation(text, clock = Date.now(), allowExpired = false) {
  if (text.length > 20000) throw Error("The invitation is too large.");
  const invitation = JSON.parse(text);
  if (invitation.version !== 1 || typeof invitation.payload !== "string" ||
      !/^[0-9a-f]{16,144}$/.test(invitation.signature)) throw Error("Invalid invitation format.");
  const t = JSON.parse(invitation.payload);
  const legacy = t.version === 1 && t.scope === "one_session_consent_only_no_payment_or_charger_authority";
  const spending = [2,3].includes(t.version) && t.scope === (t.version===3?multiScope:spendingScope) &&
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
      Date.parse(t.expires_at) - Date.parse(t.created_at) > (t.version===3||t.weekly_parent_hash?604801000:86401000) ||
      typeof t.session_id !== "string" || !t.session_id || t.session_id.length > 200 ||
      typeof t.transaction_id !== "string" || typeof t.pricing_rule !== "string" ||
      typeof t.import_price_entity !== "string" || typeof t.export_price_entity !== "string" ||
      typeof t.account_scope !== "string") throw Error("Invalid or expired session terms.");
  const operator = PublicKey.fromString(t.operator_identity);
  if(t.included_session){
    const i=t.included_session,a=i.account;
    if(t.version!==3 || typeof i.session_id!=="string" || !i.session_id ||
       typeof i.transaction_id!=="string" || !i.transaction_id ||
       !Number.isFinite(Date.parse(i.opened_at)) || Date.parse(i.opened_at)>Date.parse(t.created_at) ||
       (a!==null && (!a || a.session_id!==i.session_id || a.ocpp_transaction_id!==i.transaction_id ||
         a.opened_at!==i.opened_at || !Number.isFinite(Date.parse(a.ended_at)) ||
         Date.parse(a.ended_at)>Date.parse(t.created_at) || a.currency!=="AUD" ||
         !Number.isSafeInteger(i.amount_sats) || i.amount_sats>=t.max_total_sats ||
         ![a.import_kwh,a.export_kwh,a.net_amount_aud].every(v=>v!==null&&v!==""&&Number.isFinite(Number(v))) ||
         Number(a.import_kwh)<0 || Number(a.export_kwh)<0)) ||
       (a===null && i.amount_sats!==null))throw Error("Invalid included current session.");
  }
  if(t.closed_session_review){
    const c=t.closed_session_review,a=c.account;
    if(!a || t.session_mode!=="existing_session" || a.currency!=="AUD" ||
        a.session_id!==t.session_id || a.ocpp_transaction_id!==t.transaction_id ||
        !Number.isFinite(Date.parse(a.ended_at)) || Date.parse(a.ended_at)>clock ||
        !Number.isSafeInteger(c.amount_sats) || c.amount_sats<=0 || c.amount_sats>t.max_total_sats ||
        c.satoshis_per_aud!==t.satoshis_per_aud ||
        typeof c.reason!=="string" || c.reason.length<8 || c.reason.length>300 ||
        !Array.isArray(c.accepted_flags) || c.accepted_flags.some(f=>
          !["import:energy_without_matching_state","export:energy_without_matching_state"].includes(f)) ||
        ![a.import_kwh,a.export_kwh,a.net_amount_aud].every(v=>
          (typeof v==="number"||typeof v==="string"&&v.trim()!=="") && Number.isFinite(Number(v))) ||
        Number(a.import_kwh)<0 || Number(a.export_kwh)<0 || Number(a.net_amount_aud)<=0)
      throw Error("Invalid closed-session account review.");
  }
  if (operator.toAddress() !== t.operator_address ||
      !operator.verify(bytes(invitation.payload), Signature.fromDER(invitation.signature, "hex"))) {
    throw Error("The operator signature or receiving address does not match.");
  }
  return { invitation, terms: t };
}
export async function signConsent(wallet, checked, identity) {
  // Revalidate immediately before and after wallet interaction, including expiry.
  const { invitation, terms } = parseInvitation(JSON.stringify(checked.invitation));
  if (![2,3].includes(terms.version)) throw Error("This old invitation cannot approve spending. Ask the operator for a new invitation.");
  if(terms.weekly_parent_hash)throw Error("A session ticket cannot be approved as a standalone invitation.");
  const current = (await wallet.getPublicKey({ identityKey: true })).publicKey;
  if (current !== identity) throw Error("The connected wallet changed. Reconnect before approving.");
  const payload = canonical({
    action: terms.version===3?"authorise_multi_session_aggregate_spending":"authorise_one_session_spending", budget_id: terms.budget_id,
    driver_identity: identity, invitation_hash: await hash(invitation.payload),
    payment_authority: terms.payment_authority, version: terms.version
  });
  const { signature } = await wallet.createSignature({
    protocolID: spendingProtocol, keyID: terms.budget_id, counterparty: "anyone",
    data: bytes(payload),
    description: terms.version===3?`Approve multiple EV sessions${terms.included_session?` including current ${terms.included_session.transaction_id}`:""}: ${terms.max_total_sats} sat TOTAL including all fees until ${terms.expires_at} or a new driver registers. Credits do not replenish allowance.`:
      `Approve one final EV session debit up to ${terms.max_total_sats} sat including fees (fee cap ${terms.max_fee_sats} sat).`
  });
  const key = new KeyDeriver("anyone").derivePublicKey(spendingProtocol, terms.budget_id, identity);
  if (!key.verify(bytes(payload), Signature.fromDER(signature))) throw Error("The wallet signature did not verify.");
  parseInvitation(JSON.stringify(invitation));
  return { version: terms.version, budget_id: terms.budget_id, driver_identity: identity,
           payload, signature: hex(signature) };
}
export async function derivedInvitation(invitation,parent,sessionId){
  parseInvitation(JSON.stringify(parent.invitation));
  const checked=parseInvitation(JSON.stringify(invitation)),t=checked.terms,p=parent.terms;
  const initial=p.included_session?.session_id===sessionId?p.included_session:null;
  if(initial?.account){
    if(canonical(t.closed_session_review?.account)!==canonical(initial.account) ||
       t.closed_session_review?.amount_sats!==initial.amount_sats)
      throw Error("Current-session ticket differs from the approved account.");
  }else if(t.closed_session_review)throw Error("Unexpected historical account on this ticket.");
  if(p.version!==3||t.version!==2||t.weekly_parent_hash!==await hash(parent.invitation.payload)||
    t.session_id!==sessionId||t.session_mode!=="existing_session"||
    t.expires_at!==p.expires_at||t.max_total_sats>p.max_total_sats||t.max_fee_sats>p.max_fee_sats||
    t.satoshis_per_aud!==p.satoshis_per_aud||t.operator_address!==p.operator_address||
    t.operator_identity!==p.operator_identity||t.import_price_entity!==p.import_price_entity||
    t.export_price_entity!==p.export_price_entity||t.pricing_rule!==p.pricing_rule||
    Date.parse(t.created_at)<Date.parse(p.created_at))
    throw Error("Session ticket exceeds or differs from the multi-session approval.");
  return checked;
}

export const esc = v => String(v ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
export const finite = v => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
export const num = (v,d=0) => finite(v) ? Number(v).toLocaleString("en-AU",{minimumFractionDigits:d,maximumFractionDigits:d}) : "Unavailable";
export const stamp = v => v && Number.isFinite(Date.parse(v)) ? new Date(v).toLocaleString("en-AU",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}) : "Not checked";
export const short = v => v ? String(v).slice(0,8) : "No reference";
export function provisionalSats(session,health={},rateState) {
  const id=session?.session_id;
  const fixed=health.ongoing_credit?.sessions?.find(r=>r.session_id===id) ??
    health.driver_approvals?.find(r=>r.session_id===id&&r.approved);
  const rate=fixed ? fixed.satoshis_per_aud : rateState?.state;
  // Decimal arithmetic mirrors the settlement's positive ROUND_HALF_UP value.
  const parts=v=>{
    if(!["string","number"].includes(typeof v))return null;
    const m=String(v).match(/^-?(\d{1,16})(?:\.(\d{1,16}))?$/);
    return m?{n:BigInt(m[1]+(m[2]||"")),d:10n**BigInt((m[2]||"").length)}:null;
  };
  const amount=parts(session?.net_cost_aud),r=parts(rate);
  if(!amount||!r||Number(rate)<=0)return {sats:null,rate:null,fixed:!!fixed};
  const numerator=amount.n*r.n,denominator=amount.d*r.d;
  const rounded=(numerator*2n+denominator)/(denominator*2n);
  return {sats:rounded<=BigInt(Number.MAX_SAFE_INTEGER)?Number(rounded):null,
    rate:Number(rate),fixed:!!fixed};
}
export const chainRecordUrl=txid=>typeof txid==="string"&&/^[0-9a-f]{64}$/.test(txid)
  ?`https://api.whatsonchain.com/v1/bsv/main/tx/hash/${txid}`:null;
export const chainRecordLink=txid=>{
  const url=chainRecordUrl(txid);
  return url?`<a href="${url}" target="_blank" rel="noopener noreferrer">View chain-provider record</a><code>${esc(txid)}</code>`:
    `<code>${esc(txid||"Not submitted")}</code>`;
};
export function energyMetrics(session={}) {
  const numeric=v=>(typeof v==="number" || typeof v==="string"&&v.trim()!=="")&&Number.isFinite(Number(v));
  const direction=(energy,amount)=>{
    const kwh=numeric(energy)&&Number(energy)>=0?Number(energy):null;
    return {kwh,average:kwh>0&&numeric(amount)?Number(amount)/kwh:null};
  };
  return {toEV:direction(session.import_kwh,session.import_cost_aud),
    fromEV:direction(session.export_kwh,session.export_credit_aud)};
}
export function currentPrice(state,clock=Date.now()){
  const a=state?.attributes||{},start=Date.parse(a.start_time),end=Date.parse(a.end_time);
  const valid=state&&typeof state.state==="string"&&state.state.trim()!==""&&finite(state.state)&&
    ["$/kWh","AUD/kWh"].includes(a.unit_of_measurement)&&
    Number.isFinite(start)&&Number.isFinite(end)&&end>start&&start<=clock&&clock<end;
  return {value:valid?Number(state.state):null,available:!!valid,estimated:a.estimate!==false,
    end:valid?a.end_time:null};
}
export function stateLabel(s) {
  return ({
    provider_confirmed:"Confirmed on chain",driver_payment_provider_confirmed:"Confirmed on chain",
    provider_unconfirmed:"Awaiting block confirmation",driver_payment_provider_unconfirmed:"Awaiting block confirmation",
    submitted:"Payment submitted",broadcast_unknown:"Submission uncertain",
    ready:"Awaiting wallet collection",
    credit_queued:"Waiting for funding checks",awaiting_account_approval:"Review account",
    credit_review_approved:"Prepare credit",prepared:"Ready for payment approval",
    awaiting_driver_payment:"Waiting for driver payment",cancelled:"Cancelled",
    expired:"Review expired",expired_awaiting_reconciliation:"Expired: reconcile payment",
    credit_review_expired:"Review expired",no_payment_due:"No payment due",
    awaiting_driver_consent:"Waiting for driver approval",
    spending_authorised_wallet_permission_required:"Driver approved",
    revoked:"Approval revoked",charge_waived:"Charge waived",automatic_credit_pending:"Waiting for session end",
    waived:"Waived",closed_zero:"Closed: no payment due",
  })[s] || (s ? String(s).replaceAll("_"," ") : "Not yet assessed");
}
export function sessionStatus(s,health,now=Date.now()) {
  if(!s)return {label:"No session recorded",tone:"quiet",detail:"Create a driver invitation before the next session.",target:"drivers"};
  const payments=[...(health.automatic_credit?.payments || []).slice().reverse(),...(health.session_payments || []).slice().reverse()]
    .filter(p=>p.session_id===s.session_id && p.state!=="cancelled");
  const payment=payments.find(p=>p.txid)||payments[0];
  if(payment&&payment.state!=="waived")return {label:stateLabel(payment.state),tone:payment.state.includes("confirmed")&&!payment.state.includes("unconfirmed")?"good":payment.error||payment.state==="broadcast_unknown"?"warn":"info",
    detail:payment.txid?"Track the existing transaction. Do not pay again.":"Continue the saved settlement workflow.",target:"payments",payment};
  const closure=health.closed_sessions?.find(r=>r.session_id===s.session_id);
  if(closure&&(!finite(s.net_cost_aud)||Number(s.net_cost_aud)!==Number(closure.net_amount_aud)))
    return {label:"Closed account changed",tone:"warn",detail:"The observed account changed after closure. Review the audit record; no automatic payment is allowed.",target:"payments"};
  if(closure)return {label:stateLabel(closure.state),tone:"quiet",
    detail:closure.received_funds?`Charge waived. ${closure.received_funds.amount_sats} sat received remains unallocated; no refund authorised.`:
      `${closure.state==="closed_zero"?"Closed: no payment due.":"Charge waived; no further collection."} ${closure.reason}`,target:"payments"};
  const flags=(s.quality_flags||[]).filter(f=>!["interval_energy_allocation_estimated","not_a_final_bill"].includes(f));
  const reviewed=health.driver_approvals?.some(a=>a.session_id===s.session_id&&a.reviewed_closed_account&&
    ["awaiting_driver_consent","spending_authorised_wallet_permission_required"].includes(a.state)&&Date.parse(a.expires_at)>now);
  if(s.ended_at&&flags.length&&!reviewed)return {label:"Data review required",tone:"warn",
    detail:`Review this completed account before consent or waiver: ${flags.join(", ")}.`,target:"payments"};
  const route=health.ongoing_credit?.sessions?.find(r=>r.session_id===s.session_id);
  if(route && Number(s.net_cost_aud)<0)return {
    label:!health.ongoing_credit.effective?"Ongoing credits paused":route.error?"Credit needs attention":"Ongoing credit assigned",
    tone:route.error||!health.ongoing_credit.effective?"warn":"info",
    detail:route.error||`Credit recipient fixed for this session: ${route.recipient_address}. Final pricing, funds and the 1,000 sat total cap still apply.`,
    target:"payments"};
  const a=(health.driver_approvals || []).find(a=>a.session_id===s.session_id && a.approved && a.version===2 && a.state==="spending_authorised_wallet_permission_required" && Date.parse(a.expires_at)>now);
  if(!a){
    const pending=health.driver_approvals?.some(a=>a.session_id===s.session_id&&a.state==="awaiting_driver_consent"&&Date.parse(a.expires_at)>now);
    return {label:pending?"Awaiting consent":s.ended_at?"Review needed":"Driver approval needed",tone:"warn",detail:pending?"The driver must approve this session's private invitation.":s.ended_at?"Review this completed account to request consent, waive your charge or close a zero balance. Do not reuse a previous approval.":"Invite this driver and confirm this session before it ends.",target:s.ended_at?"payments":"drivers"};
  }
  if(!finite(s.net_cost_aud))return {label:"Pricing unavailable",tone:"warn",detail:"Resolve the session price data before settlement.",target:"payments"};
  if(Number(s.net_cost_aud)<0){
    if(!health.automatic_credit?.enabled || !a.credit_terms || !a.receiving_registered_at ||
       Date.parse(a.created_at)<Date.parse(health.automatic_credit.enabled_at) ||
       (s.ended_at && Date.parse(a.receiving_registered_at)>Date.parse(s.ended_at)))
      return {label:"Credit not ready",tone:"warn",detail:"Check the automatic-credit policy and this driver's receiving-wallet registration.",target:"drivers"};
    return {label:"Automatic credit checks enabled",tone:"info",detail:"The server will check this closed account, funding and payment limits. This is not a payment guarantee.",target:"payments"};
  }
  return {label:"Driver collection approved",tone:"info",detail:"The driver must keep BSV Browser open. Wallet permission and final checks still apply.",target:"drivers"};
}
export const styles = `
:host{display:block;min-width:0;max-width:100%;color:var(--primary-text-color,#152a3a);font-family:var(--primary-font-family,Arial,sans-serif);--accent:var(--primary-color,#186776);--muted:var(--secondary-text-color,#526472);--line:var(--divider-color,#d8e0e5);--surface:var(--card-background-color,#fff);--soft:var(--secondary-background-color,#f1f5f7);--text-xs:.78rem;--text-sm:.88rem;--text-base:1rem;--text-lg:1.22rem;--text-xl:1.8rem}
*{box-sizing:border-box}ha-card{display:block;background:var(--surface);color:inherit;padding:24px;border:1px solid var(--line);border-radius:16px;box-shadow:none}
h2,h3,p{margin:0}h2{font-size:var(--text-lg);font-weight:650}h3{font-size:var(--text-base);font-weight:650}
p{line-height:1.55;overflow-wrap:anywhere}p+p{margin-top:8px}.muted,.note,.eyebrow{color:var(--muted)}.note{font-size:var(--text-sm);line-height:1.5}.eyebrow{font-size:var(--text-xs);letter-spacing:.09em;text-transform:uppercase;font-weight:650}
.head,.row{display:flex;align-items:center;justify-content:space-between;gap:16px}.head{margin-bottom:24px}.row{flex-wrap:wrap}.stack{display:grid;gap:20px}
.badge{display:inline-flex;align-items:center;gap:8px;font-size:var(--text-xs);font-weight:650;padding:6px 10px;border-radius:6px;background:var(--soft);color:inherit}
.badge.good{color:var(--success-color,#217346)}.badge.warn{color:var(--warning-color,#945300)}.badge.info{color:var(--accent)}
.notice{padding:16px;background:var(--soft);border-radius:8px;font-size:var(--text-sm);line-height:1.5}.notice strong{display:block;margin-bottom:4px}
.metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.metric{padding:16px;background:var(--soft);border-radius:8px}.metric span{display:block;color:var(--muted);font-size:var(--text-sm)}.metric strong{display:block;font-variant-numeric:tabular-nums;font-size:var(--text-xl);line-height:1.3;margin-top:8px;font-weight:600}
.amount{font-size:var(--text-xl);font-weight:600;font-variant-numeric:tabular-nums}.section{margin-top:24px;padding-top:24px;border-top:1px solid var(--line)}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}button,.button{font:inherit;font-size:var(--text-sm);min-height:44px;border-radius:8px;padding:10px 16px;cursor:pointer;border:1px solid var(--line);color:inherit;background:var(--surface);text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:8px}
button.primary,.button.primary{color:var(--text-primary-color,#fff);background:var(--accent);border-color:var(--accent)}button.danger{color:var(--error-color,#b3261e)}button:disabled{opacity:.48;cursor:not-allowed}button:hover:not(:disabled),.button:hover{filter:brightness(.95)}:focus-visible{outline:3px solid var(--accent);outline-offset:3px}a{color:var(--accent)}
details{border-top:1px solid var(--line);margin-top:20px;padding-top:8px}summary{cursor:pointer;padding:12px 0;min-height:44px;font-size:var(--text-sm);font-weight:600}details[open]>summary{margin-bottom:8px}
label{display:block;font-size:var(--text-sm);line-height:1.5;margin:12px 0}input,textarea,select{font:inherit;box-sizing:border-box;color:inherit;background:var(--surface)}input:not([type=checkbox]),select,textarea{width:100%;padding:12px;border:1px solid var(--line);border-radius:8px;margin-top:6px;min-height:44px}input[type=checkbox]{margin-right:8px;width:18px;height:18px;vertical-align:middle;accent-color:var(--accent)}textarea{resize:vertical;font-family:monospace;font-size:var(--text-xs)}
code{display:block;overflow-wrap:anywhere;white-space:normal;font-size:var(--text-xs);line-height:1.6;font-family:monospace}dl{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;font-size:var(--text-sm)}dt{color:var(--muted)}dd{margin:0;text-align:right;overflow-wrap:anywhere}
.payment{padding:16px 0;border-bottom:1px solid var(--line)}.payment:last-child{border-bottom:0}.qr{background:white;max-width:220px;margin:16px auto;padding:8px}.qr svg{display:block;width:100%;height:auto}
ha-icon{--mdc-icon-size:22px}.steps{display:flex;gap:12px;list-style:none;padding:0;margin:20px 0;font-size:var(--text-sm);color:var(--muted)}.steps li{flex:1;padding:8px 0;border-bottom:2px solid var(--line)}.steps .done{border-color:var(--accent);color:var(--accent)}
[hidden]{display:none!important}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}.full{grid-column:1/-1}
@media(max-width:480px){ha-card{padding:20px 16px}.head{align-items:flex-start;gap:12px}.metrics{gap:8px}.metric{padding:12px}.metric strong{font-size:1.5rem}.form-grid{grid-template-columns:1fr}.head .badge{max-width:50%}.actions>*{flex:1}.steps{gap:8px;font-size:.8rem}}
`;

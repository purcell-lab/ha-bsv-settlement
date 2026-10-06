import {esc,num,stamp,short,energyMetrics,finite,chainRecordLink,stateLabel} from "./ui.js";
import {settlementRows,paymentStatus} from "./payment-status.js";
import {warningMessage} from "./quality.js";

export function evidenceDetails(row){
  const records=row.audit_records||[];
  if(records.length<2)return "";
  return `<details><summary>Retained evidence (${records.length} records)</summary>
    <p class="note">Historical projections, not additional amounts due.</p>
    <ul>${records.map(r=>`<li>${esc(r.source||"Saved record")}: ${esc(stateLabel(r.state||"Unknown"))}
      ${Number.isSafeInteger(r.amount_sats)?` · ${num(r.amount_sats)} sat`:""}
      ${r.txid?chainRecordLink(r.txid):""}</li>`).join("")}</ul></details>`;
}

export function sessionTableRows(health,sessions,direction="all"){
  const rows=settlementRows(health);
  for(const s of sessions){
    if(!rows.some(r=>r.session_id===s.session_id))rows.push({
      session_id:s.session_id,transaction_id:s.ocpp_transaction_id,
      direction:finite(s.net_cost_aud)&&Number(s.net_cost_aud)<0?"operator_to_driver":"driver_to_operator",
      state:s.ended_at?"awaiting_account_approval":"waiting_for_session_end"});
  }
  return rows.map(row=>{
    const session=sessions.find(s=>s.session_id===row.session_id);
    return {row,session,energy:energyMetrics(session),status:paymentStatus(row,sessions)};
  }).filter(r=>direction==="all"||r.status.direction===direction||
    r.row.evidence_conflict&&!r.row.direction);
}

export function sessionTable(health,sessions,direction){
  const rows=sessionTableRows(health,sessions,direction);
  if(!rows.length)return '<p class="notice section">No sessions recorded for this payment direction.</p>';
  return `<section class="section"><h3>Session accounts</h3>
    <p class="note">AUD averages exclude wallet fees. Scroll the table to see all columns. Missing historical meter data is not estimated.</p>
    <div class="table-scroll" role="region" aria-label="Session accounts" tabindex="0"><table>
    <thead><tr><th scope="col">Session / ended</th><th scope="col">Energy Imported to EV<br>kWh · average $/kWh</th><th scope="col">Energy Imported from EV<br>kWh · average $/kWh</th><th scope="col">Net AUD</th><th scope="col">Wallet / fee</th><th scope="col">Settlement status</th></tr></thead>
    <tbody>${rows.map(({row,session:s,energy:e,status:p})=>`<tr>
      <th scope="row"><details><summary>${esc(short(p.reference))}</summary><code>${esc(p.reference)}</code>${row.txid?`<p class="note">BSV transaction</p>${chainRecordLink(row.txid)}`:""}</details><span class="note">${s?esc(s.ended_at?stamp(s.ended_at):"In progress"):"Time unavailable"}</span></th>
      <td>${num(e.toEV.kwh,2)}<br><span class="note">${num(e.toEV.average,4)}</span></td>
      <td>${num(e.fromEV.kwh,2)}<br><span class="note">${num(e.fromEV.average,4)}</span></td>
      <td>${num(s?.net_cost_aud??row.net_amount_aud,2)}</td>
      <td>${row.evidence_conflict?'Not combined<br><span class="note">See retained evidence</span>':`${num(row.amount_sats)} sat<br><span class="note">Fee ${num(row.fee_sats)} sat</span>${row.max_fee_sats!==undefined?`<br><span class="note">Fee cap ${num(row.max_fee_sats)} sat</span>`:""}`}</td>
      <td><strong>${esc(p.title)}</strong><p class="note">${esc(p.detail)}</p>${evidenceDetails(row)}${warningMessage(s?.quality_flags||row.quality_flags)?`<p class="note">Metering warning (non-blocking): ${esc(warningMessage(s?.quality_flags||row.quality_flags))}</p>`:""}</td>
    </tr>`).join("")}</tbody></table></div></section>`;
}

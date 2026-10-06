// Display-only consolidation. No ledger mutation or settlement authority.
const held=new Set(["claimed","wallet_attempt_reserved","submission_authorised","broadcast_unknown",
  "expired_awaiting_reconciliation","submitted","provider_unconfirmed","driver_payment_provider_unconfirmed"]);
const fields=["session_id","state","source","txid","output_index","direction","amount_sats","fee_sats",
  "budget_id","review_id","credit_id","checked_at","wallet_receipt_status","wallet_imported_at"];
const state=r=>String(r.state||"").replace(/^(driver_payment_|credit_)(provider_)/,"$2");
const values=(rows,key)=>new Set(rows.map(r=>r[key]).filter(v=>v!==undefined&&v!==null));
const receipt=r=>r.wallet_receipt_status==="wallet_reported_accepted"&&Number.isFinite(Date.parse(r.wallet_imported_at));

export function consolidateSessionEvidence(records){
  const audit=records.map(r=>Object.fromEntries(fields.filter(k=>r[k]!==undefined).map(k=>[k,r[k]])));
  const payments=records.filter(r=>r.txid);
  let candidates=payments.length?payments:records;
  let conflict=values(payments,"txid").size>1;
  if(payments.length){
    // Differing financial fields must not be blended into a synthetic payment.
    conflict||=["direction","amount_sats","fee_sats","output_index"].some(k=>values(payments,k).size>1);
    // A separate held signing attempt remains a risk even beside a paid record.
    conflict||=records.some(r=>!r.txid&&held.has(r.state));
    if(values(payments.map(r=>({state:state(r)})),"state").size>1){
      const dated=payments.map(r=>({row:r,time:Date.parse(r.checked_at)}));
      if(dated.some(r=>!Number.isFinite(r.time)))conflict=true;
      else{
        const latest=Math.max(...dated.map(r=>r.time));
        candidates=dated.filter(r=>r.time===latest).map(r=>r.row);
        conflict||=values(candidates.map(r=>({state:state(r)})),"state").size>1;
      }
    }
  }else{
    const closed=records.filter(r=>["waived","closed_zero"].includes(r.state));
    const reserved=records.filter(r=>held.has(r.state));
    if(closed.length)candidates=closed;
    else if(reserved.length){
      candidates=reserved;
      const owners=new Set(reserved.map(r=>r.budget_id||r.review_id).filter(Boolean));
      conflict=owners.size>1;
    }
  }
  // Last indexed projection wins only within compatible evidence; no blanket
  // spread of different records (which could retain a stale amount/error/txid).
  const winner={...candidates.at(-1)};
  if(!conflict&&payments.length){
    const accepted=payments.filter(receipt).sort((a,b)=>Date.parse(a.wallet_imported_at)-Date.parse(b.wallet_imported_at)).at(-1);
    if(accepted){
      winner.wallet_receipt_status=accepted.wallet_receipt_status;
      winner.wallet_imported_at=accepted.wallet_imported_at;
    }
  }
  if(conflict){
    return {session_id:winner.session_id,transaction_id:winner.transaction_id,
      direction:values(records,"direction").size===1?winner.direction:null,
      state:"settlement_evidence_conflict",evidence_conflict:true,audit_records:audit};
  }
  return {...winner,audit_records:audit};
}

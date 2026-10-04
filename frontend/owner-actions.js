import {sessionTableRows} from "./session-table.js";
import {finite,sessionStatus,stateLabel} from "./ui.js";

const terminal=new Set(["waived","closed_zero","provider_confirmed","driver_payment_provider_confirmed"]);
const held=new Set(["claimed","wallet_attempt_reserved","recovery_ready","submission_authorised",
  "broadcast_unknown","expired_awaiting_reconciliation","submitted","provider_unconfirmed",
  "driver_payment_provider_unconfirmed"]);

// UI routing only. Every consequential operation rechecks its exact saved target on the server.
export function ownerActionRows(health,sessions,now=Date.now()){
  const rows=sessionTableRows(health,sessions,"driver_to_operator");
  for(const a of health.driver_approvals||[]){
    if(!a.session_id||a.session_id.startsWith("reservation:")||a.version===3||
      rows.some(v=>v.row.session_id===a.session_id)||
      sessions.some(s=>s.session_id===a.session_id&&finite(s.net_cost_aud)&&Number(s.net_cost_aud)<0))continue;
    // Do not infer a debt from a receiving-only registration.
    if(!a.reviewed_closed_account)continue;
    rows.push({row:{session_id:a.session_id,state:a.state,direction:"driver_to_operator"},session:null});
  }
  return rows.map(({row,session})=>{
    const approvals=(health.driver_approvals||[]).filter(a=>a.session_id===row.session_id);
    const approval=approvals.find(a=>a.budget_id===row.budget_id)||
      approvals.find(a=>!["revoked","charge_waived","expired"].includes(a.state)&&Date.parse(a.expires_at)>now);
    const budgetId=row.budget_id||approval?.budget_id;
    const complete=terminal.has(row.state)&&(!row.state.includes("confirmed")||!!row.txid);
    const actions=[],add=(id,label,primary=false)=>actions.push({id,label,primary});
    let title=stateLabel(row.state),note="";
    if(complete){
      note=row.state==="waived"?"Closed without payment. This is not a refund.":"No further collection is required.";
    }else if(row.txid||held.has(row.state)||String(row.state).includes("confirmed")){
      title=row.state==="broadcast_unknown"?"Payment outcome uncertain":stateLabel(row.state);
      note="Reconcile the original attempt. Do not create another payment or invitation.";
      add("check","Check provider status",true);
      if(budgetId)add("recovery","Review held collection");
      if(row.state==="recovery_ready"&&budgetId)add("approval","View approval / driver link");
      if(row.source==="manual"&&row.review_id)add("manual","Review saved payment request");
    }else if(row.source==="manual"){
      note="Continue this exact saved request, not a new review for the latest session.";
      if(row.review_id)add("manual","Review saved payment request",true);
      if(row.review_id&&["awaiting_driver_payment"].includes(row.state))
        add("waiver","Review waiver");
      if(!row.review_id)note="Saved review ID is unavailable. Install the matching integration update before opening this request.";
    }else if(session&&!session.ended_at){
      title="Session in progress";note="Final account is not fixed. Set up this driver before the session ends.";
      add("drivers","Open driver setup",true);
    }else if(budgetId&&(row.source==="driver"||approval)){
      const expired=Date.parse(row.expires_at||approval?.expires_at)<=now;
      title=expired?"Approval expired":approval?.state==="awaiting_driver_consent"?"Awaiting driver consent":title;
      note=expired?"An expired approval cannot collect. Inspect the saved request before replacing or closing it.":
        "Open this session's saved approval. The driver must use the original wallet.";
      add("approval","View approval / driver link",true);
      if(row.source==="driver"&&["ready","recovery_ready","wallet_attempt_reserved"].includes(row.state))add("waiver","Review waiver");
      if(row.source!=="driver")add("closure","Review consent / waiver");
    }else{
      const status=session?sessionStatus(session,health,now):null;
      title=status?.label||title;
      note=status?.detail||"Review the retained account before choosing a resolution.";
      add("closure","Review consent / waiver",true);
    }
    return {row,session,approval,budgetId,complete,title,note,actions};
  }).sort((a,b)=>Number(a.complete)-Number(b.complete));
}

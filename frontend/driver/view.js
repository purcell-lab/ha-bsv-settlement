// Existing-session approvals use terms.session_id and need no binding object.
// Only a genuinely unbound future reservation can yield the primary view.
export function showOngoingOverview({accepted,ongoingCount,sessionMode,binding,state}){
  return !!accepted && ongoingCount>0 && sessionMode==="next_session_reservation" &&
    !binding?.session_id && state==="waiting_for_operator_binding";
}

export function ongoingCreditMessage(state,imported=false){
  if(imported&&state==="provider_confirmed")return "Wallet receipt acceptance recorded";
  return ({
    waiting_for_session_end:"Session in progress. Any operator credit will be checked when the session ends.",
    no_operator_credit:"No operator credit is due for this session.",
    credit_queued:"Operator credit queued for funding and account checks.",
    credit_blocked:"Operator credit needs attention. Contact the operator.",
    submitted:"Operator credit submitted. Waiting for provider evidence.",
    provider_unconfirmed:"Operator credit seen by the chain provider, awaiting confirmation.",
    provider_confirmed:"Operator credit confirmed by the chain provider. Wallet receipt acceptance not recorded.",
    broadcast_unknown:"Operator credit submission is uncertain. Tracking the existing payment.",
  })[state] || "Operator credit status needs review.";
}

export function driverView({accepted,registered,connected,state,credit,imported,hasInvitation,creditEnabled=true,closedSession=false}){
  if(!hasInvitation)return {title:"Charge. Export. Settle.",subtitle:"Open your operator's private link inside BSV Browser.",stage:"start"};
  if(state==="waived")return {title:"Your session charge was waived",subtitle:"No further collection is authorised for this session. A waiver does not refund any funds already sent.",stage:"settled"};
  // Historical receipt reporting does not renew expired spending consent.
  if(credit&&state==="provider_confirmed")return imported?
    {title:"Wallet receipt acceptance recorded",subtitle:"Your wallet reports acceptance of this confirmed credit. No further payment is needed.",stage:"settled"}:
    {title:"Your credit is confirmed",subtitle:"Wallet acceptance is not recorded. Reconnect the receiving wallet to import and report the existing receipt. No new payment.",stage:"approved",reconnect:"Receive credit in wallet"};
  if(!accepted)return closedSession?
    {title:"Review your completed session",subtitle:"Review the frozen energy account and any metering warnings. Approval can start payment immediately; it does not start another charging session.",stage:"approve"}:
    {title:"Approve your charging session",subtitle:"Review today's rates and your spending limit. No payment is sent now.",stage:"approve"};
  if(["submitted","provider_unconfirmed","broadcast_unknown"].includes(state))return {
    title:state==="broadcast_unknown"?"Checking payment submission":
      state==="provider_unconfirmed"?"Awaiting block confirmation":"Payment awaiting confirmation",
    subtitle:"We are tracking the existing transaction. Do not send another payment.",stage:"approved"};
  if(state==="provider_confirmed")return {title:"Session payment confirmed",subtitle:"Your payment is confirmed by the chain provider.",stage:"settled"};
  if(state==="no_payment_due")return {title:"No payment due",subtitle:"Your final session account is zero.",stage:"settled"};
  if(state==="recovery_ready")return {title:"Review and resume collection",subtitle:"The operator reviewed the previous attempt. Check the same session amount before explicitly resuming. No automatic retry has occurred.",stage:"approved",reconnect:"Review and resume collection"};
  if(state==="wallet_attempt_reserved")return {title:"Collection needs review",subtitle:"The existing attempt is held to prevent duplicate payment. Ask the operator to review it; do not start another payment.",stage:"approved"};
  if(state && /blocked|failed|expired|revoked|rejected/.test(state))return {title:"Settlement needs attention",subtitle:"No new payment should be sent. Contact the operator to review the saved account and transaction status.",stage:"approved"};
  if(creditEnabled&&!registered)return {title:"Finish connecting your wallet",subtitle:"Reconnect before session end to register where credits should be sent.",stage:"approved",reconnect:"Register receiving wallet"};
  if(state==="waiting_for_operator_binding")return {title:"Approval saved",subtitle:"The operator must match this approval to your charging session.",stage:"approved",reconnect:connected?null:"Reconnect wallet"};
  if(closedSession)return {title:"Completed-account payment approved",subtitle:"Keep BSV Browser and this page open for collection. Wallet permission and final payment checks still apply.",stage:"approved",reconnect:connected?null:"Reconnect wallet"};
  return {title:"Your session is approved",subtitle:"Keep BSV Browser open for driver charges. Eligible operator credits can be sent while it is closed.",stage:"approved",reconnect:connected?null:"Reconnect wallet"};
}

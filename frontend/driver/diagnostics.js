export const stageLabels={
  check_quote:"Check the signed session account",wallet_identity:"Check driver wallet identity",
  wallet_network:"Check driver wallet network",sign_claim:"Sign the collection claim",
  claim_collection:"Reserve the collection attempt",create_draft:"Create the unsigned wallet draft",
  inspect_draft:"Validate the unsigned wallet draft",authorise_draft:"Request the one-use signing permit",
  recheck_wallet:"Recheck wallet before signing",sign_payment:"Sign the authorised transaction",
  inspect_signed:"Check the signed transaction",submit_payment:"Submit the exact signed transaction",
};
export const failureMessages={
  network_request_failed:"A network request failed; the response or wallet outcome may be unknown.",
  request_timeout:"A request timed out; the response or wallet outcome may be unknown.",
  wallet_rejected:"The wallet rejected or could not complete the requested operation.",
  validation_failed:"The quote, wallet draft or signing-permit checks did not pass.",
  unexpected_error:"Collection stopped unexpectedly; inspect the wallet before recovery.",
};
export const draftReasons={
  unsigned_draft_required:"The wallet did not return an unsigned draft.",
  invalid_beef:"The wallet transaction could not be decoded as complete Atomic BEEF.",
  unsupported_shape:"The transaction version, locktime, input count or output count is unsupported.",
  funding_evidence_missing:"Complete matching funding transactions and final input sequences are required.",
  payment_output_mismatch:"The outputs do not match the exact operator payment and supported change format.",
  invalid_fee:"The draft fee is not a valid nonnegative integer.",
  fee_limit_exceeded:"The wallet draft fee exceeds the approved fee cap.",
  total_limit_exceeded:"The total wallet debit exceeds the approved spending limit.",
};
export const draftDetailFields=["payment_sats","fee_sats","fee_cap_sats","total_debit_sats","total_cap_sats","input_count","output_count"];
export function draftError(reason,details={}){
  const e=Error(draftReasons[reason]);e.draftReason=reason;e.draftDetails=details;return e;
}
export function draftDetailText(d={}){
  return [["payment_sats","Payment"],["fee_sats","Wallet fee"],["fee_cap_sats","Fee cap"],
    ["total_debit_sats","Total debit"],["total_cap_sats","Total cap"]]
    .filter(([key])=>Number.isSafeInteger(d[key])).map(([key,label])=>`${label}: ${d[key]} sat.`).join(" ");
}
export function failureDiagnostic(stage,error){
  const message=String(error?.message||"");
  const code=/failed to fetch|network|load failed/i.test(message)?"network_request_failed":
    /timeout|timed out|abort/i.test(message)?"request_timeout":
    /denied|rejected|permission/i.test(message)?"wallet_rejected":
    /quote|draft|fee|recipient|funding|permit|signature|transaction|mainnet/i.test(message)?"validation_failed":"unexpected_error";
  // Do not send arbitrary wallet errors, URLs, signed bytes or secrets to HA.
  const result={event_id:crypto.randomUUID(),stage:stageLabels[stage]?stage:"check_quote",code};
  if(["inspect_draft","inspect_signed"].includes(stage)&&draftReasons[error?.draftReason]){
    result.reason=error.draftReason;result.code="validation_failed";
    result.details=Object.fromEntries(draftDetailFields.filter(k=>
      Number.isSafeInteger(error.draftDetails?.[k])&&error.draftDetails[k]>=0&&
      error.draftDetails[k]<=2100000000000000).map(k=>[k,error.draftDetails[k]]));
  }
  return result;
}
export function describeFailure(d){
  return d && stageLabels[d.stage] && failureMessages[d.code] ?
    `Collection paused at “${stageLabels[d.stage]}”. ${draftReasons[d.reason]||failureMessages[d.code]} ${draftDetailText(d.details)} No new payment will be attempted automatically.` : "";
}

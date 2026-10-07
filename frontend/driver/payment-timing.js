/** Historical observations, never a replacement for current payment status. */
export function paymentTimingRows(payment){
  const rows=[
    ["Broadcast attempt recorded","broadcast_attempted_at"],
    ["Broadcast acknowledged by provider","broadcast_acknowledged_at"],
    ["First provider confirmation observed","provider_first_confirmed_at"],
  ];
  if(payment.direction==="operator_to_driver")
    rows.push(["Wallet acceptance reported","wallet_accepted_reported_at"]);
  return rows.map(([label,key])=>{
    const value=payment[key],date=typeof value==="string"?new Date(value):null;
    return [label,date&&Number.isFinite(date.getTime())?
      date.toISOString().replace("T"," ").replace("Z"," UTC"):"Not recorded"];
  });
}

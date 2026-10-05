// Presentation only: never grants authority, connects a wallet or retries a payment.
export function nextAction(states) {
  for (const key of ["save", "report", "monthly", "approve", "register", "sync", "connect"]) {
    if (states[key]?.enabled && states[key]?.primary !== false) return key;
  }
  return null;
}

export function sessionJourney({accepted, state, ended, credit, imported, failure, expired}) {
  if (state === "waived") return {step: 2, title: "Charge waived", hint: "No further payment is due under this request. This is not a refund."};
  if (state === "provider_confirmed") return {
    step: 2, title: credit ? (imported ? "Credit received" : "Credit confirmed") : "Payment confirmed",
    hint: credit && !imported ? "The payment is confirmed. Add its receipt to your wallet; this does not send another payment." :
      credit ? "Your wallet reports receipt acceptance. No further action is needed." : "The chain provider confirms your payment. No further action is needed."
  };
  if (state === "no_payment_due") return {step: 2, title: "Nothing to pay", hint: "Your final energy account is zero."};
  if (["provider_unconfirmed", "submitted"].includes(state)) return {
    step: 2, title: "Awaiting block confirmation", hint: "Your payment has been submitted. We are tracking it. Do not pay again."
  };
  if (state === "broadcast_unknown") return {
    step: 2, title: "Checking your payment", hint: "Submission is uncertain. Contact the operator if this persists. Do not send another payment."
  };
  if (state === "recovery_ready") return {
    step: 2, title: "Review and resume payment", hint: "The operator has reviewed the earlier attempt. Review the amount before confirming that collection can resume."
  };
  if (failure || state === "wallet_attempt_reserved" || /blocked|failed|rejected|revoked/.test(state || "")) return {
    step: ended ? 2 : 1, title: "The operator needs to check this", hint: "Payment is paused for safety. Contact the operator; do not pay again."
  };
  if (!accepted && expired) return {step: 0, title: "Approval link expired", hint: "Ask the operator for a new invitation. No new payment is authorised here."};
  if (!accepted) return {
    step: 0,
    title: ended ? "Authorise this completed session" : "Ready to charge?",
    hint: ended ? "Approval connects your wallet and can collect this completed account now. Review the amount and fees first." :
      "One action connects your wallet and authorises this budget. Charging starts separately; no funds are reserved."
  };
  if (ended) return {step: 2, title: credit ? "Your credit is being processed" : "Finish your payment",
    hint: credit ? "The operator sends eligible credits automatically. Wallet receipt acceptance is a separate step." :
      "Keep this page and your wallet available. Any wallet payment prompts still need your approval."};
  if (state === "waiting_for_operator_binding") return {step: 0, title: "Budget approved",
    hint: "Waiting for the operator to confirm your session. You do not need to approve again. Payment collection is not ready until the session is matched. This page does not control charging."};
  return {step: 1, title: "Your charging session", hint: "Keep this page and wallet available for automatic collection when the session ends. Use the charger or vehicle controls to stop."};
}

export function renderSteps(step) {
  return ["Authorise budget", "Charge / export", "Settle"].map((label, i) =>
    `<li ${i === step ? 'aria-current="step"' : ""} class="${i < step ? "complete" : ""}"><span>${i + 1}</span>${label}</li>`).join("");
}

// Move existing, guarded controls rather than make a second implementation.
export function simplifySessionLayout() {
  const $ = id => document.getElementById(id);
  const main = $("main"), content = document.createElement("div");
  content.className = "simple-session-content";
  document.querySelector(".driver-toolbar").after(content);
  const account = document.createElement("details");
  account.id = "approved-terms";
  account.innerHTML = "<summary>Approved budget and terms</summary>";
  const livePanel=document.createElement("section");
  livePanel.id="live-session-panel";
  livePanel.innerHTML="<h2>Rates and session energy</h2>";
  livePanel.append($("prices"),$("energy-summary"),$("wallet-direction-note"));
  const support = document.createElement("details");
  support.className = "driver-support";
  support.innerHTML = "<summary>Session details and links</summary>";
  for (const id of ["pairing-section", "private-link-section", "session-reference", "result", "load-section"]) support.append($(id));
  const fullTerms=document.createElement("details");
  fullTerms.id="full-approval-terms";fullTerms.innerHTML="<summary>Full approval terms and pricing</summary>";
  for(const item of [...$("terms").querySelectorAll(":scope > details"),...$("wallet-section").querySelectorAll(":scope > details")]){
    // One disclosure level: flatten existing small technical accordions.
    const title=document.createElement("h3");title.textContent=item.querySelector("summary").textContent;
    fullTerms.append(title,...Array.from(item.children).filter(el=>el.tagName!=="SUMMARY"));item.remove();
  }
  fullTerms.append($("wallet-section").querySelector("dl"),$("approval-terms"));
  const older = document.createElement("details");
  older.innerHTML = "<summary>Other credit receipts</summary>";
  $("ongoing-section").before(older);older.append($("ongoing-section"));older.id="other-credit-details";
  content.append($("terms"), $("wallet-section"), document.querySelector(".journey-primary"),
    $("approval-action"), $("status"), $("connection-status"), $("collection-section"), fullTerms, account, older, support,
    document.querySelector(".journey-more"));
  content.prepend(livePanel);
  document.querySelector(".layout").remove();
  $("collection-section").querySelector(".section-head").after($("credit-status"));
  document.querySelector(".energy-flow").hidden = true;
  $("status").classList.add("driver-feedback");
  return {update({accepted, receiptOnly, step}) {
    const folded = accepted || receiptOnly;
    if ($("terms").parentElement !== (folded ? account : content)) {
      if (folded) account.append($("terms"), $("wallet-section"));
      else content.prepend($("terms"), $("wallet-section"));
    }
    // Rates and metering are independent of payment state or collapsed terms.
    content.prepend(livePanel);
    livePanel.hidden=$("terms").hidden;
    account.hidden = !folded;
    if(fullTerms.parentElement!==(folded?account:content)){
      if(folded)account.append(fullTerms);else $("collection-section").after(fullTerms);
    }
    older.hidden = $("ongoing-section").hidden;
    document.querySelector(".journey-primary").hidden = false;
    // Before consent, all financial terms precede the only primary action.
    const anchor=folded?$("collection-section"):$("wallet-section");
    if(anchor.nextElementSibling!==document.querySelector(".journey-primary"))anchor.after(document.querySelector(".journey-primary"));
    main.dataset.journeyStep = String(step);
  }};
}

import {esc,num,stamp,short,styles,sessionStatus,energyMetrics,currentPrice,provisionalDisplay} from "./ui.js";
import {awaitingApproval,approvalUrl,drawApprovalQR,pendingForSession} from "./approval-qr.js";
import {warningMessage} from "./quality.js";
import {confirmSessionMatch} from "./session-match.js";
import {adjustmentConfig,payAdjustment,adjustmentFeedback,adjustmentRequest,adjustmentFinished,adjustmentAmount} from "./energy-adjustment.js";
class BSVOperatorCard extends HTMLElement{
  setConfig(c){this.config=c;if(!this.shadowRoot)this.attachShadow({mode:"open"});this.render();}
  set hass(h){this._hass=h;this.render();}
  disconnectedCallback(){clearTimeout(this.approvalExpiry);this.signature=null;}
  getCardSize(){return 6;}
  render(){
    const c=this.config,h=this._hass;if(!c||!h)return;
    const ws=h.states[c.wallet_entity], health=ws?.attributes||{}, proxy=h.states[c.proxy_entity];
    const balance=h.states[c.balance_entity], stale=!ws||["unavailable","unknown"].includes(ws.state);
    const mode=c.mode||"overview";
    const sources=proxy?.attributes?.source_entities||{};
    const buy=currentPrice(h.states[c.import_price_entity||sources.import_price]);
    const sell=currentPrice(h.states[c.export_price_entity||sources.export_price]);
    const rateState=h.states[c.rate_entity];
    const signature=JSON.stringify([mode,buy,sell,rateState?.state,proxy?.state,proxy?.attributes,ws?.state,health,balance?.state,h.user?.is_admin]);
    if(signature===this.signature)return;this.signature=signature;clearTimeout(this.approvalExpiry);
    const link=(path,text,primary=false)=>`<a class="button ${primary?"primary":""}" href="/bsv-settlement/${path}">${esc(text)}</a>`;
    const sessions=[proxy?.attributes.latest_session,proxy?.attributes.previous_session].filter(Boolean);
    let body;
    if(mode==="wallet"){
      body=`<div class="head"><div><p class="eyebrow">Operator funds</p><h2>Your mainnet wallet</h2></div><ha-icon icon="mdi:wallet-outline"></ha-icon></div>
      <div class="metrics"><div class="metric"><span>Confirmed spendable</span><strong>${stale?"Unavailable":num(balance?.state)} <small>sat</small></strong></div><div class="metric"><span>Pending change</span><strong>${stale?"Unavailable":num(health.pending_change_sats)} <small>sat</small></strong></div></div>
      <p class="note" style="margin-top:12px">Pending change is money returning to you from a submitted payment. It is not yet confirmed or available to spend.</p>
      <div class="section"><div class="row"><span class="badge ${health.chain_error||health.payment_check_error||stale||!health.chain_checked_at?"warn":"good"}">${stale?"Wallet unavailable":health.chain_error?"Balance check failed":health.payment_check_error?"Payment evidence unavailable":health.chain_checked_at?"Provider check complete":"Balance not checked"}</span><span class="note">Checked ${esc(stamp(health.chain_checked_at))}</span></div>
      ${health.chain_error?`<p class="notice">${esc(health.chain_error)}. Try a fresh provider check. Do not resend a payment.</p>`:""}
      ${!health.chain_error&&health.payment_check_error?`<p class="notice">The provider has no evidence yet for the unresolved operator payment. The balance is still shown. Do not resend the payment; use the guarded recovery workflow.</p>`:""}
      <button id="refresh" style="margin-top:16px" ${stale||!h.user?.is_admin?"disabled":""}><ha-icon icon="mdi:refresh"></ha-icon>Check balance and payment</button><p id="feedback" role="status" class="note"></p></div>
      <details><summary>How the balance works</summary><p class="note">Checked every five minutes, every minute while an operator payment is unresolved, and after settlement events. Provider-reported, not independently verified. A payment can consume a large funding output and return the remainder as change.</p></details>`;
    }else{
      const s=sessions[0], summary=stale?{label:"Wallet unavailable",tone:"warn",detail:"Reconnect the wallet before assessing payment readiness.",target:"wallet"}:sessionStatus(s,health);
      const unavailable=!proxy||["unknown","unavailable"].includes(proxy.state);
      const estimate=provisionalDisplay(s,health,rateState);
      const energy=energyMetrics(s),settlementPath=summary.payment?.direction==="operator_to_driver"||
        (summary.payment&&!summary.payment.direction)||Number(s?.net_cost_aud)<0?"operator-credits":"payments";
      let adjustmentBlocked="";
      try{adjustmentConfig(h,c);}catch(e){adjustmentBlocked=e.message;}
      const adjustment=this.adjustmentResult?adjustmentFeedback(this.adjustmentResult):null;
      body=`<div class="head"><div><p class="eyebrow">Charging & settlement</p><h2>${unavailable?"Recorder unavailable":!s?"Ready for the next driver":s.ended_at?"Latest session complete":"Session in progress"}</h2></div><ha-icon icon="mdi:ev-station"></ha-icon></div>
      <div class="metrics" aria-label="Current buy and sell prices">${[[buy,"Buy (Import/ EV Charging) rate"],[sell,"Sell (Export/ V2G) rate"]].map(([p,label])=>`<div class="metric"><span>${label}</span><strong>${num(p.value,4)} <small>$/kWh</small></strong><p class="note">${p.available?`${p.estimated?"Estimated current rate":"Current rate"} · until ${esc(stamp(p.end))}`:"Unavailable or stale"}</p></div>`).join("")}</div>
      <p class="note" style="margin:12px 0 20px">Current buy and sell rates in AUD. These are not the session-average prices or a fixed quote.</p>
      ${unavailable?`<p class="notice">Session data is unavailable. Do not infer a completed payment from an old reading.</p>`:`
      <div class="notice"><strong>${esc(summary.label)}</strong>${esc(summary.detail)}</div>
      ${warningMessage(s?.quality_flags)?`<p class="notice" style="margin-top:12px" role="note"><strong>Metering warning · settlement not blocked by this flag</strong>${esc(warningMessage(s.quality_flags))} Consent, valid amounts, complete pricing and payment safety checks still apply.</p>`:""}
      <div class="actions">${summary.matchBudgetId?`<button id="match-session" class="primary" ${this.matchBusy||!h.user?.is_admin?"disabled":""}>Confirm this session</button>`:link(summary.target==="payments"?settlementPath:summary.target,summary.target==="payments"?"View settlement":summary.target==="wallet"?"Check wallet":"Set up driver",true)}${summary.matchBudgetId?link("drivers","Driver setup"):""}${link("wallet","View funds")}</div>
      <p id="match-feedback" class="note" role="status">${esc(this.matchMessage||"")}</p>
      <section id="approval-qr" class="section" hidden aria-label="Driver approval"></section>
      ${s?`<div class="section"><div class="row"><div><p class="note">${esc(estimate.label)}</p><p class="amount">${esc(estimate.value)}</p>${estimate.reason?`<p class="note" role="status">${esc(estimate.reason)}</p>`:""}<p class="note">AUD ${num(s.net_cost_aud===null||s.net_cost_aud===undefined||s.net_cost_aud===""?null:Math.abs(Number(s.net_cost_aud)),2)} · ${estimate.rate===null?"Conversion unavailable":`${num(estimate.rate,2)} sat/AUD (${estimate.fixed?"session rate":"current indicative rate"})`}</p><p class="note">Estimate only; network fees excluded. Actual payment and confirmation appear in settlement.</p></div><span class="badge ${summary.tone}">${esc(summary.label)}</span></div>
      <dl><dt>Energy Imported to EV</dt><dd>${num(energy.toEV.kwh,2)} kWh<br><span class="note">Average ${num(energy.toEV.average,4)} $/kWh</span></dd><dt>Energy Imported from EV</dt><dd>${num(energy.fromEV.kwh,2)} kWh<br><span class="note">Average ${num(energy.fromEV.average,4)} $/kWh</span></dd><dt>${s.ended_at?"Ended":"Started"}</dt><dd>${esc(stamp(s.ended_at||s.energy_started_at||s.opened_at))}</dd><dt>Session reference</dt><dd>${esc(short(s.ocpp_transaction_id))}</dd></dl>
      <p class="note">Energy-weighted session averages in AUD, not current live prices. Negative rates retain their sign. Network fees excluded; zero energy has no average price.</p>
      <details><summary>Full session reference and meter notes</summary><code>${esc(s.ocpp_transaction_id)}</code><p class="note">Sensor-derived proxy ID, not a charger-issued OCPP ID. Interval costs are provisional, not a certified bill.</p></details></div>`:""}`}
      <section class="section" aria-label="Separate 5 kWh payments"><h3>5 kWh payments</h3>
      <p class="note">Current registered driver · separate from metered sessions. A click sends an operator-funded credit immediately, up to 1,000 sat including the quoted network fee. Driver debits create a request that the driver must pay. Negative rates reverse the direction.</p>
      <div class="actions">${["export","import"].map(direction=>{const amount=adjustmentAmount(direction==="export"?sell:buy,rateState,direction);return `<button id="adjust-${direction}" ${adjustmentBlocked||this.adjustmentBusy||!amount.available?"disabled":""}><ha-icon icon="mdi:cash-${direction==="export"?"plus":"minus"}"></ha-icon>${this.adjustmentDirection===direction&&adjustmentFinished(this.adjustmentResult)?"New payment: ":""}${esc(amount.available?amount.label:`5 kWh ${direction} · ${amount.label}`)}</button>`;}).join("")}</div>
      <p class="note">Indicative payment amount, excluding fees. The server freezes the current valid rate when clicked.</p>
      <p class="note" role="status">${esc(this.adjustmentMessage||adjustment?.text||adjustmentBlocked||"Uses the current buy/sell rates above. No additional operator review step.")}</p>
      ${adjustment?link(adjustment.path,"View adjustment status"):link("operator-credits","View driver credits")}
      </section>
      ${(proxy?.attributes.issues||[]).length?`<p class="notice">Recorder needs attention: ${esc(proxy.attributes.issues.join(", "))}</p>`:""}
      ${sessions[1]?`<div class="section"><div class="row"><h3>Previous session</h3><span class="badge">${esc(sessionStatus(sessions[1],health).label)}</span></div><p class="note">${esc(short(sessions[1].ocpp_transaction_id))} · ${esc(stamp(sessions[1].ended_at))} · AUD ${num(sessions[1].net_cost_aud,2)}</p></div>`:""}
      <p class="note section">Session data updated ${esc(stamp(proxy?.attributes.updated_at))}. Charging control is separate from wallet settlement.</p>`;
    }
    const opened=[...this.shadowRoot.querySelectorAll("details[open]")].map(d=>d.querySelector("summary")?.textContent);
    this.shadowRoot.innerHTML=`<style>${styles}</style><ha-card>${body}</ha-card>`;
    for(const direction of ["import","export"]){
      const button=this.shadowRoot.getElementById("adjust-"+direction);
      if(button)button.onclick=async()=>{
        if(this.adjustmentBusy)return;
        this.adjustmentBusy=true;this.adjustmentMessage="Processing one payment attempt…";
        this.signature=null;this.render();
        try{
          const ids=adjustmentConfig(this._hass,c);
          // A lost response/reload retains the same nonsecret UUID. Only a button
          // explicitly labelled New payment can rotate a known terminal result.
          const id=adjustmentRequest(localStorage,`${ids.config_entry_id}:${ids.proxy_config_entry_id}:${ids.conversion_rate_entity}`,
            direction,this.adjustmentDirection===direction&&adjustmentFinished(this.adjustmentResult));
          this.adjustmentDirection=direction;
          // A failed NEW payment must not retain an older terminal result and
          // accidentally rotate the UUID again on its next retry.
          this.adjustmentResult=null;
          this.adjustmentResult=await payAdjustment(this._hass,c,direction,id);
          this.adjustmentMessage="";
        }catch(e){this.adjustmentMessage=`${e.message||"Payment status unavailable"}. Check settlement before retrying; no automatic retry will occur.`;}
        finally{this.adjustmentBusy=false;this.signature=null;this.render();}
      };
    }
    const pending=mode==="overview"&&!stale&&h.user?.is_admin&&
      proxy&&!["unknown","unavailable"].includes(proxy.state)&&!(proxy.attributes?.issues||[]).length?
      pendingForSession(sessions[0],health):null;
    if(pending)this.loadApprovalQR(pending.budget_id,signature);
    const match=this.shadowRoot.getElementById("match-session");
    if(match)match.onclick=async()=>{
      if(this.matchBusy)return;
      const selected=sessions[0],choice=sessionStatus(selected,health);
      if(!choice.matchBudgetId)return;
      this.matchBusy=true;match.disabled=true;
      try{
        const result=await confirmSessionMatch(()=>this._hass,c,choice.matchBudgetId,selected.session_id,
          text=>confirm(text));
        this.matchMessage=result.cancelled?"Session matching cancelled. No change made.":
          "Driver confirmed and session matched. Keep the driver page and wallet available for settlement.";
      }catch(e){this.matchMessage=e.message||"Matching failed. Refresh driver setup; do not create another approval.";}
      finally{this.matchBusy=false;this.signature=null;this.render();}
    };
    for(const d of this.shadowRoot.querySelectorAll("details"))if(opened.includes(d.querySelector("summary")?.textContent))d.open=true;
    const button=this.shadowRoot.getElementById("refresh");
    if(button)button.onclick=async()=>{
      button.disabled=true;const f=this.shadowRoot.getElementById("feedback");f.textContent="Checking the provider…";
      try{await h.callWS({type:"call_service",domain:"bsv_settlement",service:"wallet_refresh_chain",service_data:{config_entry_id:c.config_entry_id},return_response:true});f.textContent="Balance and payment checked. No payment was sent.";}
      catch(e){f.textContent=e.message||"Provider check failed. Try again later.";}
      finally{button.disabled=false;}
    };
  }
  async loadApprovalQR(budgetId,signature){
    const holder=this.shadowRoot.getElementById("approval-qr");if(!holder)return;
    try{
      const result=await this._hass.callWS({type:"call_service",domain:"bsv_settlement",service:"session_budget_status",
        service_data:{config_entry_id:this.config.config_entry_id,budget_id:budgetId},return_response:true});
      if(signature!==this.signature||!this._hass.user?.is_admin)return;
      const b=result.response,url=approvalUrl(b?.driver_link_fragment,budgetId,location.origin);
      if(!awaitingApproval(b)||!url)return;
      holder.innerHTML=`<h3>Waiting for driver approval</h3><p class="note">Scan in BSV Browser, review the terms and approve. Scanning alone does not authorise payment.</p><div class="qr"></div><div class="actions"><button class="primary" id="copy-approval">Copy approval link</button></div><p class="note" role="status" id="qr-feedback">Private link for this session only.</p>`;
      drawApprovalQR(holder.querySelector(".qr"),url);holder.hidden=false;
      this.approvalExpiry=setTimeout(()=>{holder.hidden=true;holder.replaceChildren();},
        Math.max(0,Date.parse(b.terms.expires_at)-Date.now()));
      holder.querySelector("#copy-approval").onclick=async()=>{
        try{await navigator.clipboard.writeText(url);holder.querySelector("#qr-feedback").textContent="Private approval link copied.";}
        catch{holder.querySelector("#qr-feedback").textContent="Copy unavailable. Open Drivers to select the link.";}
      };
    }catch{
      if(signature===this.signature){holder.hidden=false;holder.textContent="Approval QR unavailable. Open Drivers and check the current invitation.";}
    }
  }
}
if(!customElements.get("bsv-operator-card"))customElements.define("bsv-operator-card",BSVOperatorCard);

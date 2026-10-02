import {esc,num,stamp,short,styles,sessionStatus} from "./ui.js";
import {awaitingApproval,approvalUrl,drawApprovalQR,pendingForSession} from "./approval-qr.js";
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
    const signature=JSON.stringify([mode,proxy?.state,proxy?.attributes,ws?.state,health,balance?.state,h.user?.is_admin]);
    if(signature===this.signature)return;this.signature=signature;clearTimeout(this.approvalExpiry);
    const link=(path,text,primary=false)=>`<a class="button ${primary?"primary":""}" href="/bsv-settlement/${path}">${esc(text)}</a>`;
    const sessions=[proxy?.attributes.latest_session,proxy?.attributes.previous_session].filter(Boolean);
    let body;
    if(mode==="wallet"){
      body=`<div class="head"><div><p class="eyebrow">Operator funds</p><h2>Your mainnet wallet</h2></div><ha-icon icon="mdi:wallet-outline"></ha-icon></div>
      <div class="metrics"><div class="metric"><span>Confirmed spendable</span><strong>${stale?"Unavailable":num(balance?.state)} <small>sat</small></strong></div><div class="metric"><span>Pending change</span><strong>${stale?"Unavailable":num(health.pending_change_sats)} <small>sat</small></strong></div></div>
      <p class="note" style="margin-top:12px">Pending change is money returning to you from a submitted payment. It is not yet confirmed or available to spend.</p>
      <div class="section"><div class="row"><span class="badge ${health.chain_error||stale||!health.chain_checked_at?"warn":"good"}">${stale?"Wallet unavailable":health.chain_error?"Balance check failed":health.chain_checked_at?"Provider check complete":"Balance not checked"}</span><span class="note">Checked ${esc(stamp(health.chain_checked_at))}</span></div>
      ${health.chain_error?`<p class="notice">${esc(health.chain_error)}. Try a fresh provider check. Do not resend a payment.</p>`:""}
      <button id="refresh" style="margin-top:16px" ${stale||!h.user?.is_admin?"disabled":""}><ha-icon icon="mdi:refresh"></ha-icon>Check balance and payment</button><p id="feedback" role="status" class="note"></p></div>
      <details><summary>How the balance works</summary><p class="note">Checked every five minutes, every minute while an operator payment is unresolved, and after settlement events. Provider-reported, not independently verified. A payment can consume a large funding output and return the remainder as change.</p></details>`;
    }else{
      const s=sessions[0], summary=stale?{label:"Wallet unavailable",tone:"warn",detail:"Reconnect the wallet before assessing payment readiness.",target:"wallet"}:sessionStatus(s,health);
      const unavailable=!proxy||["unknown","unavailable"].includes(proxy.state);
      body=`<div class="head"><div><p class="eyebrow">Charging & settlement</p><h2>${unavailable?"Recorder unavailable":!s?"Ready for the next driver":s.ended_at?"Latest session complete":"Session in progress"}</h2></div><ha-icon icon="mdi:ev-station"></ha-icon></div>
      ${unavailable?`<p class="notice">Session data is unavailable. Do not infer a completed payment from an old reading.</p>`:`
      <div class="notice"><strong>${esc(summary.label)}</strong>${esc(summary.detail)}</div>
      <div class="actions">${link(summary.target,summary.target==="payments"?"View settlement":summary.target==="wallet"?"Check wallet":"Set up driver",true)}${link("wallet","View funds")}</div>
      <section id="approval-qr" class="section" hidden aria-label="Driver approval"></section>
      ${s?`<div class="section"><div class="row"><div><p class="note">${Number(s.net_cost_aud)<0?"Provisional credit to driver":"Provisional driver charge"}</p><p class="amount">AUD ${num(s.net_cost_aud===null?null:Math.abs(Number(s.net_cost_aud)),2)}</p></div><span class="badge ${summary.tone}">${esc(summary.label)}</span></div>
      <dl><dt>Charged to EV</dt><dd>${num(s.import_kwh,2)} kWh</dd><dt>Exported from EV</dt><dd>${num(s.export_kwh,2)} kWh</dd><dt>${s.ended_at?"Ended":"Started"}</dt><dd>${esc(stamp(s.ended_at||s.energy_started_at||s.opened_at))}</dd><dt>Session reference</dt><dd>${esc(short(s.ocpp_transaction_id))}</dd></dl>
      <details><summary>Full session reference and meter notes</summary><code>${esc(s.ocpp_transaction_id)}</code><p class="note">Sensor-derived proxy ID, not a charger-issued OCPP ID. Interval costs are provisional, not a certified bill.</p></details></div>`:""}`}
      ${(proxy?.attributes.issues||[]).length?`<p class="notice">Recorder needs attention: ${esc(proxy.attributes.issues.join(", "))}</p>`:""}
      ${sessions[1]?`<div class="section"><div class="row"><h3>Previous session</h3><span class="badge">${esc(sessionStatus(sessions[1],health).label)}</span></div><p class="note">${esc(short(sessions[1].ocpp_transaction_id))} · ${esc(stamp(sessions[1].ended_at))} · AUD ${num(sessions[1].net_cost_aud,2)}</p></div>`:""}
      <p class="note section">Session data updated ${esc(stamp(proxy?.attributes.updated_at))}. Charging control is separate from wallet settlement.</p>`;
    }
    const opened=[...this.shadowRoot.querySelectorAll("details[open]")].map(d=>d.querySelector("summary")?.textContent);
    this.shadowRoot.innerHTML=`<style>${styles}</style><ha-card>${body}</ha-card>`;
    const pending=mode==="overview"&&!stale&&h.user?.is_admin&&
      proxy&&!["unknown","unavailable"].includes(proxy.state)&&!(proxy.attributes?.issues||[]).length?
      pendingForSession(sessions[0],health):null;
    if(pending)this.loadApprovalQR(pending.budget_id,signature);
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

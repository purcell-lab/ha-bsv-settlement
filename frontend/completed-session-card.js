import {styles,esc,stamp,short} from "./ui.js";
import {approvalUrl,drawApprovalQR} from "./approval-qr.js";
import {validateInvitationLimits} from "./invitation-form.js";
const warningText=f=>({
  "import:energy_without_matching_state":"Charging energy was recorded while the charger state did not indicate charging.",
  "export:energy_without_matching_state":"Export energy was recorded while the charger state did not indicate discharging."
})[f]||f;

class CompletedSessionCard extends HTMLElement{
  constructor(){super();this.attachShadow({mode:"open"});this.plan=null;this.busy=false;}
  $(id){return this.shadowRoot.getElementById(id);}
  setConfig(c){
    this.config=c;if(this.ready)return;this.ready=true;
    this.shadowRoot.innerHTML=`<style>${styles}</style><section class="section">
      <h3>Resolve a completed session</h3>
      <p class="note">Request fresh driver consent, waive your charge, or close a zero account. Existing signed or submitted attempts must use reconciliation instead.</p>
      <label>Recent completed session<select id="recent"></select></label>
      <label>Session ID<input id="session-id" maxlength="200" placeholder="Select above or paste a retained session ID"></label>
      <button id="review" class="primary">Review closure options</button>
      <p id="message" role="status" aria-live="polite" class="note"></p>
      <div id="account" hidden></div>
      <div id="actions" hidden>
        <label>Reason shown to the driver or recorded with the waiver<textarea id="reason" maxlength="300" rows="3" placeholder="Explain the account review or reason for waiver"></textarea></label>
        <label><input id="account-reviewed" type="checkbox">I reviewed this completed energy account.</label>
        <label id="quality-label" hidden><input id="quality-reviewed" type="checkbox">I accept the listed provisional metering warning, with the reason above. It remains recorded and will be disclosed to the driver.</label>
        <details open><summary>Driver payment limits</summary><div class="form-grid">
          <label>Total debit limit including fee (sat)<input id="total" type="number" min="1" max="100000" value="1000"></label>
          <label>Maximum fee within total (sat)<input id="fee" type="number" min="0" max="1000" value="1000"></label>
          <label>Approval duration (minutes)<input id="minutes" type="number" min="1" max="1440" value="720"></label>
          <label>Operator name<input id="name" maxlength="100"></label>
          <label class="full">Operator contact<input id="contact" maxlength="200"></label>
        </div><p class="note">The actual fee is additional to the energy charge but must fit within the total limit. Creating a link sends no payment. The driver's later approval can collect immediately.</p></details>
        <label id="replace-label" hidden><input id="replace" type="checkbox">Revoke the existing unapproved invitation and replace it with these completed-account terms.</label>
        <div class="actions"><button id="consent" class="primary">Request driver consent</button><button id="waive" class="danger">Waive driver payment</button></div>
        <p class="note">Waiving your charge closes the account without marking it paid. This cannot waive a credit owed to the driver. A waiver cannot be undone from this interface.</p>
      </div>
      <div id="link-panel" hidden class="notice">
        <h3>Awaiting driver consent</h3><p>Share this private link only with the intended driver. The driver reviews the completed account before approving payment.</p>
        <div id="qr" class="qr"></div>
        <label>Private completed-session consent link<input id="link" readonly></label>
        <button id="copy">Copy private link</button>
      </div></section>`;
    this.$("name").value=c.operator_name||"Charging operator";
    this.$("contact").value=c.operator_contact||"";
    this.$("recent").onchange=()=>{this.$("session-id").value=this.$("recent").value;this.reset();};
    this.$("session-id").oninput=()=>this.reset();
    this.$("review").onclick=()=>this.act("prepare_session_closure");
    this.$("consent").onclick=()=>this.confirm("request_closed_session_consent");
    this.$("waive").onclick=()=>this.confirm("waive_session_charge");
    this.$("copy").onclick=async()=>{try{await navigator.clipboard.writeText(this.$("link").value);this.$("message").textContent="Private link copied. Share only with this driver.";}
      catch{this.$("link").focus();this.$("link").select();this.$("message").textContent="Link selected. Use your device's Copy command.";}};
  }
  reset(){this.plan=null;this.$("account").hidden=true;this.$("actions").hidden=true;this.$("message").textContent="Review this account before choosing an action.";this.clearLink();}
  clearLink(){this.invitation=null;this.$("link-panel").hidden=true;this.$("link").value="";this.$("qr").replaceChildren();}
  set hass(h){
    this.h=h;if(!this.ready)return;
    const p=h.states[this.config.proxy_entity]?.attributes;
    const sessions=[p?.latest_session,p?.previous_session].filter(s=>s?.ended_at);
    const signature=JSON.stringify(sessions.map(s=>[s.session_id,s.ended_at]));
    if(signature!==this.signature){
      const selected=this.$("session-id").value;this.signature=signature;
      this.$("recent").innerHTML=sessions.map(s=>`<option value="${esc(s.session_id)}">${esc(stamp(s.ended_at))} · ${esc(short(s.ocpp_transaction_id))}</option>`).join("")||'<option value="">No recent completed session</option>';
      if(!selected)this.$("session-id").value=sessions[0]?.session_id||"";
      else if(sessions.some(s=>s.session_id===selected))this.$("recent").value=selected;
    }
    if(this.invitation){
      const approval=h.states[this.config.wallet_entity]?.attributes.driver_approvals?.find(a=>a.budget_id===this.invitation.terms.budget_id);
      if(approval && approval.state!=="awaiting_driver_consent"){
        this.clearLink();this.$("message").textContent=approval.approved?"Driver consent saved. Track this session's existing collection below.":"The invitation is no longer pending. Review its saved status.";
      }else if(Date.parse(this.invitation.terms.expires_at)<=Date.now()){
        this.clearLink();this.$("message").textContent="Consent link expired. Review the account before another request.";
      }
    }
    this.controls();
  }
  controls(){
    const unavailable=[this.config.wallet_entity,this.config.proxy_entity].some(e=>!this.h?.states[e]||["unknown","unavailable"].includes(this.h.states[e].state));
    const disabled=this.busy||!this.h?.user?.is_admin||unavailable;
    for(const id of ["review","consent","waive"])this.$(id).disabled=disabled||(id==="consent"&&this.plan?.amount_sats<=0);
  }
  async confirm(service){
    if(!this.plan||this.busy)return;
    const reason=this.$("reason").value.trim(), p=this.plan;
    if(reason.length<8||!this.$("account-reviewed").checked||p.accepted_flags.length&&!this.$("quality-reviewed").checked){
      this.$("message").textContent="Enter a reason and confirm the account and any metering warning.";return;
    }
    const data={expected_review_hash:p.review_hash,reason,confirm_account_review:true,
      confirm_provisional_metering:this.$("quality-reviewed").checked};
    if(service==="request_closed_session_consent"){
      const error=validateInvitationLimits(Object.fromEntries(["total","fee","minutes"].map(k=>[k,this.$(k).value])));
      if(error){this.$("message").textContent=error;return;}
      if(p.pending_budget_id&&!this.$("replace").checked){this.$("message").textContent="Confirm replacement of the existing unapproved invitation.";return;}
      Object.assign(data,{max_total_sats:Number(this.$("total").value),max_fee_sats:Number(this.$("fee").value),
        valid_minutes:Number(this.$("minutes").value),operator_name:this.$("name").value,
        operator_contact:this.$("contact").value,confirm_replace_pending:this.$("replace").checked});
      if(!window.confirm(`Issue fresh driver consent for ${p.amount_sats} sat for completed session ${p.account.ocpp_transaction_id}, up to ${data.max_total_sats} sat total including fee (fee ceiling ${data.max_fee_sats} sat), valid ${data.valid_minutes} minutes? ${p.pending_budget_id?"The existing unsigned invitation will be revoked. ":""}Reason: ${reason}. No payment is sent until the driver approves.`))return;
    }else{
      data.confirm_no_payment=true;
      if(!window.confirm(`${p.amount_sats>0?"Waive":"Close"} completed session ${p.account.ocpp_transaction_id}, AUD ${p.account.net_amount_aud}, without payment? Reason: ${reason}. Its unsigned invitation will be revoked. This does not mark the account paid and cannot be undone here.`))return;
    }
    await this.act(service,data);
  }
  async act(service,extra={}){
    if(this.busy||!this.h?.user?.is_admin)return;
    this.busy=true;this.controls();this.$("message").textContent="Checking the saved account…";
    try{
      const result=await this.h.callWS({type:"call_service",domain:"bsv_settlement",service,return_response:true,
        service_data:{config_entry_id:this.config.config_entry_id,proxy_config_entry_id:this.config.proxy_config_entry_id,
          conversion_rate_entity:this.config.rate_entity,session_id:this.$("session-id").value,...extra}});
      const r=result.response;
      if(service==="prepare_session_closure"){
        this.plan=r;this.clearLink();this.$("account").hidden=false;
        this.$("account").innerHTML=`<div class="notice"><strong>${r.closed?esc(r.state==="waived"?"Waived":"Closed: no payment due"):"Completed energy account"}</strong>
          <p>${esc(r.account.import_kwh)} kWh charged · ${esc(r.account.export_kwh)} kWh exported · AUD ${esc(r.account.net_amount_aud)}</p>
          <code>${esc(r.account.ocpp_transaction_id)}</code>
          <p>${r.closed?esc(r.reason):`${esc(r.amount_sats)} sat energy charge at ${esc(r.satoshis_per_aud)} sat/AUD. Network fee excluded.`}</p>
          ${!r.closed&&r.accepted_flags.length?`<p>Data review required: ${esc(r.accepted_flags.map(warningText).join(" "))}</p>`:""}</div>`;
        this.$("actions").hidden=r.closed;this.$("quality-label").hidden=!r.accepted_flags?.length;
        this.$("replace-label").hidden=!r.pending_budget_id;
        for(const id of ["account-reviewed","quality-reviewed","replace"])this.$(id).checked=false;
        this.$("waive").textContent=r.amount_sats===0?"Close zero balance":"Waive driver payment";
        this.$("message").textContent=r.closed?"Account closed without payment.":"Review the account, enter a reason and choose a resolution.";
      }else if(service==="request_closed_session_consent"){
        const url=approvalUrl(r.driver_link_fragment,r.terms.budget_id,location.origin);
        if(!url)throw Error("Private link unavailable. Check the saved pending invitation; do not issue another.");
        this.invitation=r;this.$("link").value=url;drawApprovalQR(this.$("qr"),url);
        this.$("link-panel").hidden=false;this.$("actions").hidden=true;this.plan=null;
        this.$("message").textContent="Awaiting fresh driver consent. No payment was sent.";
      }else{
        this.plan=null;this.clearLink();this.$("actions").hidden=true;
        this.$("message").textContent=r.state==="waived"?"Waived. Account closed without payment; audit history retained.":"Closed: no payment due.";
        this.$("account").hidden=true;
      }
    }catch(e){this.plan=null;this.$("actions").hidden=true;this.$("message").textContent=e.message||"Review failed. No successful resolution is confirmed. Refresh the account before trying again.";}
    finally{this.busy=false;this.controls();}
  }
}
if(!customElements.get("bsv-completed-session-card"))customElements.define("bsv-completed-session-card",CompletedSessionCard);

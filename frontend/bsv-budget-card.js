import {awaitingApproval,approvalUrl,drawApprovalQR} from "./approval-qr.js";
import {styles,esc,stamp,short,stateLabel} from "./ui.js";
class BSVBudgetCard extends HTMLElement {
  setConfig(config) {
    for(const key of ["config_entry_id","proxy_config_entry_id","proxy_entity","rate_entity"])if(!config[key])throw Error(`${key} is required`);
    this.config=config;this.budget=null;this.initialRead=false;this.busy=false;this.linkBudget=null;
    if(!this.shadowRoot)this.attachShadow({mode:"open"});
    this.shadowRoot.innerHTML=`<style>${styles}</style><ha-card>
      <div class="head"><div><p class="eyebrow">Driver setup</p><h2>One driver. One session.</h2></div><ha-icon icon="mdi:account-check-outline"></ha-icon></div>
      <p class="note">Invite the driver, receive approval, then confirm the matching charging session.</p>
      <p id="ongoing-notice" class="notice" hidden></p>
      <ol class="steps"><li id="step1">1 · Invite</li><li id="step2">2 · Approve</li><li id="step3">3 · Match session</li></ol>
      <div id="current" class="notice" hidden><strong id="state-title"></strong><span id="next-step"></span></div>
      <p id="status" class="note" role="status" aria-live="polite"></p>
      <div id="link-panel" hidden class="section">
        <h3>Scan to approve in BSV Browser</h3>
        <p class="note">Share with this driver only. Scanning opens the terms; it does not approve payment or start charging.</p>
        <div id="qr" class="qr"></div>
        <label>Private approval link<input id="driver-link" readonly></label>
        <div class="actions"><button id="copy" class="primary">Copy driver link</button><a id="open-link" class="button" target="_blank" rel="noopener noreferrer">Open driver page</a></div>
        <p class="note">The QR closes when approval is received, expires or is revoked. Save your driver link for session updates and receipts.</p>
      </div>
      <div class="actions"><button id="bind" class="primary" hidden>Confirm driver and match session</button><button id="refresh" hidden>Check approval</button></div>
      <p id="session" class="note" style="margin-top:12px"></p>
      <details id="create-panel" open><summary>Create a driver invitation</summary>
        <label>Use this approval for<select id="scope"><option value="next">The next session</option><option id="current-option" value="current" disabled>The current open session</option></select></label>
        <div class="notice"><strong id="defaults">1,000 sat total limit · 10 sat maximum fee · 12 hours</strong><span id="rate"></span></div>
        <details><summary>Change limits or operator contact</summary><div class="form-grid">
          <label>Total spending limit (sat)<input id="total" type="number" min="1" max="100000" value="1000"></label>
          <label>Maximum fee (sat)<input id="fee" type="number" min="0" max="1000" value="10"></label>
          <label>Approval duration (minutes)<input id="minutes" type="number" min="1" max="1440" value="720"></label>
          <label>Operator name<input id="name" maxlength="100"></label>
          <label class="full">Operator contact<input id="contact" maxlength="200"></label>
        </div></details>
        <button id="create" class="primary" style="margin-top:16px">Create private driver link</button>
        <p class="note" style="margin-top:12px">Creating a link does not authorise payment or start charging. The driver must approve in their wallet.</p>
      </details>
      <details><summary>Approval details and recovery</summary>
        <p id="collection" class="note"></p>
        <p class="note">Driver charges need the open BSV Browser page. Registered operator credits run on the server; the driver returns to import the receipt. Matching a session is never automatic.</p>
        <p id="lost-link" class="note"></p>
        <button id="revoke" class="danger" disabled>Revoke this approval</button>
        <details><summary>Advanced: transfer JSON manually</summary>
          <label>Signed invitation<textarea id="invitation" rows="4" readonly></textarea></label>
          <label>Driver's signed approval<textarea id="receipt" rows="4"></textarea></label>
          <button id="accept" disabled>Verify and save approval</button>
        </details>
      </details>
    </ha-card>`;
    this.$("name").value=config.operator_name||"Charging operator";this.$("contact").value=config.operator_contact||"";
    for(const id of ["total","fee","minutes"])this.$(id).oninput=()=>{
      this.$("defaults").textContent=`${this.$("total").value || "?"} sat total limit · ${this.$("fee").value || "?"} sat maximum fee · ${this.$("minutes").value || "?"} minutes`;
    };
    this.$("create").onclick=()=>{
      const n=id=>Number(this.$(id).value);
      if(![["total",1,100000],["fee",0,1000],["minutes",1,1440]].every(([id,min,max])=>this.$(id).value!==""&&Number.isInteger(n(id))&&n(id)>=min&&n(id)<=max)){
        this.$("status").textContent="Enter whole-number limits within the displayed ranges.";return;
      }
      const data={proxy_config_entry_id:config.proxy_config_entry_id,conversion_rate_entity:config.rate_entity,
        max_total_sats:n("total"),max_fee_sats:n("fee"),valid_minutes:n("minutes"),
        operator_name:this.$("name").value,operator_contact:this.$("contact").value};
      if(this.$("scope").value==="current"){
        if(!this.session||this.session.ended_at){this.$("status").textContent="That session has ended. Create a next-session invitation instead.";return;}
        data.session_id=this.session.session_id;
      }
      this.perform("create_session_budget",data);
    };
    this.$("refresh").onclick=()=>this.perform("session_budget_status",this.budget?{budget_id:this.budget.terms.budget_id}:{});
    this.$("bind").onclick=()=>{
      if(confirm(`Confirm this driver is using session ${this.session.ocpp_transaction_id}. This enables settlement checks within the signed limits, not charger control.`))
        this.perform("bind_session_budget",{budget_id:this.budget.terms.budget_id,session_id:this.session.session_id,confirm_driver_present:true});
    };
    this.$("revoke").onclick=()=>{
      if(confirm("Revoke this approval? This prevents a new payment, but cannot reverse a signed or submitted transaction."))
        this.perform("revoke_session_budget",{budget_id:this.budget.terms.budget_id});
    };
    this.$("accept").onclick=()=>{
      try{const receipt=JSON.parse(this.$("receipt").value);this.perform("accept_session_budget",{budget_id:receipt.budget_id,receipt});}
      catch(e){this.$("status").textContent="Paste a valid signed approval JSON record.";}
    };
    this.$("copy").onclick=async()=>{
      try{await navigator.clipboard.writeText(this.$("driver-link").value);this.$("status").textContent="Private link copied. Share only with this driver.";}
      catch(e){this.$("driver-link").focus();this.$("driver-link").select();this.$("status").textContent="Link selected. Use your device's Copy command.";}
    };
  }
  $(id){return this.shadowRoot.getElementById(id);}
  connectedCallback(){if(!this.timer)this.timer=setInterval(()=>{this.paint();if(this.budget&&!this.busy&&this._hass?.user?.is_admin)this.perform("session_budget_status",{budget_id:this.budget.terms.budget_id},true);},15000);}
  disconnectedCallback(){clearInterval(this.timer);this.timer=null;}
  getCardSize(){return 7;}
  set hass(h){this._hass=h;if(!this.config)return;this.session=h.states[this.config.proxy_entity]?.attributes.latest_session;this.paint();
    if(h.user?.is_admin&&!this.initialRead){this.initialRead=true;this.perform("session_budget_status",{},true);}
  }
  paint(){
    const h=this._hass;if(!h)return;const b=this.budget,admin=!!h.user?.is_admin;
    const ongoing=h.states[this.config.wallet_entity]?.attributes?.ongoing_credit;
    this.$("ongoing-notice").hidden=!ongoing?.enabled;
    this.$("ongoing-notice").textContent=ongoing?.effective?
      "Ongoing operator credits are enabled. The last verified receiving registration supplies the recipient for each new session. Session matching below is for driver spending approval, not ongoing operator credits.":
      "The ongoing credit policy is paused by the master automatic-credit policy. Check Payments.";
    const usable=h.states[this.config.proxy_entity]&&!["unknown","unavailable"].includes(h.states[this.config.proxy_entity].state);
    const accepted=b?.state==="spending_authorised_wallet_permission_required";
    const matching=b?.terms.session_mode!=="existing_session"||
      (b?.terms.session_id===this.session?.session_id&&!this.session?.ended_at);
    this.$("link-panel").hidden=!(admin&&usable&&matching&&awaitingApproval(b)&&this.linkBudget===b.terms.budget_id&&this.linkFresh!==false);
    if(!admin||b&&!awaitingApproval(b)){
      this.linkBudget=null;this.$("driver-link").value="";this.$("open-link").removeAttribute("href");this.$("qr").replaceChildren();
    }
    const bound=!!b?.binding||b?.terms.session_mode==="existing_session";
    this.$("current").hidden=!b;
    this.$("step1").className=b?"done":"";this.$("step2").className=accepted?"done":"";this.$("step3").className=bound?"done":"";
    this.$("current-option").disabled=!this.session||!!this.session.ended_at;
    this.$("session").textContent=this.session?`Recorder: ${this.session.ended_at?"closed":"open"} session ${short(this.session.ocpp_transaction_id)} · ${stamp(this.session.ended_at||this.session.opened_at)}`:"No session recorded yet.";
    this.$("rate").textContent=`${h.states[this.config.rate_entity]?.state||"Unavailable"} sat per AUD. This is the demonstration conversion rate.`;
    if(b){
      this.$("state-title").textContent=stateLabel(b.state);
      this.$("next-step").textContent=!accepted?(b.state==="awaiting_driver_consent"?"Ask the driver to open their private link in BSV Browser and approve.":"This approval is not active. Create a fresh invitation for a future session."):!bound?"Driver approved. Confirm the correct open session below.":b.credit_destination?"Session matched and receiving wallet registered. Final account and payment checks still apply.":"Session matched. Ask the driver to reconnect before session end to register their receiving wallet.";
      this.$("lost-link").textContent=this.linkBudget===b.terms.budget_id?"Your pending private link is shown above.":awaitingApproval(b)?"This older invitation's link cannot be recovered automatically. Use the original saved link, or explicitly revoke it before creating a replacement.":"No approval QR is needed. Do not pay or reapprove an already-settled session.";
    }
    this.$("create").disabled=this.busy||!admin||!usable;
    this.$("accept").disabled=this.busy||!admin;
    this.$("refresh").hidden=false;this.$("refresh").disabled=this.busy||!admin;
    this.$("revoke").disabled=this.busy||!admin||!b||["revoked","expired"].includes(b.state)||!!b.automatic_credit?.txid||!!b.collection?.txid;
    this.$("bind").hidden=!b||!accepted||bound;
    const afterApproval=!!b&&Date.parse(this.session?.opened_at)>=Date.parse(b.accepted_at);
    this.$("bind").disabled=this.busy||!admin||!usable||!this.session||!!this.session.ended_at||!afterApproval;
    if(b&&accepted&&!bound&&!afterApproval)this.$("next-step").textContent="Spending approval saved for a future session. The current session began before this approval and cannot be matched to it. Ongoing credits, if enabled, are handled separately.";
  }
  async perform(service,data,silent=false){
    if(this.busy||!this._hass?.user?.is_admin)return;this.busy=true;this.paint();
    if(!silent)this.$("status").textContent="Working…";
    try{
      const result=await this._hass.callWS({type:"call_service",domain:"bsv_settlement",service,
        service_data:{config_entry_id:this.config.config_entry_id,...data},return_response:true});
      this.budget=result.response;this.linkFresh=true;const b=this.budget;
      const url=approvalUrl(b.driver_link_fragment,b.terms.budget_id,location.origin);
      if(url&&awaitingApproval(b)){
        this.linkBudget=b.terms.budget_id;this.$("driver-link").value=url;this.$("open-link").href=url;
        drawApprovalQR(this.$("qr"),url);
        this.$("create-panel").open=false;
      }
      this.$("invitation").value=JSON.stringify(b.invitation,null,2);
      const p=b.automatic_credit?.txid?b.automatic_credit:b.collection;
      this.$("collection").textContent=p?`${stateLabel(p.state)}${p.txid?": "+p.txid:""}`:"No payment submitted under this approval.";
      if(!silent)this.$("status").textContent=service==="create_session_budget"?"Invitation ready. Ask the driver to scan and review the terms.":"Approval record updated.";
    }catch(e){
      this.linkFresh=false;
      this.$("status").textContent=silent&&!this.budget?"No approval loaded. Create an invitation, or use Check approval to retry.":e.message||"Could not update the approval. Try again.";
    }finally{this.busy=false;this.paint();}
  }
}
if(!customElements.get("bsv-budget-card"))customElements.define("bsv-budget-card",BSVBudgetCard);

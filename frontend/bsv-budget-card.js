import {awaitingApproval,approvalUrl,drawApprovalQR} from "./approval-qr.js";
import {styles,esc,stamp,short,stateLabel} from "./ui.js";
import {invitationDefaults,futureInvitationDefaults,validateInvitationLimits,sameInvitationScope,pendingReplacement} from "./invitation-form.js";
import {registrationOpenRequest} from "./public-registration.js";
class BSVBudgetCard extends HTMLElement {
  setConfig(config) {
    for(const key of ["config_entry_id","proxy_config_entry_id","proxy_entity","rate_entity"])if(!config[key])throw Error(`${key} is required`);
    this.config=config;this.budget=null;this.initialRead=false;this.busy=false;this.linkBudget=null;
    if(!this.shadowRoot)this.attachShadow({mode:"open"});
    this.shadowRoot.innerHTML=`<style>${styles}</style><ha-card>
      <div class="head"><div><p class="eyebrow">Driver setup</p><h2>Driver approval and limits</h2></div><ha-icon icon="mdi:account-check-outline"></ha-icon></div>
      <p class="note">Approve a shared allowance once for future sessions. Eligible sessions then settle without per-session operator matching while the driver wallet is available. Single-session approval is optional.</p>
      <p id="ongoing-notice" class="notice" hidden></p>
      <ol class="steps"><li id="step1">1 · Invite</li><li id="step2">2 · Approve</li><li id="step3">3 · Match session</li></ol>
      <div id="current" class="notice" hidden><strong id="state-title"></strong><span id="next-step"></span></div>
      <div class="actions"><button id="bind" class="primary" hidden>Confirm this session</button><button id="refresh" hidden>Check approval</button></div>
      <p id="session" class="note" style="margin-top:12px"></p>
      <p id="status" class="note" role="status" aria-live="polite"></p>
      <div class="section">
        <h3>Public registration</h3>
        <p class="note">Open a 15-minute QR window for the next driver. The invitation covers seven days and 1,000 sat total, including fees. Historical approvals and payments stay intact; payment policies do not change.</p>
        <p class="note">Anyone with the public page can claim the invitation with their wallet. Open it only when the intended driver is ready.</p>
        <div class="actions"><button id="open-registration">Open registration for next driver</button><button id="close-registration" hidden>Close public registration</button></div>
        <p id="public-status" class="note" role="status" aria-live="polite"></p>
        <a id="public-page" class="button" href="/bsv_settlement/driver/index.html" target="_blank" rel="noopener noreferrer" hidden>Open public QR page</a>
      </div>
      <div id="link-panel" hidden class="section">
        <h3>Scan to approve in BSV Browser</h3>
        <p class="note">Share with this driver only. Scanning opens the terms; it does not approve payment or start charging.</p>
        <div id="qr" class="qr"></div>
        <label>Private approval link<input id="driver-link" readonly></label>
        <div class="actions"><button id="copy" class="primary">Copy driver link</button><a id="open-link" class="button" target="_blank" rel="noopener noreferrer">Open driver page</a></div>
        <p class="note">The QR closes when approval is received, expires or is revoked. Save your driver link for session updates and receipts.</p>
      </div>
      <details id="create-panel" open><summary>Create a driver invitation</summary>
        <form id="create-form">
        <label>Use this approval for<select id="scope"><option value="multi">Multiple future sessions, up to seven days (recommended)</option><option value="next">The next session only (operator matching required)</option><option id="current-option" value="current" disabled>The current open session only</option></select></label>
        <p class="notice">Multi-session approval ends at expiry or a newer driver registration. Its total includes all charging payments and fees, with no per-session reset. Credits do not refill it. Keep the driver wallet available for collection; this is not an offline payment guarantee.</p>
        <label><input id="include-current" type="checkbox"> Include the current recorder session, even if it has just ended, in this fresh multi-session approval</label>
        <div class="notice"><strong id="defaults">1,000 sat shared total including all fees · seven days</strong><span id="rate"></span></div>
        <p class="note">The fee is a ceiling, not a fixed charge. Payment plus actual fee must fit within the total limit. New invitations only: existing signed approvals do not change.</p>
        <details id="limits-panel" open><summary>Change limits or operator contact</summary><div class="form-grid">
          <label>Total spending limit including fee (sat)<input id="total" type="number" required min="1" max="100000" value="${invitationDefaults.total}"></label>
          <label>Maximum fee within total (sat)<input id="fee" type="number" required min="0" max="1000" value="${invitationDefaults.fee}"></label>
          <label>Approval duration (minutes)<input id="minutes" type="number" required min="1" max="10080" value="${futureInvitationDefaults.minutes}"></label>
          <label>Operator name<input id="name" maxlength="100"></label>
          <label class="full">Operator contact<input id="contact" maxlength="200"></label>
        </div>
        <p id="existing-warning" class="notice" hidden></p>
        <label id="replace-choice" hidden><input id="replace-pending" type="checkbox"> Replace the displayed unapproved invitation with these settings</label>
        <p id="form-error" class="note" role="status" aria-live="polite"></p>
        <button id="create" type="submit" class="primary" style="margin-top:16px">Create private driver link</button>
        </details>
        <p class="note" style="margin-top:12px">Creating a link does not authorise payment or start charging. The driver must approve in their wallet.</p>
        </form>
      </details>
      <details><summary>Approval details and recovery</summary>
        <p id="collection" class="note"></p>
        <p class="note">Driver charges need the open BSV Browser page. Registered operator credits run on the server; the driver returns to import receipts. A multi-session approval covers only sessions started after receiving-wallet registration. Single next-session approvals still require operator matching.</p>
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
      this.$("defaults").textContent=`${this.$("total").value || "?"} sat total limit · up to ${this.$("fee").value || "?"} sat fee within total · ${this.$("minutes").value || "?"} minutes`;
      this.$("form-error").textContent=validateInvitationLimits({...Object.fromEntries(["total","fee","minutes"].map(id=>[id,this.$(id).value])),multi:this.$("scope").value==="multi"});
    };
    this.$("scope").onchange=()=>{
      const multi=this.$("scope").value==="multi";
      this.$("minutes").max=multi?"10080":"1440";
      this.$("minutes").value=multi?"10080":String(invitationDefaults.minutes);
      this.$("include-current").disabled=!multi;
      if(!multi)this.$("include-current").checked=false;
      this.$("minutes").oninput();this.$("replace-pending").checked=false;this.paint();
    };
    this.$("create-form").onsubmit=async event=>{
      event.preventDefault();
      if(this.busy||!this._hass?.user?.is_admin)return;
      const n=id=>Number(this.$(id).value);
      const error=validateInvitationLimits({...Object.fromEntries(["total","fee","minutes"].map(id=>[id,this.$(id).value])),multi:this.$("scope").value==="multi"});
      this.$("form-error").textContent=error;if(error)return;
      const data={proxy_config_entry_id:config.proxy_config_entry_id,conversion_rate_entity:config.rate_entity,
        max_total_sats:n("total"),max_fee_sats:n("fee"),valid_minutes:n("minutes"),multi_session:this.$("scope").value==="multi",
        operator_name:this.$("name").value,operator_contact:this.$("contact").value};
      if(data.multi_session&&this.$("include-current").checked){
        if(!this.session){this.$("form-error").textContent="No current recorder session is available.";return;}
        data.initial_session_id=this.session.session_id;
      }
      if(this.$("scope").value==="current"){
        if(!this.session||this.session.ended_at){this.$("status").textContent="That session has ended. Create a next-session invitation instead.";return;}
        data.session_id=this.session.session_id;
      }
      if(sameInvitationScope(this.budget,this.$("scope").value,this.session)&&this.$("replace-pending").checked){
        if(!confirm(`Revoke the displayed unapproved invitation and replace it with ${n("total")} sat total, including up to ${n("fee")} sat fee, for ${n("minutes")} minutes? The driver must approve the new terms. No payment is sent.`))return;
        try{Object.assign(data,await pendingReplacement(this.budget));}
        catch(e){this.$("form-error").textContent=e.message;return;}
      }
      await this.perform("create_session_budget",data);
    };
    this.$("refresh").onclick=()=>this.perform("session_budget_status",this.budget?{budget_id:this.budget.terms.budget_id}:{});
    this.$("open-registration").onclick=async()=>{
      if(this.busy||!this._hass?.user?.is_admin)return;
      if(!confirm("Open public registration for 15 minutes? Anyone with the public page can approve the invitation. Terms: seven days, 1,000 sat TOTAL including all fees, with a 1,000 sat fee ceiling within that total. Historical settlements and payment policies stay unchanged. No funds move now."))return;
      const b=await this.perform("create_session_budget",{
        proxy_config_entry_id:config.proxy_config_entry_id,conversion_rate_entity:config.rate_entity,
        multi_session:true,max_total_sats:1000,max_fee_sats:1000,valid_minutes:10080,
        operator_name:this.$("name").value,operator_contact:this.$("contact").value});
      if(!b)return;
      try{
        const request=await registrationOpenRequest(b,
          data=>this.perform("session_budget_status",data,true));
        await this.perform("open_public_registration",request);
      }catch(e){this.$("status").textContent=`Public registration was not opened: ${e.message}`;}
    };
    this.$("close-registration").onclick=async()=>{
      if(this.busy||!this._hass?.user?.is_admin||!this.budget)return;
      if(!confirm("Close this public QR window? The pending private invitation and all historical approvals and payments remain unchanged."))return;
      try{
        const hash=(await pendingReplacement(this.budget)).expected_invitation_hash;
        await this.perform("close_public_registration",{budget_id:this.budget.terms.budget_id,
          expected_invitation_hash:hash,confirm_public_registration:true});
      }catch(e){this.$("status").textContent=e.message;}
    };
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
    const publicOpen=awaitingApproval(b)&&!!b?.public_registration?.available&&this.linkFresh!==false&&
      (!b.public_registration.expires_at||Date.parse(b.public_registration.expires_at)>Date.now());
    this.$("open-registration").disabled=this.busy||!admin||!usable||publicOpen;
    this.$("close-registration").hidden=!publicOpen||!admin;
    this.$("close-registration").disabled=this.busy||!usable;
    this.$("public-page").hidden=!publicOpen||!admin;
    this.$("public-status").textContent=publicOpen?
      `Public registration is open${b.public_registration.expires_at?` until ${stamp(b.public_registration.expires_at)}`:""}. It closes after approval or a registration change.`:
      "Public registration is not open for the displayed invitation. Historical private links are unchanged.";
    const accepted=b?.state==="spending_authorised_wallet_permission_required";
    const matching=b?.terms.session_mode!=="existing_session"||
      (b?.terms.session_id===this.session?.session_id&&!this.session?.ended_at);
    this.$("link-panel").hidden=!(admin&&usable&&matching&&awaitingApproval(b)&&this.linkBudget===b.terms.budget_id&&this.linkFresh!==false);
    if(!admin||b&&!awaitingApproval(b)){
      this.linkBudget=null;this.$("driver-link").value="";this.$("open-link").removeAttribute("href");this.$("qr").replaceChildren();
    }
    const multi=b?.terms.version===3;
    const bound=!!b?.binding||b?.terms.session_mode==="existing_session"||multi;
    this.$("current").hidden=!b;
    this.$("step1").className=b?"done":"";this.$("step2").className=accepted?"done":"";this.$("step3").className=bound?"done":"";
    this.$("current-option").disabled=!this.session||!!this.session.ended_at;
    this.$("session").textContent=this.session?`Recorder: ${this.session.ended_at?"closed":"open"} session ${short(this.session.ocpp_transaction_id)} · ${stamp(this.session.ended_at||this.session.opened_at)}`:"No session recorded yet.";
    this.$("rate").textContent=`${h.states[this.config.rate_entity]?.state||"Unavailable"} sat per AUD. This is the demonstration conversion rate.`;
    if(b){
      this.$("state-title").textContent=stateLabel(b.state);
      if(accepted&&!bound)this.$("state-title").textContent="Driver has approved. Confirm this session";
      this.$("next-step").textContent=!accepted?(b.state==="awaiting_driver_consent"?"Ask the driver to open their private link in BSV Browser and approve.":"This approval is not active. Create a fresh invitation for a future session."):!bound?"Driver approved. Confirm the correct open session below.":b.credit_destination?"Session matched and receiving wallet registered. Final account and payment checks still apply.":"Session matched. Ask the driver to reconnect before session end to register their receiving wallet.";
      this.$("lost-link").textContent=this.linkBudget===b.terms.budget_id?"Your pending private link is shown above.":awaitingApproval(b)?"This older invitation's link cannot be recovered automatically. Use the original saved link, or explicitly revoke it before creating a replacement.":"No approval QR is needed. Do not pay or reapprove an already-settled session.";
      if(multi&&accepted)this.$("next-step").textContent=b.multi_session?
        `${b.multi_session.remaining_sats} sat remaining of ${b.multi_session.max_total_sats} sat TOTAL including fees. ${b.multi_session.error||"Future-session collection enabled while the wallet is available."}`:
        "Approval saved. The driver must register their receiving wallet before future sessions qualify.";
    }
    const same=sameInvitationScope(b,this.$("scope").value,this.session);
    const immutable=same&&b.state!=="awaiting_driver_consent";
    this.$("existing-warning").hidden=!same;
    this.$("existing-warning").textContent=immutable?
      "This session already has a signed approval. These settings cannot change it. Use its existing collection or select a different future session.":
      "An unapproved invitation already exists. The same settings retrieve its original link. To change settings, explicitly replace it below.";
    this.$("replace-choice").hidden=!same||immutable;
    this.$("create").disabled=this.busy||!admin||!usable||immutable;
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
        if(!silent&&service==="create_session_budget")this.$("create-panel").open=false;
      }
      this.$("invitation").value=JSON.stringify(b.invitation,null,2);
      const p=b.automatic_credit?.txid?b.automatic_credit:b.collection;
      this.$("collection").textContent=p?`${stateLabel(p.state)}${p.txid?": "+p.txid:""}`:"No payment submitted under this approval.";
      if(!silent)this.$("status").textContent=service==="create_session_budget"?
        b.invitation_reused?"Existing invitation retrieved. Its original limits and expiry are unchanged.":
        "Invitation ready. Ask the driver to scan and review the terms.":"Approval record updated.";
      if(!silent&&service==="create_session_budget")this.$("replace-pending").checked=false;
      if(!silent&&service==="open_public_registration")this.$("status").textContent="Public QR window opened. Ask the intended driver to refresh the public page and scan or open the invitation.";
      if(!silent&&service==="close_public_registration")this.$("status").textContent="Public registration closed. Private links and historical settlements are unchanged.";
      return b;
    }catch(e){
      this.linkFresh=false;
      this.$("status").textContent=silent&&!this.budget?"No approval loaded. Create an invitation, or use Check approval to retry.":e.message||"Could not update the approval. Try again.";
    }finally{this.busy=false;this.paint();}
  }
}
if(!customElements.get("bsv-budget-card"))customElements.define("bsv-budget-card",BSVBudgetCard);

import "./completed-session-card.js";
import {ownerActionRows} from "./owner-actions.js";
import {styles,esc,num,short,stamp,chainRecordLink,energyMetrics} from "./ui.js";
import {approvalUrl,drawApprovalQR} from "./approval-qr.js";

class OwnerActionsCard extends HTMLElement{
  constructor(){super();this.attachShadow({mode:"open"});this.busy=false;}
  setConfig(c){
    this.config=c;if(this.ready)return;this.ready=true;
    this.shadowRoot.innerHTML=`<style>${styles}
      .account{padding:18px 0;border-bottom:1px solid var(--line)}
      .account h3{font-size:1rem}.account .actions{margin-top:10px}
      .account .note{margin-top:6px}.selected{border-left:3px solid var(--accent);padding-left:12px}
      #work{margin-top:20px;padding:18px;border:1px solid var(--line);border-radius:10px}
      #work:focus{outline:2px solid var(--accent)}#result{margin-top:12px}
      #work h3{margin-bottom:8px}summary{list-style-position:inside}
    </style><section aria-label="Owner credit actions">
      <div class="row"><h3>Unfinished transactions</h3><span id="count" class="badge"></span></div>
      <p class="note">Choose an action on the matching session. Review actions do not send money. Final approval is always separate.</p>
      <p id="access" class="notice" hidden></p><div id="queue"></div>
      <section id="work" tabindex="-1" hidden aria-label="Selected transaction">
        <div class="row"><h3 id="work-title"></h3><button id="close-work">Close details</button></div>
        <code id="work-id"></code><p id="message" class="note" role="status" aria-live="polite"></p>
        <div id="result"></div>
      </section>
      <details id="history"><summary id="history-title">Completed transactions</summary><div id="done"></div></details>
    </section>`;
    this.$("close-work").onclick=()=>{if(this.busy)return;this.selected=null;this.$("work").hidden=true;this.$("result").replaceChildren();this.paint();};
    this.shadowRoot.addEventListener("click",e=>{
      const b=e.target.closest("button[data-row]");if(!b)return;
      const v=this.rows[Number(b.dataset.row)];if(v)this.open(v,b.dataset.action);
    });
  }
  $(id){return this.shadowRoot.getElementById(id);}
  set hass(h){this.h=h;this.paint();if(this.closure)this.closure.hass=h;}
  disabled(){return this.busy||!this.h?.user?.is_admin||[this.config.wallet_entity,this.config.proxy_entity]
    .some(e=>!this.h?.states[e]||["unknown","unavailable"].includes(this.h.states[e].state));}
  paint(){
    if(!this.ready||!this.h)return;
    const p=this.h.states[this.config.proxy_entity]?.attributes||{},health=this.h.states[this.config.wallet_entity]?.attributes||{};
    this.rows=ownerActionRows(health,[p.latest_session,p.previous_session].filter(Boolean));
    const pending=this.rows.filter(v=>!v.complete),done=this.rows.filter(v=>v.complete),disabled=this.disabled();
    this.$("count").textContent=`${pending.length} unfinished`;
    this.$("access").hidden=!disabled;
    this.$("access").textContent=this.busy?"Checking the selected transaction…":!this.h.user?.is_admin?
      "Administrator access is required. Actions are shown but disabled.":"Recorder or wallet unavailable. Actions are disabled until both are available.";
    const rowHTML=v=>{
      const e=energyMetrics(v.session||{}),id=v.row.session_id;
      return `<article data-session="${esc(id)}" class="account ${this.selected?.row.session_id===id?"selected":""}">
        <div class="row"><h3>${v.row.account_kind==="manual_energy_adjustment"?"Adjustment":"Session"} ${esc(short(v.row.transaction_id||id.replace(/^sigen-proxy-/,"")))}</h3>
        <span class="badge ${v.complete?"good":"info"}">${esc(v.title)}</span></div>
        <p class="note">${Number.isSafeInteger(v.row.amount_sats)?`${num(v.row.amount_sats)} sat to owner`:"Amount not yet quoted"} · ${v.session?.ended_at?esc(stamp(v.session.ended_at)):"Historical time unavailable"}
        ${v.row.fee_sats!=null?` · ${num(v.row.fee_sats)} sat fee`:""}</p>
        <p class="note">${esc(v.note)}</p>
        ${v.row.error||v.row.diagnostic?.message?`<p class="notice">${esc(v.row.error||v.row.diagnostic.message)}</p>`:""}
        <div class="actions">${v.actions.map(a=>`<button class="${a.primary?"primary":""}" data-row="${this.rows.indexOf(v)}" data-action="${a.id}" ${disabled?"disabled":""}>${esc(a.label)}</button>`).join("")}
        ${v.row.txid?chainRecordLink(v.row.txid):""}</div>
        <details><summary>Energy and full reference</summary><code>${esc(id)}</code>
        <p class="note">Energy Imported to EV: ${num(e.toEV.kwh,2)} kWh · average ${num(e.toEV.average,4)} $/kWh<br>
        Energy Imported from EV: ${num(e.fromEV.kwh,2)} kWh · average ${num(e.fromEV.average,4)} $/kWh<br>
        Net AUD ${num(v.session?.net_cost_aud??v.row.net_amount_aud,2)}. Historical data is not estimated.</p></details></article>`;
    };
    // Retain the work panel and its form values across Home Assistant updates.
    const sig=JSON.stringify([this.rows,disabled,this.selected?.row.session_id]);
    if(sig!==this.signature){
      let focused=this.shadowRoot.activeElement;
      while(focused?.shadowRoot?.activeElement)focused=focused.shadowRoot.activeElement;
      const work=this.$("work");work.remove();
      this.signature=sig;this.$("queue").innerHTML=pending.map(rowHTML).join("")||'<p class="notice">No unfinished owner credits in the available records.</p>';
      this.$("done").innerHTML=done.map(rowHTML).join("");
      this.$("history-title").textContent=`Completed transactions (${done.length})`;
      const selected=[...this.$("queue").children].find(el=>el.dataset.session===this.selected?.row.session_id);
      (selected||this.$("queue")).after(work);
      if(focused?.isConnected)focused.focus({preventScroll:true});
    }
    for(const button of this.$("result").querySelectorAll("button"))button.disabled=disabled;
  }
  async call(service,data){
    const r=await this.h.callWS({type:"call_service",domain:"bsv_settlement",service,
      service_data:{config_entry_id:this.config.config_entry_id,...data},return_response:true});
    return r.response;
  }
  async run(fn){
    if(this.disabled())return;
    this.busy=true;this.paint();this.$("message").textContent="Checking the selected saved record…";
    for(const el of this.$("result").querySelectorAll("button"))el.disabled=true;
    try{await fn();}catch(e){
      this.$("result").replaceChildren();this.$("message").textContent=e.message||"Check failed. No successful action is confirmed. Review the saved record before trying again.";
    }finally{this.busy=false;this.paint();}
  }
  async open(v,action){
    if(this.disabled())return;
    if(action==="drivers"){location.assign("/bsv-settlement/drivers");return;}
    this.selected=v;this.$("work").hidden=false;this.$("work-title").textContent=v.actions.find(a=>a.id===action)?.label||"Session details";
    this.$("work-id").textContent=v.row.session_id;this.$("result").replaceChildren();this.$("message").textContent="";
    this.paint();this.$("work").scrollIntoView({behavior:"smooth",block:"nearest"});this.$("work").focus({preventScroll:true});
    if(action==="closure"){
      this.closure=document.createElement("bsv-completed-session-card");this.closure.setConfig(this.config);this.closure.hass=this.h;
      this.closure.selectSession(v.row.session_id);this.$("result").append(this.closure);return;
    }
    if(action==="manual"){
      this.dispatchEvent(new CustomEvent("open-saved-review",{detail:{review_id:v.row.review_id,session_id:v.row.session_id},bubbles:true,composed:true}));return;
    }
    await this.run(async()=>{
      if(action==="check"){
        await this.call("wallet_refresh_chain",{});this.$("message").textContent="Provider refresh completed. No payment was sent. Check the updated transaction status; a successful refresh does not mean payment is confirmed.";
      }else if(action==="approval"){
        const r=await this.call("session_budget_status",{budget_id:v.budgetId});
        if((r.binding?.session_id||r.terms?.session_id)!==v.row.session_id)throw Error("Saved approval does not match this session. No link displayed.");
        this.$("message").textContent=`Saved approval: ${r.state}. Expires ${stamp(r.terms?.expires_at)}.`;
        const expired=!Number.isFinite(Date.parse(r.terms?.expires_at))||Date.parse(r.terms.expires_at)<=Date.now();
        let fragment=r.driver_link_fragment;
        if(!expired&&r.collection?.state==="recovery_ready"){
          const plan=await this.call("prepare_collection_recovery",{budget_id:v.budgetId});
          if(plan.session_id!==v.row.session_id)throw Error("Reviewed collection does not match this session.");
          const link=await this.call("get_reviewed_collection_link",{budget_id:v.budgetId,
            expected_quote_hash:plan.quote_hash,confirm_private_link_disclosure:true});
          if(link.session_id!==v.row.session_id)throw Error("Recovered link does not match this session.");
          fragment=link.driver_link_fragment;
        }
        const url=!expired&&approvalUrl(fragment,v.budgetId,location.origin);
        this.$("result").innerHTML=`<p class="notice">${expired?"Approval expired. It cannot authorise a new collection. Review the existing charge for closure; do not replace an uncertain payment.":
          r.state==="awaiting_driver_consent"?"The driver must review and authorise the displayed budget. Opening or scanning alone does not approve spending.":
          "Use the original driver's wallet. Opening this link can resume an already-authorised collection; it does not create new consent."}</p>`;
        if(url){
          const box=document.createElement("div");box.innerHTML='<div class="qr"></div><label>Private driver link<textarea readonly rows="4" style="overflow-wrap:anywhere;word-break:break-all"></textarea></label><button>Copy private link</button><p class="note">Share only with the original driver. Do not publish this QR.</p>';
          box.querySelector("textarea").value=url;drawApprovalQR(box.querySelector(".qr"),url);
          box.querySelector("button").onclick=async()=>{try{await navigator.clipboard.writeText(url);this.$("message").textContent="Private link copied.";}catch{box.querySelector("textarea").select();this.$("message").textContent="Link selected. Use Copy on your device.";}};
          this.$("result").append(box);
        }else if(!expired)this.$("result").insertAdjacentHTML("beforeend",'<p class="note">A recoverable private link is not available here. The original driver can sign in at the static driver portal to view their sessions.</p><a class="button" href="/bsv_settlement/driver/index.html" target="_blank" rel="noopener noreferrer">Open driver portal</a>');
      }else if(action==="waiver"){
        const target=v.row.source==="manual"?{review_id:v.row.review_id}:{budget_id:v.budgetId};
        const p=await this.call("prepare_existing_charge_waiver",target);
        if(p.session_id!==v.row.session_id)throw Error("Waiver review does not match this session.");
        this.$("message").textContent="Read-only waiver review complete. The charge is not yet waived.";
        this.$("result").innerHTML=`<p class="notice">Waive ${num(p.amount_sats)} sat owed to the owner. This does not mark it paid, refund funds or cancel an external wallet action.</p>
          <label>Reason<input id="waiver-reason" maxlength="300"></label>
          <label><input id="waiver-confirm" type="checkbox">I authorise this waiver, no refund, and separate accounting for any external payment.</label>
          <button id="waiver-submit" class="danger">Confirm waiver of ${num(p.amount_sats)} sat</button>`;
        this.$("waiver-submit").onclick=()=>{
          const reason=this.$("waiver-reason").value.trim();
          if(reason.length<8||!this.$("waiver-confirm").checked){this.$("message").textContent="Enter a reason of at least 8 characters and confirm the waiver terms.";return;}
          if(!window.confirm(`Waive ${p.amount_sats} sat for ${p.session_id}? No refund or payment is authorised. Reason: ${reason}`))return;
          this.run(async()=>{
            await this.call("waive_existing_charge",{...target,expected_review_hash:p.review_hash,expected_amount_sats:p.amount_sats,reason,
              confirm_waive_charge:true,confirm_no_refund:true,confirm_external_payments_need_separate_accounting:true});
            this.$("result").replaceChildren();this.$("message").textContent="Charge waived. It was not paid or refunded. Audit history retained.";
          });
        };
      }else if(action==="recovery"){
        const p=await this.call("prepare_collection_recovery",{budget_id:v.budgetId});
        if(p.session_id!==v.row.session_id)throw Error("Recovery review does not match this session.");
        this.$("message").textContent=p.reason;
        this.$("result").innerHTML=`<p class="notice">${p.eligible_for_review?"Pre-signing hold. External wallet and recipient-history checks are still required before release.":"No release is available. The saved attempt reached signing or submission, or is not a releasable hold."}</p>`;
        if(p.eligible_for_review){
          const confirmations=[
            ["confirm_driver_wallet_checked","Driver wallet checked: no completed or signed payment"],
            ["confirm_recipient_history_checked","Confirmed and unconfirmed recipient history checked: no matching payment"],
            ["confirm_unsigned_draft_cancelled_or_absent","Old unsigned draft cancelled or confirmed absent"],
            ["confirm_old_driver_pages_closed","All old driver pages are closed"]];
          this.$("result").insertAdjacentHTML("beforeend",`<label>Nonsecret evidence reference<input id="recovery-evidence" maxlength="200"></label>${confirmations.map(([id,text])=>`<label><input type="checkbox" id="${id}">${text}</label>`).join("")}<button id="release">Release for driver review</button><p class="note">Release does not send money. The driver must explicitly resume, and server checks can still refuse release.</p>`);
          this.$("release").onclick=()=>{
            const evidence=this.$("recovery-evidence").value.trim();
            if(!/^[A-Za-z0-9 _.:/-]{8,200}$/.test(evidence)||confirmations.some(([id])=>!this.$(id).checked)){
              this.$("message").textContent="Complete all four checks and enter a nonsecret evidence reference.";return;
            }
            if(!window.confirm(`Release the pre-signing hold for ${p.session_id}, ${p.amount_sats} sat, for explicit driver review? No payment is sent.`))return;
            this.run(async()=>{
              await this.call("recover_driver_collection",{budget_id:v.budgetId,expected_quote_hash:p.quote_hash,expected_claimed_at:p.claimed_at,
                evidence_reference:evidence,...Object.fromEntries(confirmations.map(([id])=>[id,true]))});
              this.$("result").replaceChildren();this.$("message").textContent="Released for explicit driver review. No payment sent. Open the saved approval to share the original link.";
            });
          };
        }
      }
    });
  }
}
if(!customElements.get("bsv-owner-actions-card"))customElements.define("bsv-owner-actions-card",OwnerActionsCard);

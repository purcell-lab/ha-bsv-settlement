import qrcode from "qrcode-generator";
import {styles, stateLabel, short, stamp} from "./ui.js";
import {settlementRows,paymentStatus} from "./payment-status.js";

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

class BsvSessionReviewCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this._busy = false;
    this._message = "";
  }
  setConfig(config) {
    for (const key of ["config_entry_id", "proxy_config_entry_id", "proxy_entity", "wallet_entity", "rate_entity"]) {
      if (!config[key]) throw new Error(`${key} is required`);
    }
    this._config = config;
    this._signature = "";
    this.render();
  }
  set hass(hass) {
    this._hass = hass;
    if (this._config) {
      const r = hass.states[this._config.wallet_entity]?.attributes?.latest_session_review ?? null;
      const serialized = JSON.stringify(r);
      if (serialized !== this._serverReview) {
        this._serverReview = serialized;
        this._review = r;
      }
    }
    this.render();
  }
  getCardSize() { return 12; }
  getGridOptions() { return {columns: 12, rows: "auto"}; }
  async action(service, data = {}) {
    if (this._busy || !this._hass?.user?.is_admin) return;
    this._busy = true;
    this._message = "";
    this.render();
    try {
      const result = await this._hass.callWS({
        type: "call_service", domain: "bsv_settlement", service,
        service_data: {config_entry_id: this._config.config_entry_id, ...data},
        return_response: true,
      });
      if(!["configure_automatic_credit","configure_ongoing_credit"].includes(service)) this._review = result.response;
      this._message = "Action completed. Review the updated state below.";
    } catch (err) {
      this._message = err.message || String(err);
    } finally {
      this._busy = false;
      this.render();
    }
  }
  render() {
    if (!this._config || !this._hass) return;
    const c = this._config, h = this._hass, r = this._review;
    const proxy = h.states[c.proxy_entity], wallet = h.states[c.wallet_entity];
    const automatic = wallet?.attributes?.automatic_credit;
    const ongoing = wallet?.attributes?.ongoing_credit;
    const ready = proxy && wallet && !["unknown", "unavailable"].includes(proxy.state) &&
      !["unknown", "unavailable"].includes(wallet.state);
    const admin = h.user?.is_admin === true;
    const sessions = [proxy?.attributes?.latest_session, proxy?.attributes?.previous_session]
      .filter(s => s && s.ended_at);
    const observedSessions=[proxy?.attributes?.latest_session,proxy?.attributes?.previous_session].filter(Boolean);
    const health=wallet?.attributes||{},paymentRows=settlementRows(health);
    const presentations=paymentRows.map(row=>paymentStatus(row,observedSessions));
    const signature = JSON.stringify([r, ready, admin, sessions.map(s => [s.session_id, s.net_cost_aud]),
      this._busy, this._message, automatic, ongoing,health.session_payments,observedSessions,presentations]);
    if (signature === this._signature) return;
    this._signature = signature;
    const disabled = this._busy || !ready || !admin;
    const exact = r ? {review_id: r.review_id, terms_hash: r.terms_hash,
      recipient_address: r.recipient_address, amount_sats: r.amount_sats} : null;
    const sameReview=this._formReview===JSON.stringify([r?.terms_hash,r?.state,r?.credit_draft?.draft_id]);
    const savedForms=sameReview?[...this.shadowRoot.querySelectorAll("input,select,textarea")].map(e=>({id:e.id,value:e.value,checked:e.checked})):[];
    const focused=sameReview?this.shadowRoot.activeElement?.id:null;
    const opened=[...this.shadowRoot.querySelectorAll("details[open]")].map(e=>e.querySelector("summary")?.textContent);
    this._formReview=JSON.stringify([r?.terms_hash,r?.state,r?.credit_draft?.draft_id]);
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block}ha-card{display:block;padding:22px;color:var(--primary-text-color,#202124)}
        h2{font-size:20px;font-weight:500;margin:0 0 10px}h3{font-size:16px;margin:22px 0 10px}
        p,.note,label{font-size:14px;line-height:1.5}.note{color:var(--secondary-text-color,#606368)}
        label{display:block;margin:12px 0}input,select,textarea,button{font:inherit;box-sizing:border-box}
        input:not([type=checkbox]),select,textarea{width:100%;padding:10px;border:1px solid var(--divider-color,#aaa);
          border-radius:6px;color:inherit;background:var(--card-background-color,#fff)}
        input[type=checkbox]{margin-right:8px;accent-color:var(--primary-color,#006f79)}
        button{padding:10px 14px;margin:6px 8px 6px 0;border:1px solid var(--primary-color,#006f79);
          background:transparent;color:var(--primary-color,#006f79);border-radius:6px;cursor:pointer}
        button:disabled{opacity:.45;cursor:not-allowed}.danger{color:var(--error-color,#b3261e);border-color:currentColor}
        code{display:block;overflow-wrap:anywhere;white-space:normal;line-height:1.6;font-size:13px}
        dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:14px}dt{color:var(--secondary-text-color,#606368)}
        dd{margin:0;text-align:right;overflow-wrap:anywhere}
        .notice{padding:10px;border:1px solid var(--divider-color,#ccc);border-radius:6px}
        .qr{max-width:220px;margin:16px auto;background:#fff;line-height:0}.qr svg{width:100%;height:auto}
        textarea{min-height:130px;font-size:12px}.divider{border-top:1px solid var(--divider-color,#ddd);margin:20px 0}
      </style><style>${styles}</style>
      <ha-card>
        <div class="head"><div><p class="eyebrow">Settlement</p><h2>Payments & credits</h2></div><span class="badge ${automatic?.enabled?"good":"warn"}">${automatic?.enabled?"Automatic credits on":"Automatic credits off"}</span></div>
        <p class="note">Follow each session from review to confirmation. A submitted transaction must be reconciled, never paid again.</p>
        ${ongoing?.enabled?`<div class="section"><div class="row"><h3>Ongoing driver credits</h3><span class="badge ${ongoing.effective?"good":"warn"}">${ongoing.effective?"Enabled":"Paused by master policy"}</span></div>
        <p class="note">Credits go to the last verified driver registered before each new session opens. The initial session uses the recipient explicitly selected at activation. Each assigned recipient and conversion is fixed; later registrations affect only later sessions.</p>
        <p class="note">Latest registered receiving address</p><code>${esc(ongoing.recipient?.address||"Unavailable: no valid registered recipient")}</code>
        <p class="note">Maximum 1,000 sat per session including a 10 sat fee. No cumulative cap. Credits only; driver charges still need a separate valid spending approval.</p>
        ${ongoing.error?`<p class="notice">${esc(ongoing.error)}</p>`:""}
        <button id="stop-ongoing" class="danger" ${disabled?"disabled":""}>Stop ongoing driver credits</button>
        <p class="note">This stops the ongoing policy, not separately approved session credits. Submitted transactions continue to be reconciled.</p></div>`:""}
        ${paymentRows.length?`<section class="section" aria-label="Session settlement"><h3>Session settlement</h3>
        ${paymentRows.map((row,index)=>{const p=presentations[index];return `<article class="notice session-payment" style="margin-top:12px" data-session="${esc(row.session_id)}">
          <p class="note">Session ${esc(short(p.reference))}</p><strong>${esc(p.title)}</strong><p>${esc(p.detail)}</p>
          ${row.max_fee_sats!==undefined?`<p class="note">Maximum wallet fee: ${esc(row.max_fee_sats)} sat. This is a limit, not a fee already charged.</p>`:""}
          ${row.txid?`<details><summary>Transaction reference</summary><code>${esc(row.txid)}</code></details>`:""}
        </article>`}).join("")}</section>`:""}
        ${(automatic?.payments || []).slice().reverse().map(p=>`<article class="payment"><div class="row"><h3>${esc(p.amount_sats)} sat to driver</h3><span class="badge">${esc(stateLabel(p.state))}</span></div><p class="note">Session ${esc(short(p.transaction_id))} · ${esc(p.fee_sats)} sat fee · Automatic credit</p>
        <details><summary>Payment details</summary><dl><dt>Driver receives</dt><dd>${esc(p.amount_sats)} sat</dd><dt>Operator fee</dt><dd>${esc(p.fee_sats)} sat</dd></dl><p class="note">Session</p><code>${esc(p.transaction_id)}</code><p class="note">Recipient</p><code>${esc(p.recipient_address)}</code><p class="note">BSV transaction</p><code>${esc(p.txid || "Not submitted")}</code></details>
        ${p.error ? `<p class="notice">${esc(p.error)}</p>` : ""}</article>`).join("")}
        <details><summary>Automatic-credit policy</summary><p class="note">Eligible credits use a session-specific approval and receiving key registered before session end. Maximum operator spend: 1,000 sat per session including a 10 sat fee. Registration does not guarantee payment; final account, funding and limits are checked.</p>
        ${automatic?.enabled ? `<button id="stop-credits" class="danger" ${disabled ? "disabled" : ""}>Stop new automatic credits</button>` : ""}</details>
        ${!admin ? '<p class="notice">Administrator access is required for these actions.</p>' : ""}
        ${!ready ? '<p class="notice">Activate the recorder and mainnet wallet before using this flow.</p>' : ""}
        <details id="new-review" ${!r?"open":""}><summary>Review a completed session manually</summary>
        <p class="note">Use only when automatic settlement is not available. Review the account and receiving address before preparing a payment. Preparing a review sends no money.</p>
        <label>Closed session<select id="session">${sessions.map(s =>
          `<option value="${esc(s.session_id)}">${esc(stamp(s.ended_at))} · ${esc(short(s.ocpp_transaction_id))} · AUD ${esc(s.net_cost_aud ?? "unavailable")}</option>`
        ).join("") || '<option value="">No completed session available</option>'}</select></label>
        <button id="prepare" class="primary" ${disabled || !sessions.length ? "disabled" : ""}>Prepare account for review</button>
        <p class="note">The conversion rate is fixed when the review is prepared.</p></details>
        <button id="refresh" ${disabled ? "disabled" : ""}>Check saved review</button>
        <div role="status" aria-live="polite">${esc(this._busy ? "Working…" : this._message)}</div>
        ${r ? `<div class="divider"></div>
          <h3>${esc(r.direction === "driver_to_operator" ? "Driver payment request" : r.direction === "operator_to_driver" ? "Operator credit" : "Zero account")}</h3>
          <p><span class="badge">${esc(stateLabel(r.credit_draft?.state||r.state))}</span></p>
          <dl><dt>Frozen account</dt><dd>AUD ${esc(r.account.net_amount_aud)}</dd>
          <dt>Frozen conversion</dt><dd>${esc(r.satoshis_per_aud)} sat/AUD</dd>
          <dt>Recipient amount</dt><dd>${esc(r.amount_sats)} sat</dd></dl>
          <p class="note">Proxy transaction ID</p><code>${esc(r.account.ocpp_transaction_id)}</code>
          <p class="note">Recipient</p><code>${esc(r.recipient_address || "No payment required")}</code>
          <p class="note">Review expires: ${esc(stamp(r.expires_at))}<br>Receiving address: ${r.identity_verification==="administrator_attested_not_cryptographic"?"confirmed by the operator":"not independently verified"}. Manual addresses are separate from session-specific wallet keys.</p>
          <details><summary>Frozen terms fingerprint</summary><code>${esc(r.terms_hash)}</code></details>
          <div id="flow"></div>` : ""}
      </ha-card>`;
    const $ = selector => this.shadowRoot.querySelector(selector);
    if($("#stop-credits")) $("#stop-credits").onclick=()=>{
      if(confirm("Stop new automatic credits? Submitted payments will still be reconciled. Re-enabling requires new invitations."))this.action("configure_automatic_credit",{enabled:false});
    };
    if($("#stop-ongoing"))$("#stop-ongoing").onclick=()=>{
      if(confirm("Stop ongoing driver credits? Submitted payments will still be reconciled. Separately approved session credits remain enabled."))this.action("configure_ongoing_credit",{enabled:false});
    };
    $("#prepare").onclick = () => this.action("prepare_session_review", {
      proxy_config_entry_id: c.proxy_config_entry_id, session_id: $("#session").value,
      conversion_rate_entity: c.rate_entity,
    });
    $("#refresh").onclick = () => this.action("session_review_status");
    if (!r) return;
    const flow = $("#flow");
    if (r.state === "awaiting_account_approval") {
      flow.innerHTML = `
        <label><input id="account" type="checkbox">I reviewed the closed account and its provisional meter allocation.</label>
        <label><input id="driver" type="checkbox">I independently confirmed the driver's public identity and receiving address. This is not cryptographic proof.</label>
        <button id="approve" disabled>Approve account for ${r.direction === "operator_to_driver" ? "credit preparation" : "payment request"}</button>
        <button id="cancel" ${disabled ? "disabled" : ""}>Cancel unsigned review</button>`;
      const enable = () => {$("#approve").disabled = disabled || !$("#account").checked || !$("#driver").checked;};
      $("#account").onchange = enable;
      $("#driver").onchange = enable;
      $("#approve").onclick = () => this.action("approve_session_review", {
        ...exact, confirm_account_review: true, confirm_driver_details: true,
      });
      $("#cancel").onclick = () => this.action("cancel_session_review", {review_id:r.review_id});
    } else if (r.state === "expired" && !r.payment_request && !r.credit_draft) {
      flow.innerHTML = `<p class="note">Expired. Only an unapproved review can be cancelled and replaced.</p>
        <button id="cancel" ${disabled ? "disabled" : ""}>Cancel unapproved review</button>`;
      $("#cancel").onclick = () => this.action("cancel_session_review", {review_id:r.review_id});
    }
    if (r.payment_request) {
      const canPay = r.state === "awaiting_driver_payment" && Date.parse(r.expires_at) > Date.now();
      const box = document.createElement("div");
      box.innerHTML = `${canPay ? `<h3>Pay from the driver's BSV wallet</h3>
        <p>Enter exactly <strong>${esc(r.amount_sats)} satoshis</strong> to the address above.
        Approve the network fee in the driver wallet.</p>
        <div class="qr"></div>
        <p class="note">The QR contains only the address, not the amount, session reference or spending authority.
        Do not pay an expired request. This is a manual request, not an integrated BRC wallet approval.</p>` :
        '<p class="notice">No new payment is requested. QR withheld: reconcile the existing or expired request instead of paying again.</p>'}
        <details><summary>Frozen payment request record</summary>
        <label>Request JSON<textarea readonly aria-label="Payment request JSON"></textarea></label></details>
        <h3>Check the driver's reported payment</h3>
        <label>Transaction ID<input id="txid" type="text" maxlength="64" autocomplete="off"></label>
        <label>Output index<input id="vout" type="number" min="0" step="1" placeholder="0"></label>
        <label><input id="reference" type="checkbox">I independently confirmed this transaction was supplied by the driver for this request.</label>
        <button id="verify" disabled>Verify exact transaction output</button>
        <p class="note">Verification checks provider evidence and the exact output. It does not prove payer identity or independent chain finality.</p>`;
      box.querySelector("textarea").value = JSON.stringify(r.payment_request, null, 2);
      if (canPay) {
        const qr = qrcode(0,"M");
        qr.addData(r.recipient_address,"Byte");
        qr.make();
        box.querySelector(".qr").innerHTML = qr.createSvgTag({cellSize:4,margin:16,scalable:true});
        box.querySelector("svg").setAttribute("role","img");
        box.querySelector("svg").setAttribute("aria-label","BSV address only: " + r.recipient_address);
      }
      flow.append(box);
      const enable = () => {
        $("#verify").disabled = disabled || !$("#reference").checked ||
          !/^[0-9a-f]{64}$/.test($("#txid").value) || $("#vout").value === "" ||
          !Number.isInteger(Number($("#vout").value)) || Number($("#vout").value) < 0;
      };
      $("#reference").onchange = enable; $("#txid").oninput = enable; $("#vout").oninput = enable;
      $("#verify").onclick = () => this.action("verify_session_driver_payment", {
        review_id:r.review_id, txid:$("#txid").value, output_index:Number($("#vout").value),
        confirm_driver_payment_reference:true,
      });
    }
    if (r.state === "credit_review_approved" && !r.credit_draft) {
      flow.innerHTML = `<label>Exact operator network fee, satoshis<input id="fee" type="number" min="1" max="1000" step="1" placeholder="Enter reviewed fee"></label>
        <p class="note">1 to 1000 sat is the demonstration cap, not a network fee estimate. Preparation does not sign or broadcast.</p>
        <button id="credit" disabled>Prepare unsigned operator credit</button>
        <button id="cancel-credit" ${disabled ? "disabled" : ""}>Cancel unsigned review</button>`;
      $("#fee").oninput = () => {
        const v = Number($("#fee").value);
        $("#credit").disabled = disabled || !Number.isInteger(v) || v < 1 || v > 1000;
      };
      $("#credit").onclick = () => this.action("prepare_session_credit", {
        review_id:r.review_id, terms_hash:r.terms_hash, fee_sats:Number($("#fee").value),
      });
      $("#cancel-credit").onclick = () => this.action("cancel_session_review", {review_id:r.review_id});
    }
    if (r.credit_draft) {
      const p = r.credit_draft;
      flow.innerHTML = `<h3>Exact operator credit</h3>
        <dl><dt>Recipient receives</dt><dd>${esc(p.amount_sats)} sat</dd>
        <dt>Network fee</dt><dd>${esc(p.fee_sats)} sat</dd>
        <dt>Total operator spend</dt><dd>${esc(p.amount_sats+p.fee_sats)} sat</dd></dl>
        <code>${esc(p.recipient_address)}</code><p class="note">Draft state: ${esc(p.state)}.
        Expiry: ${esc(p.expires_at)}. Review account expiry also applies.</p>
        ${p.state === "prepared" && Date.parse(r.expires_at) > Date.now() && Date.parse(p.expires_at) > Date.now() ? `<label><input id="send-consent" type="checkbox">I authorise a real mainnet transfer of ${esc(p.amount_sats)} sat to this exact address, plus ${esc(p.fee_sats)} sat fee.</label>
        <button id="send" class="danger" disabled>Approve and broadcast this credit</button>` :
          `<p class="notice">${p.txid?(p.state==="provider_confirmed"?"Payment confirmed. No further payment is required.":"Payment submitted. Wait for confirmation; do not send again."):"This draft can no longer be sent. Cancel only an unsigned draft before preparing another."}</p>`}
        ${["prepared","expired"].includes(p.state) ? `<button id="cancel-draft" ${disabled ? "disabled" : ""}>Cancel unsigned credit</button>` : ""}
        ${p.txid ? `<p class="note">Transaction ID</p><code>${esc(p.txid)}</code>` : ""}`;
      if ($("#cancel-draft")) $("#cancel-draft").onclick = () => this.action("cancel_session_review", {review_id:r.review_id});
      if ($("#send")) {
        $("#send-consent").onchange = () => {$("#send").disabled = disabled || !$("#send-consent").checked;};
        $("#send").onclick = () => this.action("broadcast_session_credit", {
          ...exact, draft_id:p.draft_id, recipient_address:p.recipient_address,
          amount_sats:p.amount_sats, fee_sats:p.fee_sats, confirm_mainnet_payment:true,
        });
      }
    }
    if (r.receipt) {
      const note=document.createElement("p");
      note.className="notice";
      note.textContent=`Provider confirmations: ${r.receipt.confirmations}. Evidence checked: ${r.receipt.checked_at}. ${r.receipt.verification_error ? "Latest verification failed; prior evidence is not current." : ""}`;
      flow.append(note);
    }
    for(const f of savedForms){const e=this.shadowRoot.getElementById(f.id);if(e){e.value=f.value;e.checked=f.checked;}}
    for(const d of this.shadowRoot.querySelectorAll("details"))if(opened.includes(d.querySelector("summary")?.textContent))d.open=true;
    for(const id of ["account","driver","reference","send-consent"]){const e=$("#"+id);if(e?.checked)e.dispatchEvent(new Event("change"));}
    if($("#fee")?.value)$("#fee").dispatchEvent(new Event("input"));
    if(focused)this.shadowRoot.getElementById(focused)?.focus({preventScroll:true});
  }
}

if (!customElements.get("bsv-session-review-card")) {
  customElements.define("bsv-session-review-card", BsvSessionReviewCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some(c => c.type === "bsv-session-review-card")) {
  window.customCards.push({type:"bsv-session-review-card",name:"BSV session payment review",
    description:"Freeze session accounts, issue manual driver requests and explicitly approve operator credits.",preview:false});
}

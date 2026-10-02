/** Administrative invitation/receipt transfer. Never invokes a payment action. */
class BSVBudgetCard extends HTMLElement {
  setConfig(config) {
    if (!config.config_entry_id || !config.proxy_config_entry_id || !config.proxy_entity || !config.rate_entity)
      throw Error("Configure wallet, recorder and conversion-rate entities.");
    this.config = config; this.budget=null; this.initialRead=false;
    if (!this.shadowRoot) this.attachShadow({mode:"open"});
    this.shadowRoot.innerHTML = `<ha-card header="Driver session budget"><div class="body">
      <p>Automatic collection is available for a version 2 spending approval. The driver must keep the approval page open in BSV Browser, and you must bind the correct charging session. After it ends, the wallet prepares and signs within the approved limits; the server submits the payment once. Wallet permission prompts may still appear. Net credits remain operator-reviewed.</p>
      <p id="session"></p>
      <label>Operator name<input id="name" maxlength="100"></label>
      <label>Operator contact<input id="contact" maxlength="200" placeholder="Contact email or phone"></label>
      <label>Total budget, including fees (sat)<input id="total" type="number" min="1" max="100000" step="1" value="1000"></label>
      <label>Maximum network fee (sat)<input id="fee" type="number" min="0" max="1000" step="1" value="10"></label>
      <label>Spending approval valid for (minutes)<input id="minutes" type="number" min="1" max="1440" step="1" value="720"></label>
      <p id="rate"></p>
      <button id="create">Create pre-session approval link</button>
      <label>Private driver link<input id="driver-link" readonly></label>
      <p><a id="open-link" hidden target="_blank" rel="noopener noreferrer">Open this driver's approval page</a></p>
      <p>Share only with the intended driver. The link alone cannot spend: collection also requires the approved driver wallet and a valid signed transaction. Old consent receipts do not become spending approvals.</p>
      <p><a href="/bsv_settlement/driver/index.html" target="_blank" rel="noopener">Open driver approval page</a></p>
      <details><summary>Manual JSON fallback</summary>
      <label>Invitation JSON<textarea id="invitation" rows="5" readonly></textarea></label>
      <label>Signed receipt from driver<textarea id="receipt" rows="5" placeholder="Paste receipt JSON"></textarea></label>
      <button id="accept" disabled>Verify and save driver approval</button>
      </details>
      <button id="refresh" disabled>Refresh budget status</button>
      <button id="bind" disabled>Bind approval to latest session</button>
      <button id="revoke" disabled>Revoke this approval</button>
      <p id="status" role="status" aria-live="polite">Create an invitation before the charging session opens.</p>
      <p id="collection"></p>
      <p>The private link is returned once. Keep it before leaving this card. Automatic session binding is deliberately disabled to avoid assigning another driver's session.</p>
    </div></ha-card><style>
      .body{padding:0 20px 20px;color:var(--primary-text-color)}p{line-height:1.5;overflow-wrap:anywhere}
      label{display:block;margin:14px 0;font-size:14px}input,textarea{box-sizing:border-box;width:100%;margin-top:5px;padding:10px;border:1px solid var(--divider-color);border-radius:4px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit}
      textarea{font-family:monospace;font-size:12px;resize:vertical}button{min-height:44px;padding:8px 14px;margin:4px 4px 4px 0;border:1px solid var(--primary-color);background:transparent;color:var(--primary-color);border-radius:4px;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}a{color:var(--primary-color)}:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    </style>`;
    this.$("name").value = config.operator_name || "Charging operator";
    this.$("contact").value = config.operator_contact || "";
    this.$("create").onclick = () => this.perform("create_session_budget", {
      proxy_config_entry_id: config.proxy_config_entry_id,
      conversion_rate_entity: config.rate_entity, max_total_sats: this.number("total"),
      max_fee_sats: this.number("fee"), valid_minutes: this.number("minutes"),
      operator_name: this.$("name").value, operator_contact: this.$("contact").value});
    this.$("accept").onclick = () => {
      try { const receipt = JSON.parse(this.$("receipt").value);
        return this.perform("accept_session_budget", {budget_id:receipt.budget_id, receipt});
      } catch (_) { this.$("status").textContent = "Paste a valid receipt JSON record."; }
    };
    this.$("refresh").onclick = () => this.perform("session_budget_status", {budget_id:this.budget.terms.budget_id});
    this.$("bind").onclick = () => {
      if (confirm("Confirm the latest session belongs to this driver. For a version 2 spending approval, binding enables automatic collection after the session ends, within the signed limits. No charger control is performed."))
        this.perform("bind_session_budget", {budget_id:this.budget.terms.budget_id,
          session_id:this.session.session_id, confirm_driver_present:true});
    };
    this.$("revoke").onclick = () => {
      if (confirm("Revoke this session budget consent in HA? This does not reverse any payment."))
        this.perform("revoke_session_budget", {budget_id:this.budget.terms.budget_id});
    };
  }
  connectedCallback() {
    if(!this.refreshTimer)this.refreshTimer=setInterval(()=>{
      if(this.budget && this._hass?.user?.is_admin && !this.busy)
        this.perform("session_budget_status",{budget_id:this.budget.terms.budget_id});
    },15000);
  }
  disconnectedCallback() {clearInterval(this.refreshTimer);this.refreshTimer=null;}
  $(id) { return this.shadowRoot.getElementById(id); }
  number(id) { const v = this.$(id).value; return v.trim() ? Number(v) : null; }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const state = hass.states[this.config.proxy_entity];
    this.session = state?.attributes.latest_session;
    this.$("session").textContent = this.session ? `Session: ${this.session.ocpp_transaction_id} (${this.session.ended_at ? "closed" : "open"})` : "Waiting for a recorded session.";
    const admin = !!hass.user?.is_admin;
    this.$("rate").textContent = `Conversion: ${hass.states[this.config.rate_entity]?.state || "unavailable"} sat/AUD. Default 1,000 sat equals AUD10 only at 100 sat/AUD.`;
    this.$("create").disabled = this.busy || !admin || !state || ["unknown","unavailable"].includes(state.state);
    this.$("accept").disabled = this.busy || !admin;
    this.$("refresh").disabled = this.busy || !admin || !this.budget;
    this.$("revoke").disabled = this.busy || !admin || !this.budget || this.budget.state === "revoked";
    this.$("bind").disabled = this.busy || !admin || !this.session ||
      !["consent_verified_not_payment_authority","spending_authorised_wallet_permission_required"].includes(this.budget?.state) || !!this.budget?.binding;
    if (admin && !this.initialRead) {
      this.initialRead=true;
      this.perform("session_budget_status",{});
    }
  }
  async perform(service, data) {
    if (this.busy || !this._hass?.user?.is_admin) return;
    this.busy = true; this.hass = this._hass;
    this.$("status").textContent = "Waiting for Home Assistant…";
    try {
      const result = await this._hass.callWS({type:"call_service",domain:"bsv_settlement",
        service,service_data:{config_entry_id:this.config.config_entry_id,...data},return_response:true});
      this.budget = result.response;
      if (this.budget.driver_link_fragment) {
        const link = new URL("/bsv_settlement/driver/index.html",location.origin);
        link.hash = this.budget.driver_link_fragment.slice(1);
        this.$("driver-link").value=link.href;
        this.$("open-link").href=link.href;this.$("open-link").hidden=false;
      }
      this.$("invitation").value = JSON.stringify(this.budget.invitation, null, 2);
      this.$("status").textContent = `${this.budget.state}${this.budget.binding ? " · bound to "+this.budget.binding.transaction_id : ""}. ${this.budget.terms.version === 2 ? "Automatic collection requires the driver's open page and wallet permission." : "Legacy consent only; revoke and create a new invitation to approve spending."} No charger control.`;
      const c=this.budget.collection;
      this.$("collection").textContent=c ? `Collection: ${c.state}${c.fee_sats!==undefined ? ". Fee: "+c.fee_sats+" sat" : ""}${c.txid ? ". BSV transaction: "+c.txid : ""}${c.error ? ". "+c.error : ""}` : "No collection attempt yet. Status refreshes every 15 seconds.";
      if(service==="create_session_budget"&&!this.budget.driver_link_fragment&&!this.$("driver-link").value)
        this.$("status").textContent += " Existing invitation retained. If you lost its link, revoke it before creating a replacement.";
    } catch (e) { this.$("status").textContent = e.message || "Budget operation failed."; }
    finally {this.busy = false; this.hass = this._hass;}
  }
  getCardSize() { return 12; }
}
if (!customElements.get("bsv-budget-card")) customElements.define("bsv-budget-card", BSVBudgetCard);

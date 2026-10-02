/** Administrative invitation/receipt transfer. Never invokes a payment action. */
class BSVBudgetCard extends HTMLElement {
  setConfig(config) {
    if (!config.config_entry_id || !config.proxy_config_entry_id || !config.proxy_entity || !config.rate_entity)
      throw Error("Configure wallet, recorder and conversion-rate entities.");
    this.config = config; if (!this.shadowRoot) this.attachShadow({mode:"open"});
    this.shadowRoot.innerHTML = `<ha-card header="Driver session budget"><div class="body">
      <p>Sign budget consent in BSV Browser. This does not enable payment collection or charger control.</p>
      <p id="session"></p>
      <label>Total budget, including fees (sat)<input id="total" type="number" min="1" max="100000" step="1" placeholder="Enter maximum"></label>
      <label>Maximum network fee (sat)<input id="fee" type="number" min="0" max="1000" step="1" placeholder="Enter fee allowance"></label>
      <label>Consent valid for (minutes)<input id="minutes" type="number" min="1" max="1440" step="1" value="120"></label>
      <button id="create">Create invitation for open session</button>
      <p><a href="/bsv_settlement/driver/index.html" target="_blank" rel="noopener">Open driver approval page</a></p>
      <p>Copy the invitation to the driver page. No HA token or login is required there.</p>
      <label>Invitation JSON<textarea id="invitation" rows="5" readonly></textarea></label>
      <label>Signed receipt from driver<textarea id="receipt" rows="5" placeholder="Paste receipt JSON"></textarea></label>
      <button id="accept" disabled>Verify and save driver consent</button>
      <button id="refresh" disabled>Refresh budget status</button>
      <button id="revoke" disabled>Revoke this budget consent</button>
      <p id="status" role="status" aria-live="polite">No budget selected. Only open sessions can receive a new invitation.</p>
      <p>Keep the invitation JSON if you leave this card. To resume, paste its signed receipt and select Verify; the saved invitation is checked in HA.</p>
    </div></ha-card><style>
      .body{padding:0 20px 20px;color:var(--primary-text-color)}p{line-height:1.5;overflow-wrap:anywhere}
      label{display:block;margin:14px 0;font-size:14px}input,textarea{box-sizing:border-box;width:100%;margin-top:5px;padding:10px;border:1px solid var(--divider-color);border-radius:4px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit}
      textarea{font-family:monospace;font-size:12px;resize:vertical}button{min-height:44px;padding:8px 14px;margin:4px 4px 4px 0;border:1px solid var(--primary-color);background:transparent;color:var(--primary-color);border-radius:4px;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}a{color:var(--primary-color)}:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    </style>`;
    this.$("create").onclick = () => this.perform("create_session_budget", {
      proxy_config_entry_id: config.proxy_config_entry_id, session_id: this.session?.session_id,
      conversion_rate_entity: config.rate_entity, max_total_sats: this.number("total"),
      max_fee_sats: this.number("fee"), valid_minutes: this.number("minutes")});
    this.$("accept").onclick = () => {
      try { const receipt = JSON.parse(this.$("receipt").value);
        return this.perform("accept_session_budget", {budget_id:receipt.budget_id, receipt});
      } catch (_) { this.$("status").textContent = "Paste a valid receipt JSON record."; }
    };
    this.$("refresh").onclick = () => this.perform("session_budget_status", {budget_id:this.budget.terms.budget_id});
    this.$("revoke").onclick = () => {
      if (confirm("Revoke this session budget consent in HA? This does not reverse any payment."))
        this.perform("revoke_session_budget", {budget_id:this.budget.terms.budget_id});
    };
  }
  $(id) { return this.shadowRoot.getElementById(id); }
  number(id) { const v = this.$(id).value; return v.trim() ? Number(v) : null; }
  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const state = hass.states[this.config.proxy_entity];
    this.session = state?.attributes.latest_session;
    this.$("session").textContent = this.session ? `Session: ${this.session.ocpp_transaction_id} (${this.session.ended_at ? "closed" : "open"})` : "Waiting for a recorded session.";
    const admin = !!hass.user?.is_admin;
    this.$("create").disabled = this.busy || !admin || !this.session || !!this.session.ended_at ||
      !state || ["unknown","unavailable"].includes(state.state);
    this.$("accept").disabled = this.busy || !admin;
    this.$("refresh").disabled = this.busy || !admin || !this.budget;
    this.$("revoke").disabled = this.busy || !admin || !this.budget || this.budget.state === "revoked";
  }
  async perform(service, data) {
    if (this.busy || !this._hass?.user?.is_admin) return;
    this.busy = true; this.hass = this._hass;
    this.$("status").textContent = "Waiting for Home Assistant…";
    try {
      const result = await this._hass.callWS({type:"call_service",domain:"bsv_settlement",
        service,service_data:{config_entry_id:this.config.config_entry_id,...data},return_response:true});
      this.budget = result.response;
      this.$("invitation").value = JSON.stringify(this.budget.invitation, null, 2);
      this.$("status").textContent = `${this.budget.state}. No spending authority or charger control is enabled.`;
    } catch (e) { this.$("status").textContent = e.message || "Budget operation failed."; }
    finally {this.busy = false; this.hass = this._hass;}
  }
  getCardSize() { return 12; }
}
if (!customElements.get("bsv-budget-card")) customElements.define("bsv-budget-card", BSVBudgetCard);

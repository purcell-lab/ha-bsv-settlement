// Shared visual actions, not shared authority. Each mode supplies its existing guards.
export const driverActions=[
  ["connect","Connect wallet"],["pair","Connect BSV Browser"],
  ["approve","Authorise EV charging budget"],["refresh","Refresh status"],
  ["sync","Sync credit receipts"],["signout","Sign out"],
];
export function mountDriverToolbar(mode){
  const bar=document.createElement("section");bar.className="driver-toolbar";
  bar.setAttribute("aria-label","Driver navigation and actions");
  bar.innerHTML=`<nav class="driver-modes" aria-label="Driver views">
    <a id="driver-mode-portal" href="/bsv_settlement/driver/index.html">Driver portal</a>
    <span id="driver-mode-session">Your charging session</span></nav>
    <div class="driver-action-grid">${driverActions.map(([id,label])=>
      `<button id="driver-action-${id}" class="secondary" disabled>${label}</button>`).join("")}</div>
    <p id="driver-mode-help" class="small">${mode==="portal"?
      "Wallet history and receipt sync. To authorise spending, open an invitation supplied by your operator.":
      "This invitation and its settlement only. Reconnecting can resume an already-authorised collection. Use Driver portal for your full wallet-linked history."}</p>
    <details class="driver-action-help"><summary>Why are some actions unavailable?</summary><ul></ul></details>`;
  document.querySelector(".intro").after(bar);
  const portal=bar.querySelector("#driver-mode-portal"),session=bar.querySelector("#driver-mode-session");
  if(mode==="portal"){
    portal.setAttribute("aria-current","page");portal.removeAttribute("href");
    session.setAttribute("aria-disabled","true");session.title="Open a private session or registration invitation from your operator.";
  }else session.setAttribute("aria-current","page");
  return {update(states){
    const help=bar.querySelector("ul");help.replaceChildren();
    for(const [id,label] of driverActions){
      const state=states[id]||{},target=state.target&&document.getElementById(state.target);
      if(target)target.classList.add("toolbar-managed");
      const enabled=state.enabled!==undefined?!!state.enabled:!!target&&!target.disabled&&!target.hidden;
      const button=bar.querySelector(`#driver-action-${id}`);
      button.disabled=!enabled;button.className=enabled&&state.primary?"":"secondary";
      button.title=enabled?(state.hint||label):(state.reason||"Not available in this mode.");
      button.onclick=()=>{
        if(button.disabled)return;
        if(target){if(!target.disabled&&!target.hidden)target.click();}
        else state.run?.();
      };
      if(!enabled){const li=document.createElement("li");li.textContent=`${label}: ${button.title}`;help.append(li);}
    }
  }};
}

// The exact encoded value is visible and selectable, not a shortened URL.
export function qrText(holder,value,label="QR value",message=()=>{}){
  if(!holder)return;
  let panel=holder.nextElementSibling;
  if(!panel?.classList.contains("qr-value")){
    panel=document.createElement("div");panel.className="qr-value";
    const wrapper=document.createElement("label");wrapper.append(document.createTextNode(label));
    const field=document.createElement("textarea");field.readOnly=true;field.rows=3;
    field.spellcheck=false;field.setAttribute("aria-label",label);wrapper.append(field);
    const copy=document.createElement("button");copy.className="secondary";copy.textContent="Copy";
    copy.setAttribute("aria-label",`Copy ${label.toLowerCase()}`);
    panel.append(wrapper,copy);holder.after(panel);
    copy.onclick=async()=>{
      if(!field.value)return;
      let timer;
      try{await Promise.race([navigator.clipboard.writeText(field.value),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Clipboard unavailable")),2000);})]);
        message(`${label} copied. Keep private links and pairing codes private.`);}
      catch{field.focus();field.select();message("Text selected. Use your device's Copy command.");}
      finally{clearTimeout(timer);}
    };
  }
  panel.hidden=!value;panel.querySelector("textarea").value=value||"";
  panel.querySelector("button").disabled=!value;
}

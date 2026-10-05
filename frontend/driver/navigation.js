import {nextAction,renderSteps} from "./journey.js";
// Shared visual actions, not shared authority. Each mode supplies its existing guards.
export const driverActions=[
  ["connect","Connect wallet"],["pair","Connect BSV Browser"],
  ["approve","Authorise EV charging budget"],["refresh","Refresh status"],
  ["sync","Add credit to wallet"],["signout","Sign out"],
  ["save","Save existing approval"],["report","Check existing payment"],
  ["register","Authorise EV charging budget"],["monthly","Authorise monthly charging"],
];
export function mountDriverToolbar(mode){
  const bar=document.createElement("div");bar.className="driver-toolbar";
  bar.setAttribute("aria-label","Driver navigation and actions");
  bar.innerHTML=`<ol class="journey-steps" aria-label="Charging journey">${renderSteps(0)}</ol>
    <p id="driver-journey-announcement" class="visually-hidden" role="status" aria-live="polite"></p>
    <div class="journey-primary"><p id="driver-next-hint" class="small"></p><div id="driver-next-action"></div>
      <p id="driver-action-unavailable" class="small" role="status" hidden></p></div>
    <details class="journey-more"><summary>Wallet options and help</summary>
      <p class="small">Use a wallet on another device, refresh your status, or get back to your history.</p>
      <div id="driver-secondary-actions" class="driver-action-grid">${driverActions.map(([id,label])=>
      `<button id="driver-action-${id}" class="secondary" hidden disabled>${label}</button>`).join("")}</div>
      <nav class="driver-modes" aria-label="Driver views">
      <a id="driver-mode-portal" href="/bsv_settlement/driver/index.html">My charging history</a>
      <span id="driver-mode-session">Current session</span></nav>
    </details>`;
  document.querySelector(".intro").after(bar);
  const portal=bar.querySelector("#driver-mode-portal"),session=bar.querySelector("#driver-mode-session");
  if(mode==="portal"){
    portal.setAttribute("aria-current","page");portal.removeAttribute("href");
    session.setAttribute("aria-disabled","true");session.title="Open a private session or registration invitation from your operator.";
  }else session.setAttribute("aria-current","page");
  return {update(states,journey={}){
    const resolved={};
    for(const [id,label] of driverActions){
      const state=states[id]||{},target=state.target&&document.getElementById(state.target);
      if(target)target.classList.add("toolbar-managed");
      const enabled=state.enabled!==undefined?!!state.enabled:!!target&&!target.disabled&&!target.hidden;
      resolved[id]={...state,enabled};
      const button=document.getElementById(`driver-action-${id}`);
      button.disabled=!enabled;button.hidden=!enabled;button.className="secondary";
      button.textContent=state.label||label;
      button.title=enabled?(state.hint||label):(state.reason||"Not available in this mode.");
      button.onclick=()=>{
        if(button.disabled)return;
        if(target){if(!target.disabled&&!target.hidden)target.click();}
        else state.run?.();
      };
    }
    const primary=nextAction(resolved),slot=document.getElementById("driver-next-action"),grid=document.getElementById("driver-secondary-actions");
    for(const [id] of driverActions){
      const button=document.getElementById(`driver-action-${id}`),parent=id===primary?slot:grid;
      if(button.parentElement!==parent)parent.append(button);
      if(id===primary)button.className="wide";
    }
    const note=document.getElementById("driver-action-unavailable");
    note.hidden=!!primary||!journey.unavailable;note.textContent=journey.unavailable||"";
    document.getElementById("driver-next-hint").textContent=journey.hint||"";
    const step=journey.step??0;
    const announcement=document.getElementById("driver-journey-announcement");
    if(announcement.textContent!==(journey.title||""))announcement.textContent=journey.title||"";
    if(bar.dataset.step!==String(step)){
      bar.querySelector(".journey-steps").innerHTML=renderSteps(step);bar.dataset.step=String(step);
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

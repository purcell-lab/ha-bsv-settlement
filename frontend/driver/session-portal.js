import {signPortalLogin,sessionSummary,transactionStatus} from "./portal-model.js";
import {authorisationRows} from "./portal-setup.js";
// Private links do not grant access to other sessions. History still needs
// an independent identity challenge and a same-origin, owner-scoped cookie.
export function sessionPortal(){
  const card=document.createElement("section");card.id="session-wallet-summary";
  card.innerHTML="<h2>Wallet status</h2><ul class='authorisation-list'></ul><p class='small'>Signed budget, wallet payment permission and receipt acceptance are separate. Wallet prompts may still appear.</p>";
  const history=document.createElement("details");history.id="session-history-box";
  history.className="portal-disclosure";
  history.innerHTML="<summary>History</summary><p role='status'>Sign in to load your private history.</p><div></div>";
  const api=async(action,data={})=>{
    const r=await fetch("/api/bsv_settlement/portal",{method:"POST",credentials:"same-origin",cache:"no-store",
      referrerPolicy:"no-referrer",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...data}),
      signal:AbortSignal.timeout(20000)});
    if(!r.ok)throw Error("Private history unavailable");return r.json();
  };
  const node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
  let identity=null,approvals=[],expires=0,networkVerified=false;
  document.addEventListener("visibilitychange",()=>{if(document.hidden)networkVerified=false;});
  async function load(){
    const result=await api("sessions");
    if(result.identity!==identity)throw Error("History identity changed. Sign in again.");
    expires=Date.now()+result.expires_in*1000;approvals=result.authorisations||[];
    history.querySelector("p").textContent=`Latest ${result.sessions.length} of ${result.total} sessions. Open a row for details.`;
    const holder=history.querySelector("div");holder.replaceChildren();
    for(const session of result.sessions){
      const summary=sessionSummary(session),row=node("details","");
      row.append(node("summary",`${new Date(session.opened_at).toLocaleDateString()} · ${summary.payment} · ${summary.status}`),
        node("p",`Energy Imported to EV: ${session.import_kwh??"Unavailable"} kWh. Energy Imported from EV: ${session.export_kwh??"Unavailable"} kWh.`),
        node("p",session.transactions.map(transactionStatus).join(". ")));
      holder.append(row);
    }
  }
  history.addEventListener("toggle",()=>{
    if(history.open&&identity)void load().catch(()=>{
      history.querySelector("div").replaceChildren();history.querySelector("p").textContent="History could not be refreshed. Sign in again.";
    });
  });
  return {card,history,get signedIn(){return !!identity&&Date.now()<expires;},
    async signIn(wallet,expected){
      networkVerified=false;
      if((await wallet.getNetwork()).network!=="mainnet")throw Error("Use a BSV mainnet wallet.");
      networkVerified=true;
      const proof=await signPortalLogin(wallet,await api("challenge"),location.origin);
      if(proof.identity!==expected)throw Error("Use the wallet for this charging approval.");
      const result=await api("login",proof);
      if(result.identity!==expected)throw Error("History identity did not match.");
      identity=expected;await load();
    },
    update({connected,paused}){
      if(identity&&Date.now()>=expires){
        identity=null;approvals=[];history.querySelector("div").replaceChildren();
        history.querySelector("p").textContent="Private history access expired. Sign in again.";
      }
      const holder=card.querySelector("ul");holder.replaceChildren();
      for(const row of authorisationRows({identity,connected:networkVerified&&!!identity&&!document.hidden,paused,approvals})){
        const li=node("li",""),mark=node("span",row.ok?"✓":"–"),text=node("div","");
        mark.className="authorisation-mark";mark.setAttribute("aria-label",row.ok?"Verified":"Not verified");
        li.dataset.verified=String(row.ok);text.append(node("strong",row.label),node("span",row.text));
        li.append(mark,text);holder.append(li);
      }
    }
  };
}

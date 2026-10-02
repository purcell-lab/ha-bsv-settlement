import qrcode from "qrcode-generator";

export function awaitingApproval(budget, now=Date.now()){
  return !!budget && budget.state==="awaiting_driver_consent" && !budget.receipt &&
    Number.isFinite(Date.parse(budget.terms?.expires_at)) &&
    Date.parse(budget.terms.expires_at)>now &&
    !budget.collection?.txid && !budget.automatic_credit?.txid;
}

export function approvalUrl(fragment,budgetId,origin){
  if(typeof fragment!=="string"||!fragment.startsWith("#"))return null;
  const p=new URLSearchParams(fragment.slice(1));
  if(p.size!==2||p.get("budget")!==budgetId||
    !/^[0-9a-f-]{36}$/i.test(budgetId||"")||!/^[A-Za-z0-9_-]{43}$/.test(p.get("token")||""))return null;
  const url=new URL("/bsv_settlement/driver/index.html",origin);
  url.hash=p.toString();return url.href;
}

export function drawApprovalQR(holder,url){
  const qr=qrcode(0,"M");qr.addData(url,"Byte");qr.make();
  holder.innerHTML=qr.createSvgTag({cellSize:4,margin:16,scalable:true});
  const svg=holder.querySelector("svg");
  svg.setAttribute("role","img");svg.setAttribute("aria-label","Scan to approve this charging session in BSV Browser");
}

export function pendingForSession(session,health,now=Date.now()){
  if(!session||session.ended_at)return null;
  return (health.driver_approvals||[]).find(a=>
    a.session_id===session.session_id && a.state==="awaiting_driver_consent" &&
    !a.approved && Date.parse(a.expires_at)>now) || null;
}

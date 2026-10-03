import {approvalUrl} from "../approval-qr.js";

// Capabilities stay in the fragment, never a query, remote QR service or log.
export function privateSessionUrl(value,origin){
  try{
    const u=new URL(value);
    if(u.origin!==origin||u.username||u.password||u.search||
       u.pathname!=="/bsv_settlement/driver/index.html")return null;
    const p=new URLSearchParams(u.hash.slice(1));
    return approvalUrl(u.hash,p.get("budget"),origin);
  }catch{return null;}
}

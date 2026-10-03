
// Capabilities stay in the fragment, never a query, remote QR service or log.
export function privateSessionUrl(value,origin){
  try{
    const u=new URL(value);
    if(u.origin!==origin||u.username||u.password||u.search||
       u.pathname!=="/bsv_settlement/driver/index.html")return null;
    const p=new URLSearchParams(u.hash.slice(1));
    if(p.size!==2||!/^[0-9a-f-]{36}$/i.test(p.get("budget")||"")||
       !/^[A-Za-z0-9_-]{43}$/.test(p.get("token")||""))return null;
    return u.href;
  }catch{return null;}
}

import {receiptReported} from "./credit.js";
/** Scan owner-filtered history, not only the 25 records visible in the table. */
export async function pendingCreditJobs(fetchPage,identity,assertActive=()=>{}){
  const jobs=[],seen=new Set();
  let offset=0;
  for(;;){
    assertActive();
    const page=await fetchPage(offset);
    assertActive();
    if(page.identity!==identity||!Array.isArray(page.sessions)||
      !Number.isSafeInteger(page.total)||page.total<0||page.total>100000||
      page.sessions.length>25)throw Error("Private credit history could not be verified.");
    for(const session of page.sessions)for(const row of session.transactions||[]){
      if(row.direction!=="operator_to_driver"||row.state!=="provider_confirmed"||receiptReported(row))continue;
      if(typeof row.id!=="string"||!/^[0-9a-f]{64}$/.test(row.txid||""))
        throw Error("A confirmed credit is missing its payment reference. Contact the operator.");
      const key=`${row.id}:${row.txid}`;
      if(!seen.has(key)){seen.add(key);jobs.push({key,row,session});}
    }
    offset+=page.sessions.length;
    if(offset>=page.total)return jobs;
    if(!page.sessions.length)throw Error("Credit history pagination stopped before completion.");
  }
}

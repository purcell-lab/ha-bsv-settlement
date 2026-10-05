// Isolated diagnostic: never import the portal, receipts or collection workers.
export const limit = 30000;
export const declaration = {schemaVersion:1,groupPermissions:{
  description:"EV charging budget",
  spendingAuthorization:{amount:limit,description:"Monthly EV charging payments and network fees"}
}};
const keys = value => Object.keys(value||{}).sort().join(",");
function exactDeclaration(value) {
  return value?.schemaVersion===1 &&
    keys(value)==="groupPermissions,schemaVersion" &&
    keys(value.groupPermissions)==="description,spendingAuthorization" &&
    value.groupPermissions.description===declaration.groupPermissions.description &&
    keys(value.groupPermissions.spendingAuthorization)==="amount,description" &&
    value.groupPermissions.spendingAuthorization.amount===limit &&
    value.groupPermissions.spendingAuthorization.description===declaration.groupPermissions.spendingAuthorization.description;
}
export async function preflight(fetcher, origin) {
  const url=new URL(origin);
  if(url.protocol!=="https:"||url.origin!==origin||url.port)
    throw Error("Open this test on the approved HTTPS charging origin.");
  const get=async path=>{
    const r=await fetcher(path,{cache:"no-store",credentials:"omit",redirect:"error",
      signal:AbortSignal.timeout(10000)});
    if(!r.ok)throw Error("The operator test configuration could not be checked.");
    return r.json();
  };
  const status=await get("/api/bsv_settlement/grouped-test");
  if(status.enabled!==true||status.origin!==origin||status.monthly_limit_sats!==limit)
    throw Error("This test is not enabled for this origin.");
  const manifest=await get("/manifest.json");
  if(manifest.babbage!=null||!exactDeclaration(manifest.metanet))
    throw Error("The live manifest does not match the reviewed 30,000 sat request.");
  return {origin,monthly_limit_sats:limit,spending_permission:"not_verified"};
}
async function bounded(operation,ms) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(operation),new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(Error("Wallet response timed out. Check the wallet before reopening this test.")),ms);
    })]);
  } finally {clearTimeout(timer);}
}
export class GroupedTest {
  constructor({fetcher,origin,wallet,timeoutMs=90000}) {
    Object.assign(this,{fetcher,origin,wallet,timeoutMs});
    this.attempted=false;
  }
  async run() {
    if(this.attempted)throw Error("This page already attempted the test. Review the wallet before reloading.");
    this.attempted=true;
    await preflight(this.fetcher,this.origin);
    const w=this.wallet;
    if(typeof w?.getNetwork!=="function"||typeof w?.waitForAuthentication!=="function")
      throw Error("Open this page inside BSV Browser in wallet-enabled mode, then reload.");
    const network=await bounded(()=>w.getNetwork({}),5000);
    if(network?.network!=="mainnet")throw Error("Mainnet wallet required. No authorisation request was made.");
    // Explicit click only, even if already authenticated. Authentication success
    // cannot prove the amount was granted; lower prior grants may be retained.
    const result=await bounded(()=>w.waitForAuthentication({}),this.timeoutMs);
    if(result?.authenticated!==true)
      throw Error("Wallet authentication was not confirmed. Check the native wallet.");
    return {authentication:"reported_authenticated",spending_permission:"not_verified"};
  }
}

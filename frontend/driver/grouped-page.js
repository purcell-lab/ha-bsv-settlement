import {preflight,GroupedTest} from "./grouped-test.js";
const $=id=>document.getElementById(id);
$("origin").textContent=location.origin;
$("theme").onclick=()=>{
  const dark=document.documentElement.dataset.theme!=="dark";
  document.documentElement.dataset.theme=dark?"dark":"light";
  $("theme").textContent=dark?"Light":"Dark";
};
let test;
async function check() {
  try {
    if(window.top!==window.self)throw Error("Open this test as a full page, not inside another application.");
    await preflight(fetch,location.origin);
    if(typeof window.CWI?.waitForAuthentication!=="function")
      throw Error("Open this same link inside BSV Browser in wallet-enabled mode, then reload.");
    test=new GroupedTest({fetcher:fetch,origin:location.origin,wallet:window.CWI});
    $("status").textContent="Ready to ask your wallet. No spending permission has been verified.";
    $("ask").disabled=false;
  } catch {
    $("status").textContent="Test unavailable. Open this link inside BSV Browser in wallet-enabled mode. The operator must enable the reviewed manifest first.";
  }
}
$("ask").onclick=async()=>{
  if(!test||test.attempted)return;
  $("ask").disabled=true;
  $("status").textContent="Review the native wallet prompt. This may take a moment; do not press again.";
  try {
    await test.run();
    $("status").textContent="Wallet authentication returned. The 30,000 sat allowance is not yet verified.";
    $("result").textContent="Check BSV Browser's permission screen for the actual monthly limit. Tell the operator the amount and app version. No charging payment was requested.";
  } catch {
    $("status").textContent="Test stopped or the wallet did not reply. Check the wallet before reloading. No automatic retry will occur.";
    $("result").textContent="No allowance is claimed as approved. Declining a prompt is safe; inspect the wallet for any permission outcome.";
  }
};
check();

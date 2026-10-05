// Offline QA for the station-first portal preview. Needs Playwright with Chromium:
//   node build-portal-preview.mjs && NODE_PATH=$(npm root -g) node qa-station-preview.mjs ../../preview/portal <out-dir>
// Fictional wallet and sessions only; no live wallet, Home Assistant or chain.
import {chromium} from "playwright";
import http from "node:http";import fs from "node:fs";import path from "node:path";
const root=process.argv[2],out=process.argv[3];
const server=http.createServer((req,res)=>{
  const file=path.join(root,decodeURIComponent(new URL(req.url,"http://x").pathname).replace(/^\//,"")||"index.html");
  if(!fs.existsSync(file)||fs.statSync(file).isDirectory()){res.writeHead(404);return res.end();}
  res.writeHead(200,{"Content-Type":{".js":"text/javascript",".css":"text/css",".html":"text/html",".woff2":"font/woff2"}[path.extname(file)]||"text/plain"});
  fs.createReadStream(file).pipe(res);
}).listen(0);
const base=`http://localhost:${server.address().port}/index.html`;
const browser=await chromium.launch();
const results=[];const check=(name,ok,detail="")=>{results.push({name,ok,detail});};
async function page(query,{width=1365,height=900,scheme="light"}={}){
  const ctx=await browser.newContext({viewport:{width,height},colorScheme:scheme});
  const p=await ctx.newPage();const errors=[];p.on("pageerror",e=>errors.push(e.message));
  await p.goto(`${base}?${query}`);await p.waitForTimeout(700);
  return {p,ctx,errors};
}
const overflow=p=>p.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
// 1. Public station: rates, terms, disabled authorise when off.
for(const [q,name] of [["monthly=off","off"],["monthly=new","new"]]){
  const {p,ctx,errors}=await page(q);
  check(`${name}: station tab selected by default`,await p.getAttribute("#tab-station","aria-selected")==="true");
  check(`${name}: buy rate shown`,/0\.2850|\$0\.285/.test(await p.textContent("#signin-buy")),await p.textContent("#signin-buy"));
  check(`${name}: negative sell rate shown with sign`,/-|−/.test(await p.textContent("#signin-sell")),await p.textContent("#signin-sell"));
  check(`${name}: terms list names 30,000 sat incl fees`,/30,000 sat each calendar month, including network fees/.test(await p.textContent("#monthly-terms")));
  check(`${name}: authorise ${name==="off"?"unavailable":"is the primary action"}`,name==="off"?await p.isHidden("#driver-action-monthly"):(await p.getAttribute("#driver-action-monthly","class"))==="wide");
  check(`${name}: station QR payload text and copy`,(await p.inputValue("#station-qr-details textarea")).endsWith("/bsv_settlement/driver/index.html")&&
    await p.locator("#station-qr-details .qr-value button").count()===1);
  check(`${name}: no private history visible`,await p.isHidden("#portal-history"));
  check(`${name}: no page errors`,!errors.length,errors.join("; "));
  await p.screenshot({path:`${out}/station-${name}-desktop-light.png`,fullPage:true});await ctx.close();
}
// 2. One action: fresh driver authorises monthly charging.
{
  const {p,ctx,errors}=await page("monthly=new&active");
  await p.click("#driver-action-monthly");
  await p.waitForSelector("#monthly-confirm:not([hidden])",{timeout:8000});
  check("authorise: exact terms confirmation shown before wallet",/30,000 sat including fees/.test(await p.textContent("#monthly-confirm-terms")));
  check("authorise: focus moved to confirmation",await p.evaluate(()=>document.activeElement.id)==="monthly-confirm-title");
  await p.screenshot({path:`${out}/authorise-confirm-desktop-light.png`,fullPage:true});
  const before=await p.evaluate(()=>window.portalPreviewCalls.filter(c=>c==="monthly:monthly_accept").length);
  await p.click("#monthly-confirm-yes");
  await p.waitForFunction(()=>document.getElementById("tab-charging").getAttribute("aria-selected")==="true",null,{timeout:8000});
  await p.waitForTimeout(500);
  const calls=await p.evaluate(()=>window.portalPreviewCalls);
  check("authorise: accepted once",calls.filter(c=>c==="monthly:monthly_accept").length===before+1,calls.join(","));
  check("authorise: login then challenge then accept order",calls.indexOf("login")<calls.indexOf("monthly:monthly_challenge")&&
    calls.indexOf("monthly:monthly_challenge")<calls.indexOf("monthly:monthly_accept"));
  check("authorise: readiness ready",/ready/i.test(await p.textContent("#monthly-readiness-title")),await p.textContent("#monthly-readiness-title"));
  check("charging: OCPP status",await p.textContent("#charging-ocpp")==="Charging");
  check("charging: provisional sat matches #78 projection",/6 sat provisional/.test(await p.textContent("#charging-net")),await p.textContent("#charging-net"));
  check("charging: export kWh",(await p.textContent("#charging-export"))==="0.530 kWh");
  check("charging: zero import shows 0.000 not unavailable",(await p.textContent("#charging-import"))==="0.000 kWh");
  check("charging: average sell from operator helper",/\$0\.1132\/kWh/.test(await p.textContent("#charging-avg-sell")),await p.textContent("#charging-avg-sell"));
  check("payments: no wallet spend attempted",(await p.evaluate(()=>window.portalPreviewCalls)).every(c=>!/create|sign_action|commit|reserve/.test(c)));
  await p.screenshot({path:`${out}/charging-ready-desktop-light.png`,fullPage:true});
  // Wallet drawer: keyboard open, allowance, Escape returns focus.
  await p.focus("#wallet-open");await p.keyboard.press("Enter");
  check("drawer: opens and focuses its heading",await p.evaluate(()=>document.activeElement.id)==="wallet-drawer-title");
  check("drawer: allowance rows",/Remaining\s*28,505 sat/.test(await p.textContent("#allowance-rows")),await p.textContent("#allowance-rows"));
  check("drawer: permission verified",(await p.textContent("#perm-grant"))==="Verified for this month");
  await p.screenshot({path:`${out}/wallet-drawer-desktop-light.png`,fullPage:true});
  await p.click("#monthly-cancel");
  check("cancel: confirmation before signing",await p.isVisible("#monthly-cancel-confirm"));
  await p.click("#monthly-cancel-yes");await p.waitForTimeout(800);
  check("cancel: authority shown cancelled, revocation not verified",/revocation not verified/.test(await p.textContent("#perm-authority")),await p.textContent("#perm-authority"));
  check("cancel: status explains wallet permission separately",/not confirmed revoked/.test(await p.textContent("#portal-status")),await p.textContent("#portal-status"));
  await p.keyboard.press("Escape");
  check("drawer: Escape closes and returns focus",await p.isHidden("#wallet-drawer")&&await p.evaluate(()=>document.activeElement.id)==="wallet-open");
  check("authorise flow: no page errors",!errors.length,errors.join("; "));
  await ctx.close();
}
// 3. Honest readiness when the wallet's monthly permission is not verified.
{
  const {p,ctx}=await page("monthly=unverified");
  await p.click("#portal-login");await p.waitForTimeout(1200);
  check("unverified: not ready",!/^Monthly charging ready/.test(await p.textContent("#monthly-readiness-title")),await p.textContent("#monthly-readiness-title"));
  check("unverified: explains wallet permission",/not verified/.test(await p.textContent("#monthly-readiness-items")));
  check("unverified: authorise not offered when authority exists",await p.isHidden("#driver-action-monthly"));
  await p.screenshot({path:`${out}/charging-unverified-desktop-dark.png`,fullPage:true});await ctx.close();
}
// 4. Keyboard tab navigation.
{
  const {p,ctx}=await page("monthly=new");
  await p.focus("#tab-station");
  const seq=[];
  for(const key of ["ArrowRight","ArrowRight","ArrowRight","End","Home","ArrowLeft"]){
    await p.keyboard.press(key);seq.push(await p.evaluate(()=>document.activeElement.id));
  }
  check("keyboard: arrows/Home/End cycle tabs with focus",seq.join(",")==="tab-charging,tab-history,tab-station,tab-history,tab-station,tab-history",seq.join(","));
  check("keyboard: only selected tab in tab order",(await p.$$eval("[role=tab]",t=>t.filter(b=>b.tabIndex===0).length))===1);
  check("keyboard: signed-out history explains sign-in",await p.isVisible("#history-signed-out"));
  await ctx.close();
}
// 5. Mobile and desktop, light and dark: no horizontal overflow.
for(const [width,label] of [[390,"mobile"],[1365,"desktop"]])for(const scheme of ["light","dark"]){
  const {p,ctx}=await page("monthly=active&active",{width,height:844,scheme});
  await p.click("#portal-login");await p.waitForTimeout(1200);
  check(`${label} ${scheme}: no horizontal overflow (charging)`,!(await overflow(p)));
  await p.screenshot({path:`${out}/charging-active-${label}-${scheme}.png`,fullPage:true});
  await p.click("#tab-history");check(`${label} ${scheme}: no horizontal overflow (history)`,!(await overflow(p)));
  await p.screenshot({path:`${out}/history-${label}-${scheme}.png`,fullPage:false});
  await p.click("#wallet-open");check(`${label} ${scheme}: no horizontal overflow (wallet)`,!(await overflow(p)));
  await p.click("#tab-station");check(`${label} ${scheme}: no horizontal overflow (station)`,!(await overflow(p)));
  if(label==="mobile")await p.screenshot({path:`${out}/station-wallet-${label}-${scheme}.png`,fullPage:true});
  await ctx.close();
}
await browser.close();server.close();
fs.writeFileSync(`${out}/results.json`,JSON.stringify(results,null,1));
for(const r of results)console.log(`${r.ok?"PASS":"FAIL"}  ${r.name}${r.ok?"":"  ["+r.detail+"]"}`);
console.log(`${results.filter(r=>r.ok).length}/${results.length} passed`);

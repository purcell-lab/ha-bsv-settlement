// Optional offline integration QA: npm install playwright in your QA environment.
// First build frontend/driver/build-receipt-sync-preview.mjs and serve its output.
// No remote host, private driver capability or real wallet is permitted here.
import {chromium} from "playwright";
import assert from "node:assert/strict";
const base=process.env.RECEIPT_PREVIEW_URL||"http://127.0.0.1:3077";
if(new URL(base).hostname!=="127.0.0.1")throw Error("QA requires the local fictional fixture");
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
const errors=[];
page.on("pageerror",e=>errors.push(e.message));
const calls=()=>page.evaluate(()=>structuredClone(window.previewCalls));
const synced=()=>page.waitForFunction(()=>document.querySelector("#receipt-sync-status").textContent.startsWith("Confirmed credits synced"));
async function open(scenario){
  await page.goto(`${base}/?scenario=${scenario}`);
  await page.waitForFunction(()=>window.previewCalls?.actions.includes("collection_status"));
}
try{
  for(const mode of ["auto","direct","ready"]){
    await open(mode);await synced();
    assert.equal((await calls()).imports,mode==="direct"?1:2);
    assert.deepEqual((await calls()).forbidden,[]);
  }
  for(const mode of ["plain","accepted","unconfirmed"]){
    await open(mode);
    assert.equal((await calls()).identity,0);
    assert.equal((await calls()).imports,0);
    if(mode==="plain"){await page.locator("#receive-ongoing").click();await synced();assert.equal((await calls()).imports,2);}
  }
  await open("wrong");
  await page.waitForFunction(()=>document.querySelector("#receipt-sync-status").textContent.startsWith("Receipt sync paused"));
  assert.equal((await calls()).imports,0);
  await page.clock.install();
  await page.clock.fastForward(9000);
  assert.equal((await calls()).identity,1,"Do not repeat declined/wrong-wallet prompts on polling");
  for(const mode of ["declined","report"]){
    await open(mode);
    await page.waitForFunction(()=>/paused|reporting is pending/.test(document.querySelector("#receipt-sync-status").textContent));
    assert.equal((await calls()).imports,1);
    await page.locator("#receive-ongoing").click();await synced();
    assert.equal((await calls()).imports,mode==="report"?2:3);
    assert.deepEqual((await calls()).forbidden,[]);
  }
  await open("late");assert.equal((await calls()).identity,0);
  await page.evaluate(()=>window.injectPreviewWallet());
  await page.clock.fastForward(3000);await synced();
  assert.equal((await calls()).imports,2);
  for(const width of [1280,375]){
    await page.setViewportSize({width,height:900});await open("plain");
    await page.locator("#receive-ongoing").scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await page.locator("#receive-ongoing").isEnabled(),true);
    await page.locator("#receive-ongoing").click();await synced();
  }
  assert.deepEqual(errors,[]);
  console.log("Receipt-sync browser QA passed: 10 scenarios, desktop/mobile, no payment operations.");
}finally{await browser.close();}

import test from "node:test";
import assert from "node:assert/strict";
import {PrivateKey,ProtoWallet} from "@bsv/sdk";
import {canonical} from "./model.js";
import {signPortalLogin,loginProtocol,loginScope,averageNet,transactionStatus} from "./portal-model.js";
const origin="https://charging.example.com";
function challenge(patch={}){
  const payload={action:"sign_in_driver_portal",version:1,origin,scope:loginScope,
    nonce:"a".repeat(43),browser_binding:"b".repeat(64),issued_at:Math.floor(Date.now()/1000),
    expires_at:Math.floor(Date.now()/1000)+120,...patch};
  return {payload:canonical(payload),protocolID:loginProtocol,keyID:payload.nonce};
}
test("wallet login signs a separate protocol, never a spending action",async()=>{
  const wallet=new ProtoWallet(new PrivateKey(19)),sign=wallet.createSignature.bind(wallet);
  let request;
  wallet.createSignature=async r=>{request=r;return sign(r);};
  wallet.createAction=()=>assert.fail("No payment");
  const proof=await signPortalLogin(wallet,challenge(),origin);
  assert.equal(proof.identity,(await wallet.getPublicKey({identityKey:true})).publicKey);
  assert.deepEqual(request.protocolID,loginProtocol);
  assert.match(request.description,/No spending approval or payment/);
});
test("login rejects wrong origin, expiry, scope and binding before wallet use",async()=>{
  const wallet={getPublicKey:()=>assert.fail("Do not contact wallet")};
  for(const patch of [{origin:"https://evil.example"},{expires_at:1},{issued_at:99999999999},
    {scope:"authorise_spending"},{nonce:"short"},{browser_binding:"bad"},{version:2},{amount_sats:1000}]){
    await assert.rejects(()=>signPortalLogin(wallet,challenge(patch),origin),/Invalid/);
  }
});
test("missing energy is unavailable, never a fabricated zero or average",()=>{
  assert.equal(averageNet({import_kwh:2,export_kwh:8,net_amount_aud:-2}),-.2);
  for(const value of [null,undefined,"",NaN,-1])
    assert.equal(averageNet({import_kwh:value,export_kwh:8,net_amount_aud:-2}),null);
  assert.equal(averageNet({import_kwh:0,export_kwh:0,net_amount_aud:0}),null);
});
test("provider confirmation and wallet acceptance remain separate",()=>{
  const row={direction:"operator_to_driver",state:"provider_confirmed"};
  assert.match(transactionStatus(row),/sync needed/);
  assert.match(transactionStatus({...row,wallet_receipt_status:"wallet_reported_accepted"}),/acceptance recorded/);
  assert.match(transactionStatus({...row,state:"provider_unconfirmed"}),/Awaiting block/);
});

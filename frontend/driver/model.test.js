import test from "node:test";
import assert from "node:assert/strict";
import { PrivateKey, ProtoWallet, Signature } from "@bsv/sdk";
import { canonical, bytes, hex, parseInvitation, signConsent } from "./model.js";

const operator = PrivateKey.fromRandom();
const driver = new ProtoWallet(PrivateKey.fromRandom());
const terms = {
  version:1, budget_id:"11111111-2222-4333-8444-555555555555",session_id:"fictional-session",
  transaction_id:"fictional-proxy-id",network:"BSV mainnet",
  operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
  max_total_sats:1000,max_fee_sats:10,satoshis_per_aud:"100",
  pricing_rule:"Fictional test only",account_scope:"Entire session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),
  scope:"one_session_consent_only_no_payment_or_charger_authority"
};
function invitation(t = terms) {
  const payload = canonical(t);
  return {version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
}
test("operator signature and driver receipt verify with official SDK", async () => {
  const checked = parseInvitation(JSON.stringify(invitation()));
  const {publicKey} = await driver.getPublicKey({identityKey:true});
  const receipt = await signConsent(driver, checked, publicKey);
  assert.equal(receipt.driver_identity, publicKey);
  assert.equal(JSON.parse(receipt.payload).no_spending_authority,true);
});
test("tampering, wrong address, expired and invalid fee limits rejected", () => {
  const item=invitation();item.payload=item.payload.replace('"max_total_sats":1000','"max_total_sats":9999');
  assert.throws(()=>parseInvitation(JSON.stringify(item)));
  for (const updates of [{operator_address:PrivateKey.fromRandom().toPublicKey().toAddress()},
      {max_fee_sats:1000},{max_total_sats:-1},{expires_at:new Date(1).toISOString()}]) {
    assert.throws(()=>parseInvitation(JSON.stringify(invitation({...terms,...updates}))));
  }
});
test("changed wallet and rejected signature do not return consent", async () => {
  const checked=parseInvitation(JSON.stringify(invitation()));
  await assert.rejects(signConsent(driver,checked,operator.toPublicKey().toString()),/changed/);
  const key=(await driver.getPublicKey({identityKey:true})).publicKey;
  const deny={getPublicKey:driver.getPublicKey.bind(driver),createSignature:async()=>{throw Error("Denied");}};
  await assert.rejects(signConsent(deny,checked,key),/Denied/);
});
test("expiry during wallet prompt is rejected", async () => {
  const checked=parseInvitation(JSON.stringify(invitation({...terms,expires_at:new Date(Date.now()+50).toISOString()})));
  const key=(await driver.getPublicKey({identityKey:true})).publicKey;
  const slow={getPublicKey:driver.getPublicKey.bind(driver),createSignature:async args=>{
    await new Promise(resolve=>setTimeout(resolve,80));return driver.createSignature(args);
  }};
  await assert.rejects(signConsent(slow,checked,key),/expired/);
});

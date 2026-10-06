import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {captureNativeObservation} from "./native-acceptance.js";

const options = () => ({
  origin: "https://charging.example", targetAlias: "confirmed-debit-a",
  readOnlyApproved: true, timeoutMs: 20,
  target: {identity: "02" + "ab".repeat(32),
    budgetId: "11111111-2222-4333-8444-555555555555", txid: "cd".repeat(32)},
});
function fixture() {
  const opt = options(), calls = [];
  const wallet = {
    isAuthenticated: async () => ({authenticated: true}),
    getVersion: async () => ({version: "fixture-wallet-1"}),
    getNetwork: async () => ({network: "mainnet"}),
    getPublicKey: async args => {
      assert.equal(args.seekPermission, false);
      return {publicKey: opt.target.identity};
    },
    listActions: async args => {
      calls.push(args);
      assert.equal(args.seekPermission, false);
      assert.equal(args.includeInputs, false);
      assert.equal(args.includeOutputs, false);
      return {totalActions: 1, actions: [{
        txid: opt.target.txid, labels: ["ev-session:" + opt.target.budgetId],
        status: "nosend", isOutgoing: true, privateMetadata: "MUST_NOT_ESCAPE",
      }]};
    },
  };
  for (const name of ["waitForAuthentication", "createSignature", "createAction",
    "signAction", "abortAction", "internalizeAction", "sendWith", "revokePermission"])
    wallet[name] = () => assert.fail("Forbidden wallet mutation: " + name);
  return {opt, wallet, calls};
}
test("captures versioned, exact-target observation without claiming native acceptance", async () => {
  const {wallet, opt, calls} = fixture();
  const result = await captureNativeObservation(wallet, opt);
  assert.equal(result.reason, "observation_only");
  assert.equal(result.native_acceptance, "not_established");
  assert.equal(result.outgoing.wallet_status, "nosend");
  assert.equal(result.status_repaired, false);
  assert.equal(result.retry_authorised, false);
  assert.equal(result.automatic_collection_ready, false);
  assert.equal(result.receipt_acceptance, "not_assessed");
  assert.equal(calls.length, 1);
  assert.deepEqual(result.calls, ["isAuthenticated", "getVersion", "getNetwork",
    "isAuthenticated", "getPublicKey", "getNetwork", "listActions",
    "isAuthenticated", "getPublicKey", "getNetwork"]);
  for (const privateValue of [...Object.values(opt.target), "MUST_NOT_ESCAPE"])
    assert.equal(JSON.stringify(result).includes(privateValue), false);
});
test("missing explicit scope or malformed target makes no calls", async () => {
  const {wallet, opt, calls} = fixture();
  wallet.isAuthenticated = () => assert.fail("No calls");
  for (const patch of [{readOnlyApproved: false}, {readOnlyApproved: "true"},
    {origin: "http://charging.example"}, {origin: "https://charging.example/private"},
    {targetAlias: "https://private"}, {target: {...opt.target, budgetId: "-".repeat(36)}},
    {timeoutMs: 0}, {timeoutMs: 4001}, {timeoutMs: 5000}]) {
    const result = await captureNativeObservation(wallet, {...opt, ...patch});
    assert.notEqual(result.reason, "observation_only");
  }
  assert.equal(calls.length, 0);
});
test("locked, malformed, unknown-version and wrong-network environments do not list actions", async () => {
  for (const [method, response] of [
    ["isAuthenticated", {authenticated: false}],
    ["isAuthenticated", {authenticated: "true"}],
    ["getVersion", {version: null}], ["getNetwork", {network: "testnet"}],
  ]) {
    const {wallet, opt, calls} = fixture();
    wallet[method] = async () => response;
    const result = await captureNativeObservation(wallet, opt);
    assert.equal(result.reason, "environment_not_verified");
    assert.equal(calls.length, 0);
  }
});
test("wrong and changed wallet are unknown, not successful inspection", async () => {
  const {wallet, opt, calls} = fixture();
  let n = 0;
  wallet.getPublicKey = async () => ({publicKey: ++n === 1 ? opt.target.identity : "03" + "bb".repeat(32)});
  assert.equal((await captureNativeObservation(wallet, opt)).reason, "wallet_changed_during_inspection");
  assert.equal(calls.length, 1);
});
test("target is frozen across awaited wallet calls", async () => {
  const {wallet, opt} = fixture();
  const initial = opt.target.identity;
  wallet.isAuthenticated = async () => { opt.target.identity = "03" + "bb".repeat(32); return {authenticated: true}; };
  wallet.getPublicKey = async () => ({publicKey: initial});
  assert.equal((await captureNativeObservation(wallet, opt)).reason, "observation_only");
});
test("hanging RPC stops subsequent calls and blocks overlapping/repeated inspection until it ends", async () => {
  const {wallet, opt, calls} = fixture();
  let resolve, count = 0;
  wallet.isAuthenticated = () => { count++; return new Promise(r => { resolve = r; }); };
  const pending = captureNativeObservation(wallet, opt);
  assert.equal((await captureNativeObservation(wallet, opt)).reason, "inspection_in_progress");
  const result = await pending;
  assert.equal(result.reason, "rpc_timeout_no_retry");
  assert.equal(result.outstanding_rpc, true);
  assert.equal((await captureNativeObservation(wallet, opt)).reason, "inspection_in_progress");
  assert.equal(count, 1);
  assert.equal(calls.length, 0);
  resolve({authenticated: true});
  await new Promise(r => setTimeout(r, 0));
  wallet.isAuthenticated = async () => ({authenticated: true});
  assert.equal((await captureNativeObservation(wallet, opt)).reason, "observation_only");
});
test("late listActions rejection is handled; no private error data or follow-on wallet calls", async () => {
  const {wallet, opt} = fixture();
  let reject;
  wallet.listActions = () => new Promise((_, r) => { reject = r; });
  const result = await captureNativeObservation(wallet, opt);
  assert.equal(result.reason, "rpc_timeout_no_retry");
  assert.equal(result.calls.at(-1), "listActions");
  reject(Error("SECRET_URL_AND_TOKEN"));
  await new Promise(r => setTimeout(r, 0));
  assert.equal(JSON.stringify(result).includes("SECRET"), false);
});
test("denied or unsupported inspection stays unknown; proxy getter errors are contained", async () => {
  const {wallet, opt} = fixture();
  wallet.listActions = async () => { throw Error("SECRET"); };
  const result = await captureNativeObservation(wallet, opt);
  assert.equal(result.reason, "wallet_inspection_unavailable");
  assert.equal(JSON.stringify(result).includes("SECRET"), false);
  const proxy = new Proxy({}, {get() { throw Error("SECRET"); }});
  assert.equal((await captureNativeObservation(proxy, opt)).reason, "inspection_unavailable");
});
test("helper remains outside production entry graph and shipped bundle", () => {
  for (const file of ["entry.js", "app.js", "portal.js", "grouped-page.js",
    "../../custom_components/bsv_settlement/frontend/driver/app.bundle.js"])
    assert.doesNotMatch(readFileSync(new URL(file, import.meta.url), "utf8"),
      /native-acceptance|captureNativeObservation|native-wallet-acceptance-observation-v1/);
});

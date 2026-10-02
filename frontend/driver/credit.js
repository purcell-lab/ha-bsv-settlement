import { PublicKey, P2PKH, Transaction, MerklePath } from "@bsv/sdk";
import { canonical, bytes, hash, hex, spendingProtocol } from "./model.js";

export async function registerCredit(wallet, checked, identity, api) {
  const t=checked.terms, r=t.credit_receiving;
  if (!r || canonical(r.protocolID)!==canonical([2,"3241645161d8"]))
    throw Error("A new invitation is required for automatic wallet credits.");
  if((await wallet.getNetwork()).network!=="mainnet")throw Error("Connect a mainnet wallet.");
  const {publicKey}=await wallet.getPublicKey({
    protocolID:r.protocolID, keyID:`${r.derivationPrefix} ${r.derivationSuffix}`,
    counterparty:t.operator_identity, forSelf:true,
  });
  const payload=canonical({
    action:"register_session_credit_destination", version:1, budget_id:t.budget_id,
    invitation_hash:await hash(checked.invitation.payload), driver_identity:identity,
    public_key:publicKey, address:PublicKey.fromString(publicKey).toAddress(),
  });
  const {signature}=await wallet.createSignature({
    protocolID:spendingProtocol,keyID:t.budget_id,counterparty:"anyone",
    data:bytes(payload),description:"Register this wallet to receive the session credit",
  });
  return api("register_credit_destination",{proof:{payload,signature:hex(signature)}});
}

export function creditTransaction(receipt, expectedAddress) {
  const {proof:p,block:b}=receipt;
  const tx=Transaction.fromHex(receipt.raw_tx);
  if(tx.id("hex")!==receipt.txid || p.txOrId!==receipt.txid || p.target!==b.hash ||
    !Number.isSafeInteger(p.index) || p.index<0 || !Number.isSafeInteger(b.height) || b.height<1 ||
    !Array.isArray(p.nodes) || p.nodes.length>40 ||
    !/^[0-9a-f]{64}$/.test(b.merkleroot) ||
    receipt.recipient_address!==expectedAddress ||
    !Number.isSafeInteger(receipt.amount_sats) || receipt.amount_sats<1 || receipt.amount_sats>990 ||
    tx.outputs[0]?.satoshis!==receipt.amount_sats ||
    tx.outputs[0]?.lockingScript.toHex()!==new P2PKH().lock(expectedAddress).toHex())
    throw Error("The credit receipt does not match this wallet and session.");
  const path=[[{offset:p.index,hash:receipt.txid,txid:true}]];
  let index=p.index;
  for(let level=0;level<p.nodes.length;level++){
    const node=p.nodes[level];
    if(node!=="*" && !/^[0-9a-f]{64}$/.test(node))throw Error("Invalid credit Merkle proof.");
    if(!path[level])path[level]=[];
    const sibling=index%2===0 ? index+1 : index-1;
    path[level].push(node==="*" ? {offset:sibling,duplicate:true} : {offset:sibling,hash:node});
    path[level].sort((a,b)=>a.offset-b.offset);
    index=Math.floor(index/2);
  }
  const merkle=new MerklePath(b.height,path);
  if(merkle.computeRoot(receipt.txid)!==b.merkleroot)throw Error("Credit Merkle root mismatch.");
  tx.merklePath=merkle;
  return tx;
}

export async function importCredit(wallet, checked, receipt) {
  const t=checked.terms,r=t.credit_receiving;
  if(canonical(receipt.remittance)!==canonical(r) || receipt.sender_identity!==t.operator_identity ||
     receipt.budget_id!==t.budget_id)throw Error("Credit remittance does not match the invitation.");
  if((await wallet.getNetwork()).network!=="mainnet")throw Error("Connect a mainnet wallet.");
  const {publicKey}=await wallet.getPublicKey({
    protocolID:r.protocolID,keyID:`${r.derivationPrefix} ${r.derivationSuffix}`,
    counterparty:t.operator_identity,forSelf:true,
  });
  const tx=creditTransaction(receipt,PublicKey.fromString(publicKey).toAddress());
  const result=await wallet.internalizeAction({
    tx:tx.toAtomicBEEF(),
    outputs:[{outputIndex:0,protocol:"wallet payment",paymentRemittance:{
      derivationPrefix:r.derivationPrefix,derivationSuffix:r.derivationSuffix,
      senderIdentityKey:t.operator_identity,
    }}],description:"EV session energy credit",
  });
  if(result.accepted!==true)throw Error("The wallet has not accepted the credit receipt.");
  return result;
}

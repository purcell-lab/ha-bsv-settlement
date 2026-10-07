import { PublicKey, P2PKH, Transaction, MerklePath } from "@bsv/sdk";
import { canonical, bytes, hash, hex, spendingProtocol } from "./model.js";

export function creditDescription(receipt) {
  const base="EV session energy credit", a=receipt.energy_account;
  // Display only verified, session-matched metadata. Never turn missing data
  // into a zero, or prevent import of an otherwise valid older payment.
  const numeric=v=>(typeof v==="number" || (typeof v==="string" && v.trim()!=="")) &&
    Number.isFinite(Number(v));
  if(!a || a.currency!=="AUD" || !receipt.session_id ||
     a.session_id!==receipt.session_id || a.ocpp_transaction_id!==receipt.transaction_id ||
     ![a.import_kwh,a.export_kwh,a.net_amount_aud,receipt.net_amount_aud].every(numeric))
    return base;
  const imported=Number(a.import_kwh), exported=Number(a.export_kwh);
  const net=Number(a.net_amount_aud), total=imported+exported;
  if(imported<0 || exported<0 || total<=0 || total>1e9 || net>=0 ||
     net!==Number(receipt.net_amount_aud) || !Number.isFinite(-net/total))
    return base;
  const energy=total.toFixed(2);
  if(Number(energy)===0)return base;
  const direction=imported>0 && exported>0
    ? `${energy} kWh total (${imported.toFixed(2)} in, ${exported.toFixed(2)} out)`
    : `${energy} kWh ${exported>0?"exported":"charged"}`;
  const average=-net/total;
  const price=average>0 && average<0.00005?"<A$0.0001":`A$${average.toFixed(4)}`;
  return `EV session credit | ${direction} | avg net credit ${price}/kWh`;
}

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
    data:bytes(payload),description:t.version===3?
      `Register to receive EV session credits until ${t.expires_at} or a newer driver registration`:
      "Register this wallet to receive the session credit",
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
    !Number.isSafeInteger(receipt.amount_sats) || receipt.amount_sats<1 ||
    !Number.isSafeInteger(receipt.fee_sats) || receipt.fee_sats<1 ||
    receipt.amount_sats+receipt.fee_sats>1000 ||
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

export const earlyCreditStage="unconfirmed_ancestor_proven_v1";

export async function earlyCreditTransaction(receipt,expectedAddress){
  if(receipt.delivery_stage!==earlyCreditStage||receipt.state!=="provider_unconfirmed"||
    !Array.isArray(receipt.ancestry)||receipt.ancestry.length!==1||
    typeof receipt.raw_tx!=="string"||receipt.raw_tx.length>200000)
    throw Error("Unsupported early credit envelope.");
  const source=receipt.ancestry[0],p=source.proof,b=source.block;
  if(typeof source.raw_tx!=="string"||source.raw_tx.length>200000)
    throw Error("Unsupported credit funding transaction.");
  const tx=Transaction.fromHex(receipt.raw_tx),parent=Transaction.fromHex(source.raw_tx);
  const input=tx.inputs[0],index=input?.sourceOutputIndex;
  if(tx.id("hex")!==receipt.txid||parent.id("hex")!==source.txid||tx.inputs.length!==1||
    tx.lockTime!==0||!input?.unlockingScript||input.sourceTXID!==source.txid||
    !Number.isSafeInteger(index)||index<0||!parent.outputs[index]||
    receipt.recipient_address!==expectedAddress||
    !Number.isSafeInteger(receipt.amount_sats)||receipt.amount_sats<1||
    !Number.isSafeInteger(receipt.fee_sats)||receipt.fee_sats<1||
    receipt.amount_sats+receipt.fee_sats>1000||
    tx.outputs.length<1||tx.outputs.length>2||
    tx.outputs.some(o=>!Number.isSafeInteger(o.satoshis)||o.satoshis<0)||
    tx.outputs[0].satoshis!==receipt.amount_sats||
    tx.outputs[0].lockingScript.toHex()!==new P2PKH().lock(expectedAddress).toHex()||
    parent.outputs[index].lockingScript.toHex()!==new P2PKH().lock(
      PublicKey.fromString(receipt.sender_identity).toAddress()).toHex()||
    parent.outputs[index].satoshis-tx.outputs.reduce((sum,o)=>sum+o.satoshis,0)!==receipt.fee_sats)
    throw Error("Early credit funding, signature or recipient mismatch.");
  if(!p||!b||p.txOrId!==source.txid||p.target!==b.hash||
    !/^[0-9a-f]{64}$/.test(b.hash)||!/^[0-9a-f]{64}$/.test(b.merkleroot)||
    !Number.isSafeInteger(p.index)||p.index<0||!Number.isSafeInteger(b.height)||b.height<1||
    !Array.isArray(p.nodes)||p.nodes.length>40||p.index>=2**p.nodes.length)
    throw Error("Invalid confirmed funding proof.");
  const path=[[{offset:p.index,hash:source.txid,txid:true}]];
  let offset=p.index;
  for(let level=0;level<p.nodes.length;level++){
    const node=p.nodes[level];
    if(node!=="*"&&!/^[0-9a-f]{64}$/.test(node))throw Error("Invalid funding proof node.");
    if(!path[level])path[level]=[];
    const sibling=offset%2===0?offset+1:offset-1;
    path[level].push(node==="*"?{offset:sibling,duplicate:true}:{offset:sibling,hash:node});
    path[level].sort((a,b)=>a.offset-b.offset);offset=Math.floor(offset/2);
  }
  const merkle=new MerklePath(b.height,path);
  if(merkle.computeRoot(source.txid)!==b.merkleroot)throw Error("Funding Merkle root mismatch.");
  parent.merklePath=merkle;input.sourceTransaction=parent;
  // Validate spending scripts locally; native wallet must validate headers and
  // apply its own unconfirmed-payment policy. Never fabricate a child proof.
  if(!await tx.verify("scripts only"))throw Error("Invalid early credit spending scripts.");
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
  if(receipt.delivery_stage!==undefined&&receipt.delivery_stage!==earlyCreditStage)
    throw Error("Unsupported credit receipt stage.");
  const tx=receipt.delivery_stage===earlyCreditStage?
    await earlyCreditTransaction(receipt,PublicKey.fromString(publicKey).toAddress()):
    creditTransaction(receipt,PublicKey.fromString(publicKey).toAddress());
  const result=await wallet.internalizeAction({
    tx:tx.toAtomicBEEF(),
    outputs:[{outputIndex:0,protocol:"wallet payment",paymentRemittance:{
      derivationPrefix:r.derivationPrefix,derivationSuffix:r.derivationSuffix,
      senderIdentityKey:t.operator_identity,
    }}],description:creditDescription(receipt),
  });
  if(result.accepted!==true)throw Error("The wallet has not accepted the credit receipt.");
  return result;
}

export const receiptProtocol = [2, "ev credit receipt"];
export function receiptReported(row) {
  return row?.wallet_receipt_status === "wallet_reported_accepted" &&
    Number.isFinite(Date.parse(row.wallet_imported_at));
}

export async function importAndReportCredit(wallet, checked, receipt, identity, api, cache) {
  // Cache only in this page, never authority. A reporting retry must not repeat
  // internalizeAction or create a payment. Reloads use the server report.
  if ((await wallet.getPublicKey({identityKey:true})).publicKey !== identity)
    throw Error("Connect the wallet that registered this receiving address.");
  const key=`${receipt.budget_id}:${receipt.txid}`;
  let entry=cache.get(key);
  if(!entry) {
    await importCredit(wallet,checked,receipt);
    entry={accepted:true,deliveryStage:receipt.delivery_stage};cache.set(key,entry);
  }
  try {
    if(!entry.acknowledgement) {
      const payload=canonical({
        action:"report_credit_receipt_accepted",version:1,network:"BSV mainnet",
        budget_id:checked.terms.budget_id,credit_id:receipt.credit_id||receipt.budget_id,
        session_id:receipt.session_id,transaction_id:receipt.transaction_id,txid:receipt.txid,
        output_index:0,amount_sats:receipt.amount_sats,recipient_address:receipt.recipient_address,
        driver_identity:identity,operator_identity:checked.terms.operator_identity,
        invitation_hash:await hash(checked.invitation.payload),accepted:true,
        ...(entry.deliveryStage===earlyCreditStage?{version:2,delivery_stage:earlyCreditStage}:{}),
      });
      const {signature}=await wallet.createSignature({
        protocolID:receiptProtocol,keyID:checked.terms.budget_id,counterparty:"anyone",
        data:bytes(payload),
        description:entry.deliveryStage===earlyCreditStage?
          "Report receipt of this unconfirmed EV credit. Not block confirmation or spending permission.":
          "Report receipt acceptance for this existing EV credit. No payment or spending permission.",
      });
      entry.acknowledgement={payload,signature:hex(signature)};
    }
    const result=await api("acknowledge_credit_receipt",{
      ...(receipt.credit_id?{credit_id:receipt.credit_id}:{}),
      ...(entry.deliveryStage===earlyCreditStage?{delivery_stage:earlyCreditStage}:{}),
      acknowledgement:entry.acknowledgement,
    });
    if(result.txid!==receipt.txid || !receiptReported(result))
      throw Error("The operator did not confirm saving the acknowledgement.");
    return result;
  } catch(e) {
    const error=new Error(`Wallet accepted the receipt; reporting to the operator is pending: ${e.message}`);
    error.walletAccepted=true;
    throw error;
  }
}

/* Read only browser proof formats, never runtime settings or credentials. */
import {readFile,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';

const proofNames=new Set(['receipt.json','acceptance.json','browser.json',
  'runtime-settings-browser.json','runtime-settings.json']);

export async function browserProofs(directory){
  const proofs=[];
  for(const entry of await readdir(directory,{withFileTypes:true})){
    const file=path.join(directory,entry.name);
    if(entry.isSymbolicLink())continue;
    if(entry.isDirectory())proofs.push(...await browserProofs(file));
    else if(proofNames.has(entry.name)){
      const bytes=await readFile(file),record=JSON.parse(bytes);
      if(!Array.isArray(record.checks)||!record.checks.length)continue;
      // The two retained assertion runners write acceptance.json only after
      // their assertions complete; their parent must also finish with exit 0.
      const assertionReceipt=entry.name==='acceptance.json'&&record.status===undefined
        &&Array.isArray(record.errors)&&record.checks.every(check=>typeof check==='string'&&check.length);
      assert.ok(record.status==='passed'||assertionReceipt,file);
      assert.ok(record.checks.every(check=>{
        if(typeof check==='string')return check.length>0;
        if(!check||typeof check!=='object')return false;
        if('passed' in check)return typeof check.name==='string'&&check.name.length>0&&check.passed===true;
        return typeof check.kind==='string'&&check.kind.length>0&&check.status==='passed';
      }),file);
      assert.ok(!record.errors?.length&&!record.failures?.length&&!record.external_attempts?.length,file);
      proofs.push({path:file,sha256:createHash('sha256').update(bytes).digest('hex'),checks:record.checks.length,record});
    }
  }
  return proofs;
}

export function summarizeProofs(proofs){
  const unique=[...new Map(proofs.map(proof=>[proof.sha256,proof])).values()];
  return {contexts:proofs.map(({record,...metadata})=>metadata),
    raw_receipt_check_sum:proofs.reduce((sum,proof)=>sum+proof.checks,0),
    unique_context_count:unique.length,checks:unique.reduce((sum,proof)=>sum+proof.checks,0)};
}

/* Pure UI naming checks: no browser, production Store or network required. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
const root=path.resolve(import.meta.dirname,'..');
const code=readFileSync(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const start=code.indexOf('function sourceRecordName(record){');
const end=code.indexOf('function sourceRecordButtons(task){',start);
assert(start>=0&&end>start);
const label=vm.runInNewContext(code.slice(start,end)+'\nsourceRecordName');
assert.equal(label({source:'official',operation:'release_catalog',parameters:{channel:'nbs_release'}}),'国家统计局发布目录');
assert.equal(label({source:'official',operation:'release_catalog',parameters:{channel:'fed_fomc'}}),'FOMC声明目录');
assert.equal(label({source:'official',operation:'release_catalog',parameters:{channel:'unknown'}}),'官方发布目录');
assert.equal(label({source:'official',operation:'release_catalog'}),'官方发布目录');
assert.equal(label({source:'official',kind:'material',operation:'release_original',snapshot:{official_origin:{title:'FOMC声明 2026-09-16'}}}),'FOMC声明 2026-09-16');
assert.equal(label({source:'ifind',kind:'api',operation:'date_sequence'}),'iFinD 序列数据');
console.log(JSON.stringify({status:'passed',checks:6,nativeWindowOpened:false}));

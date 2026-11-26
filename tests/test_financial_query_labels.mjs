/* Exercise the actual source-label function without booting another App. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source=fs.readFileSync(path.join(root,'products/app/frontend/workspace.js'),'utf8');
const start=source.indexOf('function sourceRecordName(record){');
const end=source.indexOf('\nfunction sourceRecordButtons(',start);
assert.ok(start>=0&&end>start,'Read the complete actual label function');
const context=vm.createContext({window:{}});
const data=fs.readFileSync(path.join(root,'products/app/frontend/ui/research-data.js'),'utf8');
vm.runInContext(data,context);
const label=vm.runInContext(source.slice(start,end)+'\nsourceRecordName',context);

test('financial semantic source uses a concise business label',()=>{
  const name=label({kind:'api',source:'ifind',operation:'smart_stock_picking'});
  assert.equal(name,'iFinD 财务数据查询');
  assert.ok(!name.includes('smart_stock_picking'));
});

test('existing disclosure and market source names remain unchanged',()=>{
  assert.equal(label({kind:'api',source:'ifind',operation:'report_query'}),'iFinD 公告目录');
  assert.equal(label({kind:'api',source:'tinyshare',operation:'income'}),'Tinyshare 利润表');
  assert.equal(label({kind:'api',source:'ifind',operation:'cmd_history_quotation'}),'iFinD 历史行情');
});

test('original-document titles are not replaced by API terminology',()=>{
  assert.equal(label({kind:'filing',snapshot:{material:{name:'半年度报告.pdf'}}}),'半年度报告.pdf');
  assert.equal(label({kind:'material',name:'研究材料.txt'}),'研究材料.txt');
});

test('official fund and index records expose useful source names',()=>{
  assert.equal(label({kind:'api',source:'official',operation:'etf_pcf'}),'上交所ETF申赎清单');
  assert.equal(label({kind:'api',source:'official',operation:'etf_daily_quotes',parameters:{ts_code:'510300.SH'}}),'上交所ETF日行情');
  assert.equal(label({kind:'api',source:'official',operation:'etf_daily_quotes',parameters:{ts_code:'159925.SZ'}}),'深交所ETF日行情');
  assert.equal(label({kind:'api',source:'official',operation:'etf_nav_history'}),'深交所ETF单位净值');
  assert.equal(label({kind:'api',source:'official',operation:'index_catalog'}),'中证指数文件目录');
  assert.equal(label({kind:'api',source:'official',operation:'index_file',snapshot:{role:'weights'}}),'中证指数成分权重');
  assert.equal(label({kind:'api',source:'official',operation:'index_file',snapshot:{role:'members'}}),'中证指数成分名单');
});
test('economic discovery remains distinct from observation data',()=>{
  assert.equal(label({kind:'api',source:'ifind',operation:'search_edb'}),'iFinD 指标目录');
  assert.equal(label({kind:'api',source:'ifind',operation:'edb_service'}),'iFinD 经济数据');
});

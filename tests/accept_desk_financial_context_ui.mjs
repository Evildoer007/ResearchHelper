/* Extend the retained evidence suite with explicitly mixed financial scopes. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const root=path.resolve(import.meta.dirname,'..');
const output=process.env.RESEARCHHELPER_UI_OUTPUT||path.join(root,'result/acceptance/desk-financial-context-20261105/source-browser');
await fs.mkdir(output,{recursive:true});
const previous=await fs.readFile(path.join(root,'tests/accept_desk_evidence_ui.mjs'),'utf8');
const replaceOne=(source,before,after)=>{assert.equal(source.split(before).length,2,'Inspect the retained suite before adapting it');return source.replace(before,after)};
let adapted=replaceOne(previous,"const root=path.resolve(import.meta.dirname,'..');",'const root='+JSON.stringify(root)+';');
adapted=replaceOne(adapted,"raw_value:value,unit:'原始单位待核对',locator:","raw_value:value,unit:type==='1'?'元':'原始单位待核对',locator:");
const additional=`
 const separate=structuredClone(scopes);
 for(const fact of separate.draft_report.facts)if(fact.id.startsWith('scope-fact-2-')){fact.field='ths_np_stock';fact.label='净利润';fact.locator.field='ths_np_stock'}
 for(const source of separate.source_records)if(source.id.startsWith('scope-2-')){
  source.parameters.indipara[0].indicator='ths_np_stock';
  const table=source.snapshot.tables[0].table;table.ths_np_stock=table.ths_revenue_stock;delete table.ths_revenue_stock;
 }
 const originalSeparate=JSON.stringify(separate);
 await showTask(separate,1);await scopeView.locator('[data-evidence-view=table]').click();
 check('different fields retain their own explicit financial request scope',await scopeView.locator('tbody th').allTextContents().then(rows=>rows.length===2&&rows[0].includes('营业收入')&&rows[0].includes('请求合并报表')&&rows[1].includes('净利润')&&rows[1].includes('请求母公司报表')));
 check('different fields preserve known and unresolved literal units',await scopeView.locator('tbody th').allTextContents().then(rows=>rows[0].endsWith('元')&&rows[1].endsWith('单位待核对')));
 check('a mixed-field view preserves all six value links',await scopeView.locator('[data-observation]').count()===6);
 await scopeView.locator('[data-evidence-view=chart]').click();await scopeCanvas.locator('canvas').waitFor();
 check('mixed-field chart titles preserve both field and request context',await scopeCanvas.evaluate(element=>{const titles=window.echarts.getInstanceByDom(element).getOption().title.map(row=>row.text);return titles[0].includes('营业收入')&&titles[0].includes('请求合并报表')&&titles[1].includes('净利润')&&titles[1].includes('请求母公司报表')}));
 check('mixed-field presentation does not mutate the saved input',await page.evaluate(before=>JSON.stringify(selected)===before,originalSeparate));
`;
adapted=replaceOne(adapted," check('no unexpected script errors',receipt.errors.length===0);",additional+" check('no unexpected script errors',receipt.errors.length===0);");
const generated=path.join(output,'financial-context-case.mjs');await fs.writeFile(generated,adapted);
await fs.writeFile(path.join(output,'fixture-origin.json'),JSON.stringify({source:'tests/accept_desk_evidence_ui.mjs',sha256:createHash('sha256').update(previous).digest('hex'),preserved_assertions:true,changes:['Synthetic consolidated amounts use explicit yuan; parent amounts keep unknown units','An additional disjoint-field case checks visible rows, chart titles and immutable inputs'],model_calls:0,data_requests:0},null,2));
const completed=spawnSync(process.execPath,[generated],{cwd:root,env:{...process.env,RESEARCHHELPER_UI_OUTPUT:output},encoding:'utf8',timeout:100000});
process.stdout.write(completed.stdout||'');process.stderr.write(completed.stderr||'');assert.equal(completed.status,0,'Inspect the mixed-context browser receipt');

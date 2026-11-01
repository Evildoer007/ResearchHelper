const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const sandbox={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/desk-report.js'),'utf8'),sandbox);
const render=sandbox.window.RHDeskViews.report;
const esc=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const context=overrides=>({esc,apiMode:true,reportProfile:()=> 'standard',reportURL:()=>'/api/tasks/one/report/2?profile=standard',reportVersionBusy:()=>false,...overrides});
const delivered={id:'one',version:2,delivery:{version:2},report_html:'<h1>标准正文</h1>',report_detailed_html:'<h1>详细正文</h1>'};
test('empty reports offer working navigation without acceptance gates',()=>{
  for(const task of [null,{status:'待开始'},{pending_content:{},status:'失败'}]){
    const html=render(task,context());
    assert.match(html,/data-action="chat"/);assert.doesNotMatch(html,/iframe|通过检查|data-action="export"/);
  }
  assert.match(render({status:'运行中'},context()),/data-stage="1"/);
});
test('delivered reports preserve HTML first, menus, frame and saved version',()=>{
  const html=render(delivered,context());
  assert.match(html,/<option value="html">HTML/);
  for(const action of ['export','print','visual-library','versions'])assert.match(html,new RegExp('data-action="'+action+'"'));
  assert.match(html,/版本2/);assert.match(html,/id="report-frame"/);assert.match(html,/src="\/api\/tasks\/one\/report\/2/);
});
test('offline profiles read their respective frozen HTML without mutation',()=>{
  const before=JSON.stringify(delivered);
  for(const profile of ['standard','detailed']){
    const html=render(delivered,context({apiMode:false,reportProfile:()=>profile}));
    assert.match(html,new RegExp('data-report-profile="'+profile+'" aria-pressed="true"'));
    assert.match(html,new RegExp('srcdoc="&lt;h1&gt;'+(profile==='standard'?'标准':'详细')+'正文'));
  }
  assert.equal(JSON.stringify(delivered),before);
});
test('offline unsupported formats and absent detailed variant are disabled',()=>{
  const html=render({...delivered,report_detailed_html:null},context({apiMode:false,reportProfile:()=> 'detailed'}));
  for(const format of ['docx','xlsx','csv'])assert.match(html,new RegExp('value="'+format+'" disabled'));
  assert.match(html,/data-report-profile="detailed" aria-pressed="false" disabled/);
  assert.match(html,/打印PDF/);assert.match(html,/srcdoc="&lt;h1&gt;标准正文/);
});
test('version requests disable the existing version entry',()=>{
  assert.match(render(delivered,context({reportVersionBusy:()=>true})),/data-action="versions" disabled aria-busy="true"/);
});
test('version, URL and offline HTML cannot escape their containers',()=>{
  const task={...delivered,delivery:{version:'<img src=x>'},report_html:'"><img src=x onerror="bad()">'};
  const live=render(task,context({reportURL:()=>'/report?x=" onload="bad()'}));
  assert.match(live,/版本&lt;img src=x&gt;/);assert.match(live,/x=&quot; onload=&quot;bad\(\)/);
  const offline=render(task,context({apiMode:false}));
  assert.match(offline,/srcdoc="&quot;&gt;&lt;img/);assert.doesNotMatch(offline,/<img/);
});

const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../products/app/frontend/ui/research-data.js'),'utf8');
const box={window:{}};vm.runInNewContext(source,box);
const data=box.window.RHResearchData;
const esc=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const task={id:'synthetic-reading-ui',version:1};
const cases=[
 {value:1.125,display:'1.13',expected:'1.13'},
 {value:-0.0001,display:'-0.0001',expected:'-0.0001'},
 {value:1.23456e-7,display:'1.23e-7',expected:'1.23e-7'},
 {value:-1.23456e-9,display:'-1.23e-9',expected:'-1.23e-9'},
 {value:0,display:'0.00',expected:'0.00'},
 {value:1.25,display:'<script>bad</script>',expected:'1.25'},
 {value:1.25,display:'1e9999',expected:'1.25'},
];
for(const [index,row] of cases.entries()){
 const calc={id:'A'+index,label:'计算读数',unit:'%',...row};
 const before=JSON.stringify({calc,task});
 const html=data.calcButton(calc,task,{esc});
 assert.ok(html.includes('<strong>'+row.expected+'%</strong>'),html);
 assert.ok(html.includes('data-source-task="synthetic-reading-ui"')&&html.includes('data-source-version="1"'));
 assert.equal(JSON.stringify({calc,task}),before);
}
console.log(JSON.stringify({status:'passed',cases:cases.length,checks:cases.length*3}));

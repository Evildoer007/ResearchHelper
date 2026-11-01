/* Real SVG bounds for ordered qualitative paths; no financial or model input. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=process.cwd(),output=process.env.RESEARCHHELPER_MECHANISM_OUTPUT;
assert(output,'Use an isolated acceptance output');await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const record={status:'running',cases:[],errors:[],externalRequests:0,model_calls:0,data_requests:0};
try{
 const page=await browser.newPage({viewport:{width:900,height:700},reducedMotion:'reduce'});
 await page.route(/^https?:/,route=>{record.externalRequests++;return route.abort()});
 page.on('pageerror',error=>record.errors.push(error.message));
 await page.setContent('<!doctype html><style>body{margin:0}#chart{width:350px;height:154px}</style><div id="chart"></div>');
 for(const file of ['assets/vendor/echarts.min.js','products/app/frontend/visual-catalog-data.js','products/app/frontend/visual-library.js'])await page.addScriptTag({path:path.join(root,file)});
 for(const long of [false,true])for(const width of [280,350,700])for(const height of (long?[260]:[154,220]))for(const count of [2,3,4,5]){
  const state=await page.evaluate(({width,height,count,long})=>{
   const short=['需求变化','传导条件','业务影响','相反解释','反馈变化'];
   const names=long?short.map(text=>text+'需要结合实际范围及适用条件分别核对'):short;
   const data={qualitative:true,nodes:names.slice(0,count).map((name,index)=>({id:'n'+index,name,level:index,kind:index===1?'condition':'hypothesis'})),
    links:Array.from({length:count-1},(_,index)=>({source:'n'+index,target:'n'+(index+1)}))};
   const frozen=JSON.stringify(data),node=document.getElementById('chart');
   echarts.getInstanceByDom(node)?.dispose();node.style.width=width+'px';node.style.height=height+'px';
   const chart=echarts.init(node,null,{renderer:'svg'});chart.setOption(RHVisuals.option('evidence',data,'research',{width,height}));chart.getZr().flush();
   const edge=node.getBoundingClientRect(),rect=element=>{const b=element.getBoundingClientRect();return {left:b.left-edge.left,right:b.right-edge.left,top:b.top-edge.top,bottom:b.bottom-edge.top}};
   const boxes=[...node.querySelectorAll('svg path, svg rect')].map(element=>({element,...rect(element)}))
    .filter(box=>box.element.getAttribute('fill')!=='none'&&box.element.getAttribute('stroke')&&box.element.getAttribute('stroke')!=='none'&&box.right-box.left>50&&box.bottom-box.top>40)
    .map(({element,...box})=>box);
   const labels=[...node.querySelectorAll('svg text')].map(element=>({text:element.textContent,...rect(element)}));
   const outside=boxes.concat(labels).filter(box=>box.left<0||box.top<0||box.right>width+1||box.bottom>height+1);
   const collisions=[];
   for(let a=0;a<boxes.length;a++)for(let b=a+1;b<boxes.length;b++){
    const x=Math.min(boxes[a].right,boxes[b].right)-Math.max(boxes[a].left,boxes[b].left);
    const y=Math.min(boxes[a].bottom,boxes[b].bottom)-Math.max(boxes[a].top,boxes[b].top);
    if(x>1&&y>1)collisions.push([a,b]);
   }
   const arrows=[...node.querySelectorAll('svg path,svg polygon')].map(element=>({element,...rect(element)})).filter(box=>box.element.getAttribute('fill')!=='none'&&box.right-box.left>3&&box.right-box.left<12&&box.bottom-box.top>3&&box.bottom-box.top<12);
   const escapedLabels=labels.filter(label=>!boxes.some(box=>label.left>=box.left-1&&label.right<=box.right+1&&label.top>=box.top-1&&label.bottom<=box.bottom+1));
   return {width,height,count,long,boxes:boxes.length,arrows:arrows.length,outside,collisions,escapedLabels,
    valuesUnchanged:JSON.stringify(data)===frozen,labelsComplete:labels.map(label=>label.text).join('').replaceAll(/\s/g,'')===names.slice(0,count).join('').replaceAll(/\s/g,'')};
  },{width,height,count,long});
  record.cases.push(state);
  if(width===350&&((count===3&&!long&&height===154)||(count===5&&long)))await page.screenshot({path:path.join(output,long?'five-long-nodes.png':'three-nodes.png')});
 }
 record.status=record.cases.every(row=>row.boxes===row.count&&row.arrows===row.count-1&&!row.outside.length&&!row.collisions.length&&!row.escapedLabels.length&&row.valuesUnchanged&&row.labelsComplete)&&!record.errors.length&&!record.externalRequests?'passed':'failed';
}catch(error){record.status='failed';record.error=String(error)}
finally{
 await browser.close();await writeFile(path.join(output,'receipt.json'),JSON.stringify(record,null,2));
 console.log(JSON.stringify({status:record.status,cases:record.cases.length,
  failed:record.cases.filter(row=>row.boxes!==row.count||row.arrows!==row.count-1||row.outside.length||row.collisions.length||row.escapedLabels.length||!row.valuesUnchanged||!row.labelsComplete).length,
  error:record.error,receipt:path.join(output,'receipt.json')}));
}
process.exitCode=record.status==='passed'?0:1;

/* Actual SVG geometry, not a claim inferred from data-only assertions. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const input=JSON.parse(await readFile(process.env.RESEARCHHELPER_CHART_REVIEW,'utf8'));
const output=path.dirname(process.env.RESEARCHHELPER_CHART_REVIEW);
const record={status:'failed',checks:[],failures:[],errors:[],screens:[],externalRequests:0};
const check=(name,passed)=>{record.checks.push({name,passed});if(!passed)record.failures.push(name)};
let browser;
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  for(const [width,height] of [[1440,900],[1024,768],[390,844]]){
    const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'});
    await context.route(/https?:\/\//,route=>{record.externalRequests++;return route.abort()});
    const page=await context.newPage();page.on('pageerror',error=>record.errors.push(error.message));
    await page.goto(pathToFileURL(input.report).href);await page.locator('main .chart svg').first().waitFor();
    check(width+' independent figures use full paper width',await page.locator('.argument-reading.argument-comparisons').count()===1);
    for(const media of ['screen','print']){
      await page.emulateMedia({media});await page.waitForTimeout(100);
      const geometry=await page.locator('main .chart').evaluateAll(nodes=>nodes.map(node=>{
        const edge=node.getBoundingClientRect();
        const labels=[...node.querySelectorAll('svg text')].map(text=>{
          const rect=text.getBoundingClientRect();return {text:text.textContent,left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};
        }).filter(rect=>rect.right>rect.left&&rect.bottom>rect.top);
        const outside=labels.filter(rect=>rect.left<edge.left-1||rect.right>edge.right+1||rect.top<edge.top-1||rect.bottom>edge.bottom+1);
        const collisions=[];
        for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++){
          const a=labels[i],b=labels[j];
          if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)
            collisions.push([a.text,b.text]);
        }
        const chart=echarts.getInstanceByDom(node),option=chart.getOption();
        return {outside,collisions,text:labels.map(label=>label.text).join(' '),
          values:option.series.flatMap(series=>series.data.map(item=>typeof item==='number'?item:item.value)),
          tooltip:typeof option.tooltip?.[0]?.formatter==='function'?option.tooltip[0].formatter({seriesIndex:0,dataIndex:0}):null};
      }));
      record.checks.push({name:width+' '+media+' measured geometry',geometry});
      check(width+' '+media+' no SVG text clips',geometry.every(item=>item.outside.length===0));
      check(width+' '+media+' no SVG label collisions',geometry.every(item=>item.collisions.length===0));
      check(width+' '+media+' explicit compact money unit',geometry.every(item=>item.text.includes('亿元')));
      check(width+' '+media+' underlying points keep exact original values',geometry.every(item=>item.values.every(value=>input.values.includes(value)))&&
        input.values.every(value=>geometry.some(item=>item.values.includes(value))));
      check(width+' '+media+' tooltip retains exact original amount unit',geometry.every(item=>typeof item.tooltip==='string'&&
        item.tooltip.includes('元')&&item.tooltip.includes(String(item.values[0]).replace(/\B(?=(\d{3})+(?!\d))/g,',')))||
        geometry.every(item=>typeof item.tooltip==='string'&&item.tooltip.replaceAll(',','').includes(String(item.values[0]))));
      check(width+' '+media+' no page overflow or client accordions',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)&&
        await page.locator('details,summary').count()===0);
      if(media==='screen'){
        const screenshot=path.join(output,'report-'+width+'.png');await page.screenshot({path:screenshot,fullPage:true});record.screens.push(screenshot);
      }
    }
    await context.close();
  }
  check('no external requests or JavaScript errors',record.externalRequests===0&&record.errors.length===0);
  record.status=record.failures.length?'failed':'passed';
}catch(error){record.error=String(error)}
finally{
  if(browser)await browser.close();
  await writeFile(path.join(output,'browser-receipt.json'),JSON.stringify(record,null,2));
  console.log(JSON.stringify({status:record.status,failures:record.failures,error:record.error}));
}
process.exitCode=record.status==='passed'?0:1;

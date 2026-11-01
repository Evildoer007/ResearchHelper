// Render both source icons in fresh headless pages, never launch either App.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=process.cwd(), output=path.join(root,'result/acceptance/icon-parity');
const paths={ResearchHelper:path.join(root,'products/app/frontend/app-icon.svg'),OptionHelper:'/Users/haoranxu/Desktop/OptionHelper/assets/icons/optionhelper-app-icon-tile-light.svg'};
await fs.mkdir(output,{recursive:true});
const sources={}, hashes={};
for(const [name,file] of Object.entries(paths)){sources[name]=await fs.readFile(file,'utf8');hashes[name]=createHash('sha256').update(sources[name]).digest('hex');}
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu','--force-color-profile=srgb']});
try{
  for(const size of [16,24,32,40,64,128,256,512,1024]){
    for(const [name,source] of Object.entries(sources)){
      const page=await browser.newPage({viewport:{width:size,height:size},deviceScaleFactor:1});
      try{
        await page.route('**/*',route=>route.abort());
        await page.setContent('<html><style>html,body{margin:0;background:transparent}body>svg{display:block;width:100vw;height:100vh}</style>'+source+'</html>');
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        await page.screenshot({path:path.join(output,name+'-'+size+'.png'),omitBackground:true});
      }finally{await page.close();}
    }
  }
}finally{await browser.close();}
for(const [name,file] of Object.entries(paths)){
  if(createHash('sha256').update(await fs.readFile(file)).digest('hex')!==hashes[name])throw Error('Icon source changed during comparison: '+name);
}
await fs.writeFile(path.join(output,'render.json'),JSON.stringify({source_paths:paths,source_hashes:hashes,sizes:[16,24,32,40,64,128,256,512,1024],headless:true,external_network:false},null,2));
console.log('Rendered 9 sizes for both icons without foreground UI.');

// Read-only UI review fixture. No service, provider, native window or clipboard.
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
  const page=await browser.newPage({reducedMotion:'reduce'});
  await page.route('**/*',route=>route.abort());
  await page.setContent('<button id="origin">Search</button>');
  await page.addScriptTag({content:await readFile(new URL('../products/app/frontend/ui/task-search.js',import.meta.url),'utf8')});
  await page.evaluate(()=>{
    window.RHTaskSearch.createTaskSearch({entries:()=>[{id:'fixture',kind:'task',title:'测试任务'}],select:()=>true,canOpen:()=>true,canSelect:()=>true,beforeOpen:()=>{},restoreFocus:()=>document.querySelector('#origin').focus()});
  });
  for(const key of ['Enter','Escape']) {
    await page.evaluate(()=>window.dispatchEvent(new Event('researchhelper:search-tasks')));
    const result=await page.evaluate(key=>{
      const input=document.querySelector('dialog input');input.value='测';input.focus();
      input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'测'}));
      const event=new KeyboardEvent('keydown',{key,isComposing:true,keyCode:229,bubbles:true,cancelable:true});
      input.dispatchEvent(event);
      return {key,prevented:event.defaultPrevented,searchOpen:document.querySelector('dialog').open,inputRetainsFocus:document.activeElement===input,focusText:document.activeElement.textContent};
    },key);
    console.log(JSON.stringify(result));
  }
} finally {await browser.close();}

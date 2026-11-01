// Execute the actual frontend invalidation and native bootstrap scripts in isolation.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const frontend=fs.readFileSync(path.join(__dirname,'../frontend/workspace.js'),'utf8');
const leave=frontend.slice(frontend.indexOf('function leaveWorkspace('),frontend.indexOf('async function api('));
function context(bridge){
  const events=[],element={value:'sensitive draft',inert:false,style:{},replaceChildren(){events.push('cleared')},close(){}};
  const value={leaving:false,draftKey:'',taskSelectionGeneration:0,stopWatching(){},tasks:[1],selected:{},historical:{},queuedMaterials:[1],pendingSubmission:{},composerMemory:new Map(),draft:{},$(){return element},window:{webkit:{messageHandlers:{researchhelper:bridge}}},location:{replace(url){events.push(url)}},loginURL(){return '/login/index.html'},events};
  vm.createContext(value);vm.runInContext(leave,value);return value;
}
test('identity invalidation notifies native host before clearing and redirecting',()=>{
  const messages=[],value=context({postMessage(message){messages.push(message)}});
  value.leaveWorkspace();assert.equal(messages[0].action,'signed_out');assert.equal(value.tasks.length,0);assert.equal(value.selected,null);assert.equal(value.events.at(-1),'/login/index.html');
  value.leaveWorkspace();assert.equal(messages.length,1);
});
test('offline browser needs no native bridge',()=>{const value=context(undefined);value.leaveWorkspace();assert.equal(value.leaving,true);assert.equal(value.selected,null)});
test('native bridge failure cannot retain sensitive workspace',()=>{const value=context({postMessage(){throw Error('host closed')}});value.leaveWorkspace();assert.equal(value.tasks.length,0);assert.equal(value.events.at(-1),'/login/index.html')});
const csharp=fs.readFileSync(path.join(__dirname,'../desktop/windows/Program.cs'),'utf8');
const source=csharp.match(/\$"(\(\(\)=>\{\{if\(window\.top[^\n]+)"/)[1].replaceAll('{{','{').replaceAll('}}','}').replaceAll('{material}','fallback').replaceAll('{scale}','1');
function bootstrap(root,frame=false){
  let handler;const messages=[],document={documentElement:root,addEventListener(name,callback){assert.equal(name,'DOMContentLoaded');handler=callback}},window={chrome:{webview:{postMessage(value){messages.push(value)}}}};window.top=frame?{}:window;
  const value={window,document,location:{pathname:'/',href:'http://127.0.0.1:43123/'},URL};vm.runInNewContext(source,value);return {window,document,messages,ready:()=>handler()};
}
test('Windows bridge is registered even before documentElement exists',()=>{
  const value=bootstrap(null);value.window.webkit.messageHandlers.researchhelper.postMessage({action:'signed_out'});assert.equal(value.messages[0].action,'signed_out');assert.equal(value.messages[0].type,'researchhelper_action');
  value.document.documentElement={dataset:{},classList:{add(){}}};value.ready();assert.equal(value.document.documentElement.dataset.nativeShell,'windows');
});
test('bootstrap never gives report subframes a native bridge',()=>{const value=bootstrap(null,true);assert.equal(value.window.webkit,undefined)});
test('both native shells clear report windows on identity invalidation',()=>{
  const swift=fs.readFileSync(path.join(__dirname,'../desktop/macos/ResearchHelperApp.swift'),'utf8');
  assert.match(csharp,/action\.GetString\(\) == "signed_out"\) CloseReportWindows\(\)/);
  assert.match(swift,/action == "signed_out"[\s\S]*?message\.frameInfo\.isMainFrame[\s\S]*?reportWindow\.close\(\)/);
  assert.match(csharp,/source\.AbsolutePath != "\/" && type\.GetString\(\) != "set_theme"/);
});

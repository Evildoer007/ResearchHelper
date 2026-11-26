// Execute the actual frontend invalidation and native bootstrap scripts in isolation.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const frontend=fs.readFileSync(path.join(__dirname,'../frontend/workspace.js'),'utf8');
const {functionSource}=require('./function_source.cjs');
const leave=['leaveWorkspace','cancelTaskSelection','closeArchivedFiles'].map(name=>functionSource(frontend,name)).join('\n');
const transition=fs.readFileSync(path.join(__dirname,'../frontend/ui/conversation-transition.js'),'utf8');
function context(bridge){
  const events=[],element={value:'sensitive draft',inert:false,style:{},
    replaceChildren(){events.push('cleared')},close(){},removeAttribute(name){events.push('removed '+name)}};
  const destroy=name=>({destroy(){events.push('destroyed '+name)}});
  const value={leaving:false,draftKey:'',draftSaveTimer:null,clearTimeout,taskSelectionGeneration:0,stopWatching(){events.push('stopped watching')},
    artifactExport:{cancelPreparation(){events.push('report export cancelled')}},
    tasks:[1],selected:{},historical:{},queuedMaterials:[1],pendingSubmission:{},composerMemory:new Map(),draft:{},
    reportChoices:new Map(),reportVersionDialog:null,pendingTaskView:{mode:'desk'},selectingTaskId:'pending-task',$(){return element},
    archiveFilePanel:{controller:{abort(){events.push('archived-file read aborted')}}},
    window:{webkit:{messageHandlers:{researchhelper:bridge}}},
    location:{replace(url){events.push(url)}},loginURL(){return '/login/index.html'},events,
    ...Object.fromEntries(['taskSearch','userGuide','messageActions','researchProcess','deskInteractions',
      'reportReader','conversationOutline','conversationReader','composerControls','workspaceModeMotion'].map(name=>[name,destroy(name)]))};
  vm.createContext(value);vm.runInContext(transition,value);
  value.taskTransition=value.window.RHConversationTransition.createConversationTransition(null);
  vm.runInContext(leave,value);return value;
}
test('identity invalidation notifies native host before clearing and redirecting',()=>{
  const messages=[],value=context({postMessage(message){messages.push(message)}});
  value.leaveWorkspace();assert.equal(messages[0].action,'signed_out');assert.equal(value.tasks.length,0);assert.equal(value.selected,null);assert.equal(value.events.at(-1),'/login/index.html');
  assert.equal(value.selectingTaskId,'');assert.equal(value.pendingTaskView,null);
  assert.equal(value.archiveFilePanel,null);assert.ok(value.events.includes('archived-file read aborted'));
  assert.equal(value.taskSelectionGeneration,2);
  assert.ok(value.events.includes('destroyed workspaceModeMotion'));
  assert.equal(value.events.filter(event=>event.startsWith('destroyed ')).length,10);
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
  assert.match(csharp,/requestedAction == "signed_out"\) LeaveWorkspace\(\)/);
  assert.match(csharp,/private void LeaveWorkspace\(\)\s*\{[^}]*CloseReportWindows\(\);/);
  assert.match(swift,/action == "signed_out"[\s\S]*?message\.frameInfo\.isMainFrame[\s\S]*?leaveWorkspace\(\)/);
  assert.match(swift,/private func leaveWorkspace\(\)[\s\S]*?for reportWindow in Array\(reportWindows\.keys\) \{ reportWindow\.close\(\) \}/);
  assert.match(csharp,/source\.AbsolutePath != "\/" && type\.GetString\(\) != "set_theme"/);
});

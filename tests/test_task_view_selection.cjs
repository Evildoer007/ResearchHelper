const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('products/app/frontend/workspace.js','utf8');
const selection=source.slice(source.indexOf('function cancelTaskSelection(){'),source.indexOf('function stopWatching(){'));
const navigation=source.slice(source.indexOf('function view(index){'),source.indexOf('function renderAssistant(){'));
const permission=source.slice(source.indexOf('function canUseDesk(){'),source.indexOf('function savedDeskStage(){'));
const labels=source.split('\n').find(line=>line.startsWith('const labels='));
const task=id=>({id,version:1,revision:1,question:'研究'+id,cutoff:'2026-11-05',status:'失败',nodes:[],questions:[]});
function fixture({selected=task('a'),desk=false,role='admin'}={}){
 const elements=new Map(),saved={a:1,b:2,c:0},writes=[],reads=[];let request;
 const noop=()=>{},context={apiMode:true,boot:{authentication:true,role},selectingTaskId:'',pendingTaskView:null,taskSelectionGeneration:0,selected,historical:null,leaving:false,contextOpen:false,artifactsOpen:desk,stage:desk?1:0,tasks:[task('a'),task('b'),task('c')],document:{activeElement:{}},
  taskTransition:{cancel:noop,exit:async()=>true,enter:noop},workspaceModeMotion:{stop:noop,play:noop},assistantOpen:false,
  captureDraft:noop,stopWatching:noop,saveComposerDraft:noop,restoreComposerDraft:noop,closeTasks:noop,remember:noop,render:noop,resetScroll:noop,watch:noop,
  artifactExport:{updated:noop},restoreRememberedReport:noop,
  focusWorkspaceControl:noop,setContext:noop,notice:value=>writes.push(['notice',value]),
  $:id=>{if(!elements.has(id))elements.set(id,{inert:false,setAttribute:noop,removeAttribute:noop,focus:noop});return elements.get(id);},
  savedDeskStage:()=>saved[context.selected?.id]??0,
  rememberDeskStage:()=>{writes.push(['stage',context.selected?.id,context.stage]);saved[context.selected?.id]=context.stage;},
  api:path=>{reads.push(path);return new Promise((resolve,reject)=>request={resolve,reject});}};
 vm.createContext(context);vm.runInContext([labels,permission,selection,navigation].join('\n'),context);
 return {context,saved,writes,reads,resolve:value=>request.resolve(value),reject:reason=>request.reject(reason)};
}
test('switching to Desk while the first task is loading keeps that exact task',async()=>{
 const f=fixture({selected:null}),c=f.context,promise=c.loadSelectedTask('b'),generation=c.taskSelectionGeneration;
 c.switchMode('desk');assert.equal(c.selectingTaskId,'b');assert.equal(c.taskSelectionGeneration,generation);
 f.resolve(task('b'));await promise;assert.equal(c.selected.id,'b');assert.equal(c.artifactsOpen,true);assert.equal(c.stage,2);assert.equal(f.reads.length,1);
});
test('a queued page choice belongs to the new task rather than the previous one',async()=>{
 const f=fixture({desk:true}),c=f.context,promise=c.loadSelectedTask('b');c.view(3);
 assert.equal(c.selected.id,'a');assert.deepEqual(f.writes,[]);f.resolve(task('b'));await promise;
 assert.equal(c.selected.id,'b');assert.equal(c.stage,3);assert.equal(f.saved.a,1);assert.equal(f.saved.b,3);
});
test('the last mode and page intent wins without another read',async()=>{
 const f=fixture({desk:true}),c=f.context,promise=c.loadSelectedTask('b');c.view(4);c.switchMode('chat');c.switchMode('desk');
 f.resolve(task('b'));await promise;assert.equal(c.selected.id,'b');assert.equal(c.artifactsOpen,true);assert.equal(c.stage,4);assert.equal(f.reads.length,1);
});
test('a later explicit task choice discards the earlier queued navigation',async()=>{
 const f=fixture({desk:true}),c=f.context,first=c.loadSelectedTask('b');c.view(4);const resolveFirst=f.resolve;
 // Retain the first actual promise resolver before selecting the other task.
 const firstRead=f.context.api;let completeSecond;
 f.context.api=()=>new Promise(resolve=>completeSecond=resolve);
 const second=c.loadSelectedTask('c');resolveFirst(task('b'));await first;completeSecond(task('c'));await second;
 assert.equal(c.selected.id,'c');assert.equal(c.stage,0);assert.equal(c.pendingTaskView,null);assert.equal(f.saved.a,1);f.context.api=firstRead;
});
test('explicit cancellation discards both a late reply and the queued page',async()=>{
 const f=fixture(),c=f.context,promise=c.loadSelectedTask('b');c.view(3);c.cancelTaskSelection();f.resolve(task('b'));await promise;
 assert.equal(c.selected.id,'a');assert.equal(c.pendingTaskView,null);assert.equal(c.selectingTaskId,'');
});
test('a failed read does not apply the pending page to the previous task',async()=>{
 const f=fixture({desk:true}),c=f.context,promise=c.loadSelectedTask('b');c.view(3);f.reject(Error('controlled read failure'));await promise;
 assert.equal(c.selected.id,'a');assert.equal(c.stage,1);assert.equal(f.saved.a,1);assert.equal(c.pendingTaskView,null);assert.equal(c.selectingTaskId,'');
});
test('a sales task change never queues internal Desk navigation',async()=>{
 const f=fixture({role:'sales'}),c=f.context,promise=c.loadSelectedTask('b');
 c.switchMode('desk');c.view(3);assert.equal(c.pendingTaskView,null);
 f.resolve(task('b'));await promise;
 assert.equal(c.selected.id,'b');assert.equal(c.artifactsOpen,false);assert.deepEqual(f.writes,[]);
});

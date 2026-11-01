/* OH installTaskSearch DOM and motion, adapted to RH's existing task catalog.
 * No Store, provider, engine or native protocol is owned by this component. */
(()=>{
  'use strict';
  function createTaskSearch({entries,select,canOpen,canSelect,beforeOpen,restoreFocus}){
    const dialog=document.createElement('dialog');
    dialog.className='task-search-dialog';dialog.setAttribute('aria-label','搜索任务');
    const input=document.createElement('input');
    input.type='search';input.placeholder='搜索任务或报告';input.setAttribute('aria-label','搜索任务');
    const close=document.createElement('button');
    close.type='button';close.textContent='×';close.setAttribute('aria-label','关闭搜索');
    const header=document.createElement('header');header.append(input,close);
    const list=document.createElement('div');
    const feedback=document.createElement('p');feedback.setAttribute('role','status');feedback.hidden=true;
    dialog.append(header,list,feedback);document.body.append(dialog);
    const trigger=document.createElement('button');
    trigger.type='button';trigger.className='rail-task-search';
    trigger.setAttribute('aria-label','搜索任务');trigger.title='搜索任务';
    trigger.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>';
    let destroyed=false,exiting=false,origin=null,animation=null,restoreOnClose=true,snapshot='';
    let composing=false,imeEscape=false;
    const isComposition=event=>composing||event.isComposing||event.keyCode===229;
    function animate(kind,done){
      animation?.cancel();animation=null;
      if(matchMedia('(prefers-reduced-motion: reduce)').matches||!dialog.animate||!dialog.isConnected){done?.();return}
      const exit=kind==='exit';
      animation=dialog.animate(exit?[{opacity:1,transform:'scale(1)'},{opacity:0,transform:'scale(.99)'}]:[{opacity:0,transform:'translateY(4px) scale(.97)'},{opacity:1,transform:'none'}],{duration:exit?150:250,easing:'cubic-bezier(.22,1,.36,1)'});
      const current=animation;
      const complete=()=>{if(destroyed||animation!==current)return;animation=null;done?.()};
      current.onfinish=complete;
      // WebKit may defer finish events while its window is hidden. The real
      // completion promise settles independently; identity guards prevent
      // double completion or an old animation closing a new surface.
      current.finished?.then(complete,()=>{});
    }
    function finishClose(restore){
      animation?.cancel();animation=null;restoreOnClose=false;
      composing=false;imeEscape=false;
      dialog.close();dialog.inert=false;exiting=false;
      // Own the close-to-focus transition synchronously. A queued native close
      // event must not steal focus from a newly opened surface or selection.
      if(restore&&!destroyed&&!document.querySelector('dialog[open]'))restoreFocus(origin);
    }
    function dismiss(){
      if(!dialog.open||exiting)return;
      exiting=true;dialog.inert=true;animate('exit',()=>finishClose(true));
    }
    function render(){
      const query=input.value.trim().toLocaleLowerCase();
      const rows=entries(query),disabled=!canSelect();
      const next=JSON.stringify([query,disabled,rows]);
      if(next===snapshot)return;
      const focused=list.contains(document.activeElement)?{kind:document.activeElement.dataset.kind,id:document.activeElement.dataset.id}:null;
      list.replaceChildren();feedback.hidden=true;snapshot=next;
      for(const row of rows){
        const button=document.createElement('button');button.type='button';button.textContent=row.title;
        button.dataset.kind=row.kind;button.dataset.id=row.id;button.disabled=disabled;
        button.onclick=()=>{
          // Re-resolve through RH's catalog at activation: a result is not a
          // retained authorization to switch after a task begins saving.
          if(!canSelect()){feedback.textContent='正在保存研究，请稍候';feedback.hidden=false;return}
          if(!select(row)){render();feedback.textContent='任务已变化，请重新选择';feedback.hidden=false;return}
          finishClose(false);restoreFocus(null);
        };
        list.append(button);
      }
      if(!rows.length){const empty=document.createElement('p');empty.textContent=query?'未找到任务或报告':'尚无任务或报告';list.append(empty)}
      if(!canSelect()){feedback.textContent='正在保存研究，请稍候';feedback.hidden=false}
      if(focused){
        const target=[...list.children].find(button=>!button.disabled&&button.dataset.kind===focused.kind&&button.dataset.id===focused.id);
        (target||input).focus({preventScroll:true});
      }
    }
    function open(){
      if(destroyed||!canOpen()||exiting)return;
      if(document.querySelector('dialog[open]')!==dialog&&document.querySelector('dialog[open]'))return;
      if(!dialog.open){origin=document.activeElement;beforeOpen();input.value='';composing=false;imeEscape=false;restoreOnClose=true;dialog.showModal();animate('enter')}
      render();input.focus({preventScroll:true});
    }
    function shortcut(event){
      if(!event.defaultPrevented&&!isComposition(event)&&(event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){
        if(!canOpen()||document.querySelector('dialog[open]')&& !dialog.open)return;
        event.preventDefault();open();
      }
    }
    input.oninput=render;
    input.addEventListener('compositionstart',()=>{composing=true});
    input.addEventListener('compositionend',()=>{composing=false});
    input.onkeydown=event=>{
      if(isComposition(event))return;
      if(event.key==='ArrowDown'||event.key==='Enter'){event.preventDefault();list.querySelector('button:not(:disabled)')?.focus()}
    };
    list.onkeydown=event=>{
      if(isComposition(event))return;
      if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
      const buttons=[...list.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
      if(index<0)return;event.preventDefault();
      if(event.key==='ArrowUp'&&index===0){input.focus();return}
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)));
      buttons[next]?.focus();
    };
    close.onclick=dismiss;trigger.onclick=open;
    // Search fields consume Escape to clear text on some WebKit/Chrome builds;
    // the modal must close consistently regardless of a nonempty query.
    dialog.addEventListener('keydown',event=>{
      if(event.key==='Escape'){
        // Safari can end composition before this keydown and report229.
        // Keep a following native dialog-cancel from closing the search;
        // the next ordinary Escape remains an explicit dismiss action.
        imeEscape=isComposition(event);
        if(imeEscape)return;
        event.preventDefault();dismiss();return;
      }
      if(isComposition(event))return;
      imeEscape=false;
      if(event.key!=='Tab'||exiting)return;
      const controls=[input,close,...list.querySelectorAll('button:not(:disabled)')],first=controls[0],last=controls.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
    });
    dialog.addEventListener('keyup',event=>{if(event.key==='Escape')imeEscape=false});
    dialog.addEventListener('cancel',event=>{event.preventDefault();if(!composing&&!imeEscape)dismiss()});
    dialog.addEventListener('close',()=>{
      if(destroyed||!restoreOnClose||dialog.open||document.querySelector('dialog[open]'))return;
      restoreFocus(origin);
    });
    window.addEventListener('researchhelper:search-tasks',open);
    document.addEventListener('keydown',shortcut);
    return Object.freeze({
      mount(host){if(host&&!destroyed)host.append(trigger)},
      refresh(){if(dialog.open&&!exiting)render()},
      destroy(){
        if(destroyed)return;destroyed=true;composing=false;imeEscape=false;animation?.cancel();
        document.removeEventListener('keydown',shortcut);window.removeEventListener('researchhelper:search-tasks',open);
        dialog.close();dialog.remove();trigger.remove();origin=null;
      },
    });
  }
  window.RHTaskSearch=Object.freeze({createTaskSearch});
})();

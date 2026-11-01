/* OH createConversationMessage copy feedback and setMessageActionIcon port.
 * RH keeps existing rendered-body text. Copy is local presentation only;
 * lifecycle ownership prevents a late fallback from stealing another task's focus. */
(function(global){
  'use strict';
  const copyPath='M9 9h12v12H9ZM5 15H3V3h12v2',successPath='m5 12 4 4L19 6';
  function setMessageActionIcon(control,label,path){
    control.setAttribute('aria-label',label);control.title=label;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [name,value] of Object.entries({viewBox:'0 0 24 24','aria-hidden':'true',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round'}))svg.setAttribute(name,value);
    const shape=document.createElementNS('http://www.w3.org/2000/svg','path');shape.setAttribute('d',path);svg.append(shape);control.replaceChildren(svg);
  }
  function createMessageActions({canRun=()=>true,restoreFocus=button=>button.focus({preventScroll:true})}={}){
    const states=new WeakMap(),active=new Set();let destroyed=false;
    const owned=(button,state)=>!destroyed&&state.valid&&canRun()&&button.isConnected&&states.get(button)===state;
    const allowedFallback=(button,state)=>{
      if(!owned(button,state)||button.closest('[inert]')||!button.getClientRects().length)return false;
      const modal=document.querySelector('dialog[open]');return !modal||modal.contains(button);
    };
    function reset(button,state){
      clearTimeout(state.timer);state.timer=0;state.busy=false;button.dataset.busy='false';button.dataset.copied='false';button.removeAttribute('aria-busy');
      setMessageActionIcon(button,state.label,copyPath);state.status.textContent='';
    }
    function prune(){
      for(const state of active){if(!state.button.isConnected){state.valid=false;clearTimeout(state.timer);active.delete(state)}}
    }
    async function copy(button){
      prune();if(destroyed||!canRun()||!button?.isConnected)return;
      const message=button.closest('.message'),body=message?.querySelector('.message__body'),status=message?.querySelector('.message__copy-status');
      if(!body||!status)return;
      let state=states.get(button);
      if(state?.busy)return;
      if(!state){state={button,status,label:button.dataset.copyLabel||(message.classList.contains('message--user')?'复制我的消息':'复制文字'),valid:true,busy:false,timer:0};states.set(button,state)}
      const text=body.innerText;if(!text.trim())return;
      reset(button,state);state.busy=true;button.dataset.busy='true';button.setAttribute('aria-busy','true');active.add(state);
      let copied=false;
      try{
        try{await navigator.clipboard.writeText(text);copied=true}
        catch{
          // A pending clipboard denial does not authorize a fallback after the
          // user has left this message or opened a protected modal.
          if(!allowedFallback(button,state))return;
          const field=document.createElement('textarea');field.value=text;field.setAttribute('readonly','');field.setAttribute('aria-hidden','true');field.tabIndex=-1;
          field.style.position='fixed';field.style.opacity='0';document.body.append(field);
          try{field.select();copied=document.execCommand('copy')===true}catch{copied=false}
          finally{field.remove();if(allowedFallback(button,state)&&(!document.activeElement||document.activeElement===document.body))restoreFocus(button)}
        }
        if(!owned(button,state))return;
        if(!copied){setMessageActionIcon(button,'复制失败，请重试',copyPath);status.textContent='复制失败，请重试';return}
        setMessageActionIcon(button,'已复制',successPath);status.textContent='已复制';button.dataset.copied='true';
        state.timer=setTimeout(()=>{if(owned(button,state))reset(button,state);active.delete(state)},1000);
      }finally{
        state.busy=false;if(button.isConnected&&!destroyed){button.dataset.busy='false';button.removeAttribute('aria-busy')}
        if(!state.timer)active.delete(state);
      }
    }
    return {copy,prune,destroy(){
      if(destroyed)return;destroyed=true;
      for(const state of active){state.valid=false;if(state.button.isConnected)reset(state.button,state);else clearTimeout(state.timer)}active.clear();
    }};
  }
  global.RHMessageActions={createMessageActions};
})(window);

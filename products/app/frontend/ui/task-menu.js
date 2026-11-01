/* OH task-menu DOM, portal and keyboard patterns; RH callbacks own persistence.
 * No task bodies, accounts, provider configuration or report content live here. */
(() => {
  'use strict';
  let opened = null;
  const scale = () => parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const icon = kind => {
    const paths = {
      more:'<circle cx="8" cy="3.4" r="1.15"/><circle cx="8" cy="8" r="1.15"/><circle cx="8" cy="12.6" r="1.15"/>',
      pin:'<path d="M5 2.5h6M6 2.5v4l-2 2v1h8v-1l-2-2v-4M8 9.5v4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
      edit:'<path d="M9.94076 1.34942C10.7047 0.90231 11.6503 0.902415 12.4143 1.34942C12.7061 1.52015 12.9688 1.79118 13.3104 2.13284C13.6521 2.47448 13.9231 2.73721 14.0939 3.02894C14.5408 3.79294 14.5409 4.73856 14.0939 5.50251C13.9231 5.79415 13.652 6.05704 13.3104 6.39861L6.65932 13.0497C6.28068 13.4284 6.00695 13.7108 5.66543 13.9097C5.32391 14.1085 4.94315 14.2074 4.42705 14.3498L3.24394 14.6761C2.77527 14.8054 2.34538 14.9262 2.00131 14.9684C1.65196 15.0112 1.17964 15.0013 0.810764 14.6325C0.441921 14.2637 0.432107 13.7913 0.47486 13.442C0.517035 13.0979 0.6379 12.668 0.767181 12.1993L1.09352 11.0162C1.23588 10.5001 1.33481 10.1193 1.5336 9.77784C1.7325 9.43632 2.0149 9.1626 2.39355 8.78395L9.04466 2.13284C9.38625 1.79126 9.64911 1.52016 9.94076 1.34942ZM15.5427 14.8398H7.55223L8.96707 13.425H15.5427V14.8398ZM3.39382 9.78422C2.965 10.213 2.84244 10.3436 2.75709 10.49C2.67183 10.6366 2.61862 10.8079 2.45733 11.3925L2.13099 12.5756C2.00183 13.0439 1.92194 13.3419 1.88863 13.5536C2.10041 13.5204 2.39872 13.4416 2.86764 13.3123L4.05075 12.9859C4.63544 12.8246 4.80669 12.7715 4.95323 12.6862C5.09968 12.6008 5.23022 12.4783 5.65905 12.0494L10.721 6.98644L8.45577 4.72121L3.39382 9.78422ZM11.7 2.57079C11.3774 2.38198 10.9777 2.38198 10.6551 2.57079C10.5602 2.62647 10.4487 2.72931 10.0449 3.13311L9.45604 3.72094L11.7213 5.98617L12.3102 5.39833C12.7139 4.99457 12.8168 4.88307 12.8725 4.78818C13.0613 4.46561 13.0612 4.06585 12.8725 3.74326C12.8169 3.64827 12.7146 3.53752 12.3102 3.13311C11.9057 2.72863 11.795 2.6264 11.7 2.57079Z"/>',
      archive:'<path d="M1.5 1.5h13v4h-1v9h-11v-9h-1zm1.3 1.3v1.4h10.4V2.8zm1 2.7v7.7h8.4V5.5zM6 7h4v1.3H6z"/>',
    };
    return '<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'+paths[kind]+'</svg>';
  };
  const close = (restoreFocus = false) => {
    if (!opened) return;
    const {menu,trigger,row}=opened;opened=null;
    menu.hidden=true;menu.removeAttribute('style');row.append(menu);trigger.setAttribute('aria-expanded','false');
    if (restoreFocus&&trigger.isConnected&&!trigger.closest('[hidden],[inert]'))trigger.focus({preventScroll:true});
  };
  const position = () => {
    if (!opened)return;
    if (!opened.trigger.isConnected||opened.trigger.closest('[hidden],[inert]')||!opened.trigger.getClientRects().length){close();return}
    const {menu,trigger}=opened,unit=scale(),r=trigger.getBoundingClientRect();
    const width=menu.offsetWidth,height=menu.offsetHeight,viewWidth=innerWidth/unit,viewHeight=innerHeight/unit,margin=12;
    const left=Math.max(margin,Math.min(r.right/unit-width,viewWidth-width-margin));
    const below=viewHeight-r.bottom/unit>=height+4||r.top/unit<height+4;
    const top=below?r.bottom/unit+4:r.top/unit-height-4;
    menu.style.left=left+'px';menu.style.top=Math.max(margin,Math.min(top,viewHeight-height-margin))+'px';
  };
  const focusItem = position => {
    const items=[...opened.menu.querySelectorAll('[role=menuitem]:not([disabled])')],index=items.indexOf(document.activeElement);
    (position==='first'?items[0]:position==='last'?items.at(-1):items[(index+position+items.length)%items.length])?.focus();
  };
  document.addEventListener('pointerdown',event=>{if(opened&&!opened.row.contains(event.target)&&!opened.menu.contains(event.target))close()});
  document.addEventListener('focusin',event=>{if(opened&&event.target!==opened.trigger&&!opened.menu.contains(event.target))close()});
  document.addEventListener('keydown',event=>{
    if(!opened||event.defaultPrevented)return;
    if(event.key==='Escape'){event.preventDefault();close(true)}
    else if(event.key==='Tab'){close(true)}
    else if(opened.menu.contains(event.target)&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
      event.preventDefault();focusItem(event.key==='Home'?'first':event.key==='End'?'last':event.key==='ArrowDown'?1:-1);
    }
  });
  document.addEventListener('scroll',position,true);window.addEventListener('resize',position);
  window.RHTaskMenu={close,
    mount(list,presentation,onAction,busy){
      close();
      list.querySelectorAll('.task-menu-trigger,.task-menu,.task-item__pin').forEach(element=>element.remove());
      list.querySelectorAll('.task-item[data-task]').forEach(button=>{
        const id=button.dataset.task,state=presentation(id),row=button.parentElement;
        row.dataset.hasTaskMenu='true';
        if(state.pinned){const pin=document.createElement('span');pin.className='task-item__pin';pin.setAttribute('role','img');pin.setAttribute('aria-label','已置顶');pin.innerHTML=icon('pin');button.querySelector('.task-item__title').append(pin)}
        const trigger=document.createElement('button');trigger.type='button';trigger.className='task-menu-trigger';trigger.dataset.menuTask=id;
        trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-label','管理任务：'+button.querySelector('.task-item__text').textContent);
        const menu=document.createElement('div');menu.id='rh-task-menu-'+id;menu.className='task-menu';menu.setAttribute('role','menu');menu.hidden=true;
        trigger.setAttribute('aria-controls',menu.id);trigger.innerHTML=icon('more');trigger.disabled=busy(id);
        for(const [action,label,glyph] of [['pin',state.pinned?'取消置顶':'置顶任务','pin'],['rename','重命名','edit'],[state.archived?'restore':'archive',state.archived?'恢复任务':'归档任务','archive']]){
          const item=document.createElement('button');item.type='button';item.className='task-menu__action';item.setAttribute('role','menuitem');item.tabIndex=-1;
          item.innerHTML='<span class="task-menu__icon">'+icon(glyph)+'</span><span class="task-menu__label"></span>';item.querySelector('.task-menu__label').textContent=label;
          item.dataset.menuAction=action;item.disabled=busy(id);
          item.addEventListener('click',event=>{event.stopPropagation();close(true);onAction(id,action)});menu.append(item);
        }
        const open=()=>{
          close();menu.hidden=false;
          // Keep the portal inside the mobile rail's focus boundary, not behind
          // the inert work area; desktop uses OH's body-level portal.
          (matchMedia('(max-width:700px)').matches?document.querySelector('#tasks'):document.body).append(menu);
          menu.style.position='fixed';trigger.setAttribute('aria-expanded','true');opened={menu,trigger,row};position();focusItem('first');
        };
        trigger.addEventListener('click',event=>{event.stopPropagation();if(opened?.trigger===trigger)close(true);else open()});
        trigger.addEventListener('keydown',event=>{
          if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
          event.preventDefault();if(opened?.trigger!==trigger)open();focusItem(['ArrowUp','End'].includes(event.key)?'last':'first');
        });
        row.append(trigger,menu);
      });
    },
  };
})();

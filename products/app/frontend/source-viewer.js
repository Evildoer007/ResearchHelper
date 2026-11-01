/* Read retained originals without creating evidence, network requests or model calls. */
(() => {
  'use strict';
  const SEGMENT_SIZE = 8000;
  const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

  function materialOf(record) {
    if (record.kind === 'filing') return record.snapshot?.material;
    if (record.kind === 'material' || Array.isArray(record.parts)) return record.snapshot || record;
    return null;
  }

  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const rawText = value => value === undefined ? '未提供' : value === null ? 'null' :
    typeof value === 'object' ? (Array.isArray(value) ? '非标量：数组，共'+value.length+'项，内容未展开' : '非标量：对象，内容未展开') : String(value);

  function officialCatalogue(container,record,version,relatedRecords) {
    const entries=record.snapshot?.entries;
    const boundary='<p class="source-boundary">目录用于定位原文，不代表证据已核验。</p>';
    if(!Array.isArray(entries)){
      container.innerHTML=boundary+'<p role="status">目录结构异常，未展示条目。请核对留存快照。</p>'+metadata(record,version);
      return;
    }
    const rows=entries.slice(0,100).filter(entry=>isObject(entry)&&typeof entry.id==='string'&&typeof entry.title==='string'&&entry.title);
    const records=Array.isArray(relatedRecords)?relatedRecords:[];
    const originalFor=entry=>{
      if(typeof record.task_id!=='string'||record.research_version!==version)return null;
      return records.find(item=>item.source==='official'&&item.operation==='release_original'&&item.kind==='material'&&
        item.task_id===record.task_id&&item.research_version===version&&item.parameters?.catalog_id===record.id&&
        item.parameters?.release_id===entry.id&&item.snapshot?.official_origin?.id===entry.id&&
        item.snapshot?.status==='已解析'&&typeof item.id==='string'&&item.id);
    };
    container.innerHTML=boundary+'<form class="source-search"><label for="source-catalog-query" class="sr-only">查找目录标题</label>'+
      '<input id="source-catalog-query" type="search" maxlength="80" placeholder="查找目录标题" autocomplete="off"><button type="submit">查找</button></form>'+
      '<p class="source-catalog-feedback source-location" role="status" aria-live="polite"></p>'+
      '<div class="table-wrap" tabindex="0" aria-label="留存发布目录"><table class="source-catalog"><thead><tr><th scope="col">标题</th><th scope="col">原文</th></tr></thead><tbody></tbody></table></div>'+metadata(record,version);
    const input=container.querySelector('#source-catalog-query'),body=container.querySelector('tbody'),feedback=container.querySelector('.source-catalog-feedback');
    function render(){
      const query=input.value.trim().toLocaleLowerCase(),filtered=rows.filter(entry=>entry.title.toLocaleLowerCase().includes(query));
      body.innerHTML=filtered.map(entry=>{
        const original=originalFor(entry);
        return '<tr><th scope="row">'+escapeHTML(entry.title)+'</th><td>'+(original?
          '<button type="button" class="text" data-source="'+escapeHTML(original.id)+'" aria-label="查看原文：'+escapeHTML(entry.title)+'">查看原文</button>':'尚未下载')+'</td></tr>';
      }).join('');
      feedback.textContent=!rows.length?'本页未留存目录条目，不代表没有相关发布。':!filtered.length?'未找到匹配标题，请调整关键词。':
        '显示'+filtered.length+'/'+rows.length+'条，目录覆盖未核验。';
      if(entries.length>100)feedback.textContent+='仅展示前100条。';
      if(rows.length!==Math.min(entries.length,100))feedback.textContent+='部分条目结构异常，未展示。';
    }
    container.querySelector('form').addEventListener('submit',event=>{event.preventDefault();render()});
    input.addEventListener('input',render);
    render();
  }

  function obtainedTime(value){
    if(typeof value!=='string'||!value)return '未记录';
    // A display conversion only: keep the exact receipt in datetime/title.
    // Date-only, unzoned and malformed legacy values remain literal.
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value))return escapeHTML(value);
    const date=new Date(value),calendar=new Date(value.slice(0,10)+'T00:00:00Z');
    if(!Number.isFinite(date.getTime())||!Number.isFinite(calendar.getTime())||calendar.toISOString().slice(0,10)!==value.slice(0,10))return escapeHTML(value);
    const display=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date);
    return '<time datetime="'+escapeHTML(value)+'" title="原始获取时点：'+escapeHTML(value)+'">'+escapeHTML(display)+'，北京时间</time>';
  }

  // Keep references to retained columns/rows; only the selected row becomes DOM.
  function apiTables(record) {
    const snapshot = record.snapshot;
    if (!isObject(snapshot)) return null;
    if (record.source === 'tinyshare') {
      if (!Array.isArray(snapshot.data)) return null;
      return [{name:'数据',length:snapshot.data.length,row:index => snapshot.data[index]}];
    }
    if (record.source !== 'ifind' || !Array.isArray(snapshot.tables)) return null;
    return snapshot.tables.map((table,index) => {
      const columns = isObject(table?.table) ? Object.entries(table.table) : null;
      const arrays = columns?.filter(([,value]) => Array.isArray(value)) || [];
      return {name:'表' + (index + 1) + (typeof table?.thscode === 'string' ? ' ' + table.thscode : ''),
        malformed:!columns || columns.some(([,value]) => !Array.isArray(value)) ||
          new Set(arrays.map(([,values])=>values.length)).size>1,
        length:arrays.reduce((length,[,values]) => Math.max(length,values.length),0),
        row:position => columns && Object.fromEntries([
          ...(Object.hasOwn(table,'thscode')?[['thscode',table.thscode]]:[]),
          ...(Object.hasOwn(table,'time')||Object.hasOwn(snapshot,'time')?
            [['time',Array.isArray(table.time??snapshot.time) ? (table.time??snapshot.time)[position] : (table.time??snapshot.time)]]:[]),
          ...columns.map(([name,values]) => [name,Array.isArray(values) ? values[position] : values])])};
    });
  }

  function apiStatus(record,tables) {
    const snapshot=record.snapshot;
    if (snapshot === undefined || snapshot === null) return '未保留原始快照，不能补造原值。';
    if (!isObject(snapshot)) return '返回结构异常：快照不是已登记的对象结构。';
    const code=record.source==='ifind'?snapshot.errorcode:snapshot.code;
    if ((typeof code === 'number'||typeof code === 'string') && code !== 0 && code !== '0')
      return '业务失败，具体原因待按接口定义核查。';
    if ((code !== undefined && typeof code !== 'number' && typeof code !== 'string') || !tables || tables.some(table=>table.malformed) ||
        (record.source==='tinyshare'&&snapshot.data.some(row=>!isObject(row))))
      return '返回结构异常，不能作为已核验数据。';
    if(record.status==='返回结构异常')return '响应范围或结构未通过核验，原始记录仅供核对。';
    if (!tables.some(table=>table.length)) return '接口返回为空，未保留数据行。';
    return code===undefined ? '业务返回码未提供，原值待核验。' : '业务成功，原值待核验。';
  }

  function financialQueryIssues(record){
    if(record.source!=='ifind'||record.operation!=='smart_stock_picking')return '';
    const contract=record.financial_query_contract;
    if(!isObject(contract))return '<p class="source-boundary source-boundary-issues">未保留字段匹配记录，不能确认所需指标已取得。</p>';
    if(contract.query_kind==='object_discovery'){
      const unsupported=Array.isArray(contract.unsupported_candidates)?contract.unsupported_candidates:[];
      const message=typeof contract.response_issue==='string'&&contract.response_issue?contract.response_issue:
        unsupported.length?'部分候选的市场暂不支持后续查询，原值仍可浏览。':'';
      return message?'<p class="source-boundary source-boundary-issues" role="status">'+escapeHTML(message)+'</p>':'';
    }
    const messages=[];
    if(typeof contract.response_issue==='string'&&contract.response_issue)messages.push(contract.response_issue);
    else if(!Array.isArray(contract.unmatched_fields)||!Array.isArray(contract.missing_values))
      messages.push('未保留完整字段匹配记录，不能确认所需指标已取得。');
    const names=key=>Array.isArray(contract[key])?contract[key].filter(value=>typeof value==='string'&&value):[];
    const unmatched=names('unmatched_fields'),missing=names('missing_values');
    if(unmatched.length)messages.push('未匹配指标：'+unmatched.join('、')+'。不能用其他口径替代。');
    if(missing.length)messages.push('空值指标：'+missing.join('、')+'。原值保留为空，不补零。');
    return messages.length?'<p class="source-boundary source-boundary-issues" role="status">'+messages.map(escapeHTML).join(' ')+'</p>':'';
  }

  function apiViewer(container,record,version,locator) {
    const tables=apiTables(record),snapshot=isObject(record.snapshot)?record.snapshot:{};
    const message=snapshot.errmsg??snapshot.msg;
    const hasMessage=message!==undefined&&message!==null&&(typeof message!=='string'||message.trim()!=='');
    let tableIndex=0,rowIndex=0;
    const hasLocator=locator && Object.keys(locator).length>0;
    let invalid='';
    if(hasLocator){
      tableIndex=record.source==='tinyshare' ? 0 : locator.table;
      rowIndex=locator.row;
      if (!Number.isInteger(tableIndex)||tableIndex<0||!tables?.[tableIndex]||
          !Number.isInteger(rowIndex)||rowIndex<0||rowIndex>=tables[tableIndex].length||
          (record.source==='tinyshare'&&locator.table!==undefined&&locator.table!==0)) invalid='来源定位无效：表或行不存在，未跳转到其他行。';
      else if(locator.field!==undefined && (typeof locator.field!=='string'||!Object.hasOwn(tables[tableIndex].row(rowIndex)||{},locator.field)))
        invalid='来源定位无效：指定字段不存在，未跳转到其他字段。';
    }
    const boundary=record.source==='ifind'&&record.operation==='edb_service'
      ? '宏观原始序列。指标含义、单位、首次公开时间与修订版本未核验，不用于计算或投资判断。'
      : record.source==='ifind'&&record.operation==='data_pool'
      ? '指数原始名单。成分资格、完整性、权重及历史可得性未核验。'
      : record.source==='ifind'&&record.operation==='smart_stock_picking'&&record.financial_query_contract?.query_kind==='object_discovery'
      ? '研究候选。行业归属和样本完整性未核验，后续需查原件。'
      : record.source==='ifind'&&record.operation==='smart_stock_picking'
      ? '财务原始记录。单位、币种、合并范围及历史版本未核验，不用于计算或投资判断。'
      : '原始接口快照，浏览不批准证据，也不改变研究结论。';
    container.innerHTML='<p class="source-boundary">'+escapeHTML(boundary)+'</p>'+
      financialQueryIssues(record)+
      '<dl class="source-api-summary"><dt>获取时间</dt><dd>'+obtainedTime(record.obtained_at)+'</dd><dt>业务状态</dt><dd>'+escapeHTML(apiStatus(record,tables))+
      '</dd>'+(hasMessage?'<dt>接口消息</dt><dd>'+escapeHTML(rawText(message))+'</dd>':'')+'</dl>'+
      (record.source==='tinyshare'&&['income','cashflow'].includes(record.operation)?'<p class="source-boundary">单位、币种及历史版本未核验；报告期不等于公告日。此处仅展示原值，不作计算。</p>':'')+
      '<p class="source-api-feedback" role="status" aria-live="polite">'+escapeHTML(invalid)+'</p>'+
      (invalid&&tables?.some(table=>table.length)?'<button type="button" data-source-browse>浏览留存记录</button>':'')+
      (!invalid&&tables?.length?'<div class="source-navigation source-api-navigation">'+
      (tables.length>1?'<label for="source-table">表<select id="source-table" data-choice aria-label="选择原始表">'+tables.map((table,index)=>'<option value="'+index+'">'+escapeHTML(table.name)+'</option>').join('')+'</select></label>':'')+
      '<button type="button" data-source-row="previous">上一行</button><label for="source-row">行<input id="source-row" type="number" min="1" inputmode="numeric" aria-describedby="source-api-location"></label><button type="button" data-source-row="next">下一行</button></div><p id="source-api-location" class="source-location" aria-live="polite"></p><div class="source-api-fields"></div>':'')+metadata(record,version);
    if(invalid){container.querySelector('[data-source-browse]')?.addEventListener('click',()=>{
      const firstTable=tables.findIndex(table=>table.length);
      apiViewer(container,record,version,{table:firstTable,row:0});
    });return;}
    if(!tables?.length)return;
    const input=container.querySelector('#source-row'),select=container.querySelector('#source-table'),feedback=container.querySelector('.source-api-feedback');
    function render(){
      const table=tables[tableIndex],cells=table.length?table.row(rowIndex):null;
      if(select)select.value=String(tableIndex);
      input.value=table.length?String(rowIndex+1):'';input.max=String(table.length);input.disabled=!table.length;input.removeAttribute('aria-invalid');
      container.querySelector('#source-api-location').textContent='表'+(tableIndex+1)+'/'+tables.length+'，'+(table.length?'行'+(rowIndex+1)+'/'+table.length:'无数据行');
      container.querySelector('[data-source-row="previous"]').disabled=rowIndex===0||!table.length;
      container.querySelector('[data-source-row="next"]').disabled=rowIndex+1>=table.length;
      const fields=container.querySelector('.source-api-fields');
      fields.innerHTML=!table.length?'<p>当前表为空，未返回数据行。</p>':!isObject(cells)?'<p>返回结构异常：当前行不是字段对象。</p>':
        '<div class="table-wrap" tabindex="0" aria-label="原始接口字段"><table><thead><tr><th scope="col">字段</th><th scope="col">原值</th></tr></thead><tbody>'+Object.entries(cells).map(([name,value])=>{
          const focused=hasLocator&&tableIndex===(record.source==='tinyshare'?0:locator.table)&&rowIndex===locator.row&&name===locator.field;
          const suppliedLabel=record.source==='ifind'&&record.operation==='data_pool'?record.snapshot?.outParams?.[name]:null;
          const label=typeof suppliedLabel==='string'&&suppliedLabel.trim()?suppliedLabel:name;
          return '<tr'+(focused?' class="source-field-target" aria-label="证据定位字段"':'')+'><th scope="row" title="'+escapeHTML(name)+'">'+escapeHTML(label)+(focused?'（定位字段）':'')+'</th><td>'+escapeHTML(rawText(value))+'</td></tr>';
        }).join('')+'</tbody></table></div>';
    }
    container.querySelectorAll('[data-source-row]').forEach(button=>button.addEventListener('click',()=>{rowIndex+=button.dataset.sourceRow==='next'?1:-1;feedback.textContent='';render()}));
    input.addEventListener('change',()=>{const value=Number(input.value);if(!Number.isInteger(value)||value<1||value>tables[tableIndex].length){input.setAttribute('aria-invalid','true');feedback.textContent='请输入1至'+tables[tableIndex].length+'的行号。';return}rowIndex=value-1;feedback.textContent='';render()});
    select?.addEventListener('change',()=>{tableIndex=Number(select.value);rowIndex=0;feedback.textContent='';render()});
    render();
    window.RHChoice?.enhanceSelects(container);
    container.querySelector('.source-field-target')?.scrollIntoView({block:'nearest'});
  }

  function metadata(record, version) {
    const snapshot=isObject(record.snapshot)?record.snapshot:{};
    const code=record.source==='ifind'?snapshot.errorcode:snapshot.code;
    return '<details class="source-metadata"><summary>内容版本与指纹</summary>' +
      '<dl><dt>研究版本</dt><dd>' + escapeHTML(version) + '</dd>' +
      (code!==undefined?'<dt>业务返回码</dt><dd>'+escapeHTML(rawText(code))+'</dd>':'')+
      '<dt>内容版本</dt><dd>' + escapeHTML(record.content_version ?? '未记录') + '</dd>' +
      '<dt>内容指纹</dt><dd>' + escapeHTML(record.snapshot_hash || record.hash || '未记录') + '</dd>' +
      '<dt>请求指纹</dt><dd>'+escapeHTML(record.request_hash||'未记录')+'</dd>'+
      '<dt>请求参数</dt><dd>'+escapeHTML(record.parameters===undefined?'未记录':JSON.stringify(record.parameters))+'</dd></dl></details>';
  }

  function identitySource(record) {
    const snapshot=record.snapshot||{},params=record.parameters||{};
    const mode=params.functionpara?.mode;
    const rows=Array.isArray(snapshot.tables)?snapshot.tables:[];
    let shown=0,omitted=0;
    const entries=rows.map((row,index)=>{
      const values=row?.table?.thscode;
      if(!Array.isArray(values))return '';
      return values.map((value,position)=>{
        if(shown>=100){omitted++;return ''}
        shown++;
        return '<tr><td>'+escapeHTML(index+1)+'.'+escapeHTML(position+1)+'</td><td>'+escapeHTML(row?.[mode]??'未提供')+
          '</td><td>'+escapeHTML(typeof value==='string'?value:'非明确代码，未采用')+'</td></tr>';
      }).join('');
    }).join('');
    return '<dl><dt>查询方式</dt><dd>'+escapeHTML(mode==='secname'?'证券名称':mode==='seccode'?'证券号码':'未登记')+
      '</dd><dt>原始查询</dt><dd>'+escapeHTML(params[mode]??'未提供')+'</dd><dt>响应匹配模式</dt><dd>'+escapeHTML(
        snapshot.inputParams?.isexact===1?'精确匹配':snapshot.inputParams?.isexact===0?'模糊匹配':'未确认')+
      '</dd><dt>业务返回码</dt><dd>'+escapeHTML(typeof snapshot.errorcode==='number'||typeof snapshot.errorcode==='string'?snapshot.errorcode:'未提供')+'</dd></dl>'+
      (entries?'<div class="table-wrap" tabindex="0" aria-label="代码转换原始响应"><table><thead><tr><th>原始位置</th><th>响应查询值</th><th>证券代码原值</th></tr></thead><tbody>'+entries+'</tbody></table></div>':
       '<p>未返回已登记格式的代码行，不能补造映射。</p>')+
      (omitted?'<p>仅展示前100项，另有'+escapeHTML(omitted)+'项未展示；不代表完整审阅。</p>':'');
  }

  function calendarSource(container, record, version) {
    const contract=record.calendar_contract,snapshot=record.snapshot;
    const boundary='<p class="source-boundary">iFinD交易日历原始日期。浏览不批准金融证据，不证明个券成交或历史当时可得。</p>';
    const dates=contract?.dates,paths=contract?.column_paths;
    const column=path=>Array.isArray(path)&&path.length?path.reduce((value,key)=>
      value!==null&&typeof value==='object'&&Object.hasOwn(value,key)?value[key]:undefined,snapshot):undefined;
    const valid=contract?.version===1&&contract.raw_only===true&&contract.financial_approval===false&&
      snapshot?.errorcode===0&&Array.isArray(dates)&&dates.every(day=>typeof day==='string')&&
      Array.isArray(paths)&&paths.length>0&&paths.every(path=>{
        const raw=column(path);return Array.isArray(raw)&&raw.length===dates.length&&raw.every((day,index)=>day===dates[index]);
      })&&(dates.length>0||contract.explicit_empty===true);
    if(!valid){
      container.innerHTML=boundary+'<p role="status">日历日期与留存解析不一致或尚未识别，未补造清单。</p>'+metadata(record,version);
      return;
    }
    const exchange={SSE:'上交所',SZSE:'深交所'}[contract.exchange]||contract.exchange||'未识别市场';
    container.innerHTML=boundary+'<dl class="source-api-summary"><dt>市场</dt><dd>'+escapeHTML(exchange)+
      '</dd><dt>查询窗口</dt><dd>'+escapeHTML(record.parameters?.startdate)+'至'+escapeHTML(record.parameters?.enddate)+
      '</dd><dt>返回日期</dt><dd>'+dates.length+'项</dd></dl>'+
      (dates.length?'<div class="source-navigation"><button type="button" data-calendar-page="previous">上一页</button><p class="source-location" role="status" aria-live="polite"></p><button type="button" data-calendar-page="next">下一页</button></div>'+
      '<div class="table-wrap" tabindex="0" aria-label="交易日历原始日期"><table><thead><tr><th scope="col">原始行</th><th scope="col">日期</th></tr></thead><tbody></tbody></table></div>':
      '<p role="status">该窗口明确返回空日期清单。</p>')+metadata(record,version);
    if(!dates.length)return;
    let page=0;const pageSize=50,previous=container.querySelector('[data-calendar-page=previous]'),next=container.querySelector('[data-calendar-page=next]');
    const render=()=>{
      const start=page*pageSize,end=Math.min(start+pageSize,dates.length);
      container.querySelector('tbody').innerHTML=dates.slice(start,end).map((day,index)=>
        '<tr><th scope="row">'+(start+index+1)+'</th><td>'+escapeHTML(day)+'</td></tr>').join('');
      container.querySelector('.source-location').textContent=(start+1)+'至'+end+'项，共'+dates.length+'项';
      previous.disabled=page===0;next.disabled=end===dates.length;
    };
    previous.addEventListener('click',()=>{page--;render()});next.addEventListener('click',()=>{page++;render()});
    render();
  }

  function mount(container, {record, version, locator = {}, quote = '', relatedRecords = []}) {
    if(record.source==='ifind'&&record.operation==='get_trade_dates'){
      calendarSource(container,record,version);
      return;
    }
    if(record.source==='official'&&record.operation==='release_catalog'){
      officialCatalogue(container,record,version,relatedRecords);
      return;
    }
    const material = materialOf(record);
    if (!material) {
      if(record.source==='ifind'&&record.operation==='get_thscode'){
        container.innerHTML='<p class="source-boundary">代码转换原始响应，浏览不批准证据或历史可得性。</p>'+identitySource(record)+metadata(record,version);
        return;
      }
      apiViewer(container,record,version,locator);
      return;
    }
    const parts = Array.isArray(material.parts) ? material.parts : [];
    if (!parts.length || !parts.some(part => typeof part.text === 'string' && part.text.trim())) {
      container.innerHTML = '<p>' + escapeHTML(material.limitation || '原件未提取到正文，不能生成摘录。') + '</p>' + metadata(record, version);
      return;
    }
    let partIndex = Math.max(0, parts.findIndex(part => part.locator === locator.part));
    let start = 0, query = '', hitPart = -1, hitOffset = -1;
    const initialOffset = quote ? parts[partIndex].text.indexOf(quote) : -1;
    if (initialOffset >= 0) {
      start = Math.max(0,initialOffset - 400);
      hitPart = partIndex; hitOffset = initialOffset;
    }
    const boundary = String(material.extract_version || '').startsWith('html-')
      ? '静态HTML原文，未执行脚本。动态内容与显示状态未核验。'
      : '原件解析文本，浏览不等于已核验。图片与版面未包含在文本中。';
    const layoutLimitation=parts.some(part=>part.pdf_layout_limited)&&typeof material.limitation==='string'?material.limitation:'';
    container.innerHTML = '<p class="source-boundary">' + escapeHTML(boundary+layoutLimitation) + '</p>' +
      '<form class="source-search"><label class="sr-only" for="source-query">检索原件正文</label>' +
      '<input id="source-query" type="search" maxlength="80" placeholder="检索原文" autocomplete="off">' +
      '<button type="submit">查找</button><button type="button" data-source-find="previous" disabled>上一处</button>' +
      '<button type="button" data-source-find="next" disabled>下一处</button></form>' +
      '<p id="source-search-result" class="source-search-result" role="status" aria-live="polite"></p>' +
      '<div class="source-navigation"><button type="button" data-source-page="previous">上一段</button>' +
      '<label for="source-position">位置<input id="source-position" type="number" min="1" max="' + parts.length +
      '" inputmode="numeric" aria-describedby="source-location"></label>' +
      '<button type="button" data-source-page="next">下一段</button></div>' +
      '<p id="source-location" class="source-location" aria-live="polite"></p>' +
      '<div class="source-text" tabindex="0" aria-label="原件正文"></div>' + metadata(record, version);
    const textNode = container.querySelector('.source-text');
    const locationNode = container.querySelector('#source-location');
    const resultNode = container.querySelector('#source-search-result');
    const position = container.querySelector('#source-position');
    const input = container.querySelector('#source-query');
    const textOf = index => String(parts[index].text ?? '');

    function render() {
      const text = textOf(partIndex), end = Math.min(start + SEGMENT_SIZE, text.length);
      const excerpt = text.slice(start, end);
      const localHit = partIndex === hitPart ? hitOffset - start : -1;
      const needle = query || quote;
      if (needle && localHit >= 0 && localHit < excerpt.length) {
        const markedEnd = Math.min(localHit + needle.length, excerpt.length);
        textNode.innerHTML = escapeHTML(excerpt.slice(0,localHit)) + '<mark>' +
          escapeHTML(excerpt.slice(localHit,markedEnd)) + '</mark>' + escapeHTML(excerpt.slice(markedEnd));
      } else textNode.textContent = excerpt || '此页或段落未提取到文字。';
      position.value = String(partIndex + 1);
      position.removeAttribute('aria-invalid');
      locationNode.textContent = parts[partIndex].locator + '，位置' + (partIndex + 1) + '/' + parts.length +
        (text.length > SEGMENT_SIZE ? '，字符' + (start + 1) + '至' + end + '/' + text.length : '');
      container.querySelector('[data-source-page="previous"]').disabled = partIndex === 0 && start === 0;
      container.querySelector('[data-source-page="next"]').disabled = partIndex === parts.length - 1 && end >= text.length;
      textNode.scrollTop = 0;
    }

    function find(direction, initial = false) {
      if (!query) return;
      const beginning = initial ? 0 : hitPart;
      for (let distance = 0; distance <= parts.length; distance++) {
        const index = (beginning + direction * distance + parts.length) % parts.length;
        const text = textOf(index);
        let from = direction === 1 ? 0 : text.length;
        if (!initial && distance === 0) from = hitOffset + direction;
        if (direction === -1 && from < 0) continue;
        const offset = direction === 1 ? text.indexOf(query, from) : text.lastIndexOf(query, from);
        if (offset < 0) continue;
        partIndex = hitPart = index;
        hitOffset = offset;
        // Place the entire match in one bounded segment, including boundary hits.
        start = Math.max(0, offset - Math.min(400, Math.floor(SEGMENT_SIZE / 2)));
        render();
        return;
      }
    }

    container.querySelector('.source-search').addEventListener('submit', event => {
      event.preventDefault();
      query = input.value.trim();
      let count = 0;
      if (query) for (let index = 0; index < parts.length; index++) {
        const text = textOf(index);
        for (let offset = text.indexOf(query); offset >= 0; offset = text.indexOf(query, offset + 1)) count++;
      }
      for (const button of container.querySelectorAll('[data-source-find]')) button.disabled = count === 0;
      resultNode.textContent = !query ? '' : count ? '共' + count + '处匹配' : '未检索到该词组，不代表原件不存在相关事实。';
      if (count) find(1,true);
      else { hitPart = hitOffset = -1; render(); }
    });
    container.querySelectorAll('[data-source-find]').forEach(button => button.addEventListener('click', () =>
      find(button.dataset.sourceFind === 'next' ? 1 : -1)));
    container.querySelectorAll('[data-source-page]').forEach(button => button.addEventListener('click', () => {
      const text = textOf(partIndex);
      if (button.dataset.sourcePage === 'next') {
        if (start + SEGMENT_SIZE < text.length) start += SEGMENT_SIZE;
        else if (partIndex + 1 < parts.length) { partIndex++; start = 0; }
      } else if (start > 0) start = Math.max(0,start - SEGMENT_SIZE);
      else if (partIndex > 0) { partIndex--; start = Math.floor(Math.max(0,textOf(partIndex).length - 1) / SEGMENT_SIZE) * SEGMENT_SIZE; }
      render();
    }));
    position.addEventListener('change', () => {
      const value = Number(position.value);
      if (!Number.isInteger(value) || value < 1 || value > parts.length) {
        position.setAttribute('aria-invalid','true');
        resultNode.textContent = '请输入1至' + parts.length + '的位置序号。';
        return;
      }
      partIndex = value - 1; start = 0; render();
    });
    render();
    input.focus({preventScroll:true});
  }
  window.RHSourceViewer = {mount};
})();

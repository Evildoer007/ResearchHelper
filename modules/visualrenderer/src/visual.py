"""Presentation-only renderer; the frozen content remains authoritative."""
from __future__ import annotations
import copy
import html
import json
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from urllib.parse import urlsplit
from .contracts import METHOD_REGISTRY, DOCUMENT_CONTENT_INTENT, document_content_answer, fingerprint
from .layout import resource_directory
from .visual_selection import _shown, argument_charts, ledger_html, plot_values_fit_browser, report_chart_supported, select_figures
from .publication_content import (observation_raw_unit, observation_title,
                                  observation_value, reader_period_label, source_label)


MONEY_UNIT_FACTORS = {'元': 1, '千元': 1_000, '万元': 10_000,
                     '百万元': 1_000_000, '千万元': 10_000_000, '亿元': 100_000_000}


def logic_ordinal(number):
    digits = '零一二三四五六七八九'
    if 0 < number < 10:
        return digits[number]
    if 10 <= number < 100:
        tens, units = divmod(number, 10)
        return (digits[tens] if tens > 1 else '') + '十' + (digits[units] if units else '')
    return str(number)


def percentage_reading_digits(method, value):
    """One display precision for the same approved result in prose and charts."""
    digits = 4 if method in ('etf_nav_premium_v1', 'etf_daily_nav_premium_v1') else 2
    value = Decimal(str(value))
    while value and abs(value) < Decimal(1).scaleb(-digits) and digits < 8:
        digits += 1
    return digits


def display_money_scale(figure):
    """A display unit only; original rows and calculation units never change."""
    unit=figure['unit']
    original=dict(unit=unit,source_unit=unit,divisor=1,monetary=False)
    factors=MONEY_UNIT_FACTORS
    if (unit not in factors or figure.get('kind') in ('points','observations')
            or figure.get('unit_type')=='fund_unit_price'):
        return original
    maximum=max((abs(row['value']) for row in figure['rows']),default=0)
    if maximum==0:return dict(original,monetary=True)
    yuan=maximum*factors[unit]
    target,factor=('亿元',100_000_000) if yuan>=100_000_000 else ('万元',10_000) if yuan>=10_000 else ('元',1)
    return dict(unit=target,source_unit=unit,divisor=factor/factors[unit],monetary=True)


def research_chart_spec(content, figure, target):
    """Use one plot scale for both the reading caption and the selected chart."""
    display = display_money_scale(figure)
    spec = select_figures(content, [dict(figure, target=target, display=display)])[0]
    choice = spec.get('auto_visual')
    if choice and figure['kind'] == 'valuation_scenarios':
        choice['data'].update(numberDigits=4, focusIndex=0)
        choice['reason'] = 'registered_valuation_scenarios'
        choice['ledger']['columns'] = ['情景', '数值（' + figure['unit'] + '）']
    if not choice or display['unit'] == figure['unit']:
        return spec
    data = choice['data']
    series = data.get('series', [data])
    values = [value for item in series for value in item.get('values', []) if value is not None]
    scaled = {value: value / display['divisor'] for value in values}
    if (data.get('unit') != figure['unit'] or not scaled
            or len(set(scaled.values())) != len(scaled)
            or any(not math.isfinite(shown) or original != 0 and shown == 0 for original, shown in scaled.items())):
        spec['display'] = dict(display, unit=figure['unit'], divisor=1)
        return spec
    for item in series:
        item['values'] = [scaled.get(value) for value in item['values']]
    data['unit'] = display['unit']
    return spec


def display_prose_numbers(text):
    """Group explicit money tokens, not dates, codes, ratios or source quotes."""
    def grouped(match):
        whole,point,fraction=match.group().partition('.')
        return re.sub(r'(?<=\d)(?=(?:\d{3})+$)',',',whole)+point+fraction
    return re.sub(r'(?<![A-Za-z0-9.,])[+\-−]?\d{4,}(?:\.\d+)?(?=(?:百万元|千万元|亿元|万元|千元|元)(?![A-Za-z]))',
                  grouped,text)


def statement_presentation(record, field, content):
    """Render only verified quantity spans at reading precision.

    Frozen words, values, numeric proof and content hash remain unchanged.
    The original value remains in the linked evidence/calculation and tooltip.
    Unbound prose, quotes, dates, codes and non-identity projections are never
    rounded, interpreted or repaired.
    """
    text = record.get(field, '')
    integrity = (record.get('numeric_integrity') or {}).get('fields', {}).get(field, {})
    if not isinstance(text, str) or integrity.get('text_hash') != fingerprint(text):
        return text, []
    evidence = {item['id']: item for item in content.get('evidence', [])}
    calculations = {item['id']: item for item in content.get('calculations', [])}
    changes, end = [], 0
    for binding in integrity.get('bindings', []):
        span = binding.get('span')
        if (not isinstance(span, list) or len(span) != 2 or any(type(i) is not int for i in span)
                or not end <= span[0] < span[1] <= len(text)
                or text[span[0]:span[1]] != binding.get('display')
                or binding.get('projection') != 'identity'):
            continue
        kind, ref, unit = binding.get('source_kind'), binding.get('ref'), binding.get('unit')
        source = (evidence if kind == 'evidence' else calculations if kind == 'calculation' else {}).get(ref)
        if source is None:
            continue
        path = binding.get('path')
        original = source.get(path) if kind == 'evidence' else source.get('output', {}).get(path)
        if type(original) not in (int, float) or not math.isfinite(original):
            continue
        try:
            with localcontext() as context:
                context.prec = 50
                value = Decimal(str(binding['canonical_value']))
                if value != Decimal(str(original)):
                    continue
                label = None
                factors = MONEY_UNIT_FACTORS
                metric = (source.get('calculation_context') or {}).get('metric')
                if (kind == 'evidence' and path == 'value' and unit == source.get('unit') and unit in factors
                        and metric in ('revenue', 'net_profit', 'operating_cash_flow', 'gross_profit')):
                    yuan = value * factors[unit]
                    target, factor = (('亿元', Decimal(100000000)) if abs(yuan) >= 100000000
                        else ('万元', Decimal(10000)) if abs(yuan) >= 10000 else ('元', Decimal(1)))
                    shown = yuan / factor
                    rounded = shown.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                    if rounded or not shown:
                        label = format(rounded, ',.2f') + target
                elif kind == 'calculation' and unit == source.get('output', {}).get('unit') == '%' and path == 'value':
                    # Never round a real small movement to a displayed zero.
                    digits = percentage_reading_digits(source.get('method'), value)
                    rounded = value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
                    if rounded or not value:
                        label = format(rounded, f'.{digits}f') + '%'
                if label is None:
                    continue
        except (InvalidOperation, ValueError, TypeError, KeyError):
            continue
        changes.append(dict(start=span[0], end=span[1], label=label,
            target=('ev-' if kind == 'evidence' else '') + ref, original=binding['display'],
            exact=format(value, ',f') + unit))
        end = span[1]
    return text, changes


def statement_display_text(record, field, content):
    text, changes = statement_presentation(record, field, content)
    for item in reversed(changes):
        text = text[:item['start']] + item['label'] + text[item['end']:]
    return text


def _quotation_presentation(record, field, content):
    """Link exact bound quotations; deduplicate only the same original passage."""
    text = record.get(field, '')
    integrity = (record.get('numeric_integrity') or {}).get('fields', {}).get(field, {})
    if (content.get('verbatim_evidence_segments_version') != 1 or not isinstance(text, str)
            or integrity.get('text_hash') != fingerprint(text)):
        return []
    evidence = {item['id']: item for item in content.get('evidence', [])}
    changes, seen, end = [], set(), 0
    for binding in integrity.get('bindings', []):
        span = binding.get('span')
        item = evidence.get(binding.get('ref'))
        if (binding.get('projection') != 'verbatim_evidence' or binding.get('source_kind') != 'evidence'
                or binding.get('path') != 'text' or not item or not isinstance(item.get('text'), str)
                or item.get('source_check') != item.get('applicability_check') or item.get('source_check') != '通过'
                or not isinstance(span, list) or len(span) != 2 or any(type(value) is not int for value in span)
                or not end <= span[0] < span[1] <= len(text)
                or text[span[0]:span[1]] != binding.get('display')
                or binding['display'] != '“' + item['text'] + '”'):
            continue
        key = (item.get('artifact_id'), item.get('snapshot_hash'),
               item.get('record_locator', {}).get('part'), item['text'])
        comparable = all(isinstance(value, str) and value for value in key)
        changes.append(dict(start=span[0], end=span[1], target='ev-' + item['id'],
            quotation=binding['display'], repeated=comparable and key in seen))
        if comparable:
            seen.add(key)
        end = span[1]
    return changes


def statement_paragraphs(record, field, content):
    text, changes = statement_presentation(record, field, content)
    combined = sorted(changes + _quotation_presentation(record, field, content), key=lambda item: item['start'])
    if all(first['end'] <= second['start'] for first, second in zip(combined, combined[1:])):
        changes = combined
    output, offset = [], 0
    for paragraph in reading_paragraphs(text):
        pieces, cursor = [], offset
        stop = offset + len(paragraph)
        for item in changes:
            if not offset <= item['start'] < item['end'] <= stop:
                continue
            pieces.append(html.escape(display_prose_numbers(text[cursor:item['start']]), quote=True))
            escape = lambda value: html.escape(str(value), quote=True)
            if 'quotation' in item:
                if item['repeated']:
                    pieces.append("<span class='source-quotation-reused'><a class='source-quotation-repeat' href='#"
                        + escape(item['target']) + "' aria-haspopup='dialog' aria-label='查看同一原文及本次用途'>原文</a></span>")
                else:
                    pieces.append("<a class='source-quotation' href='#" + escape(item['target'])
                        + "' aria-haspopup='dialog' title='查看这段引文的原件与核验'><q>"
                        + escape(item['quotation']) + '</q></a>')
            else:
                pieces.append("<a class='quantity-link' href='#" + escape(item['target'])
                    + "' aria-haspopup='dialog' data-original-display='" + escape(item['original'])
                    + "' title='原值：" + escape(item['exact'])
                    + "' aria-label='" + escape(item['label'] + '，查看原值、口径与依据') + "'>"
                    + escape(item['label']) + '</a>')
            cursor = item['end']
        pieces.append(html.escape(display_prose_numbers(text[cursor:stop]), quote=True))
        output.append(''.join(pieces)); offset = stop
    return output


def reading_diagnostics(content):
    """Identify reading notes by their retained producer, not wording/regex."""
    states = [content.get('reasoning_state', {}).get('reading', {}).get('bundle', {})]
    states.extend(reading for reading in content.get('source_readings', []) if isinstance(reading, dict))
    return {note for note in content.get('reading_diagnostics', []) if isinstance(note, str)} | {
        note for state in states for note in state.get('limitations', []) if isinstance(note, str)}


def inspection_link(target, label, purpose=None, *, observation_ref=None, described_by=None):
    """A report-local reference, never an external query or a new inference."""
    escape = lambda value: html.escape(str(value), quote=True)
    purpose = purpose or ('查看' + label)
    observation = " data-inspection-observation='" + escape(observation_ref) + "'" if observation_ref else ''
    description = " aria-describedby='" + escape(described_by) + "'" if described_by else ''
    return ("<a href='#" + escape(target) + "' aria-haspopup='dialog' aria-label='"
            + escape(purpose) + "'" + observation + description + ">" + escape(label) + '</a>')


def inspection_surface(records):
    """Keep complete frozen records off the client paper, not in accordions.

    Records are moved, not cloned, into one accessible reading dialog. Their
    IDs remain unique, and closing the dialog returns them to the hidden store.
    No-JavaScript reading retains a linear, complete supporting-record fallback.
    """
    return ("<div id='report-records' hidden>" + ''.join(records) + '</div>'
            "<dialog id='report-inspector' aria-labelledby='inspection-title'>"
            "<header class='inspection-header'><button type='button' data-inspection-back hidden>返回</button>"
            "<h2 id='inspection-title'>研究依据</h2><button type='button' data-inspection-close aria-label='关闭研究依据'>"
            "<svg viewBox='0 0 24 24' width='20' height='20' aria-hidden='true'><path d='m6 6 12 12M18 6 6 18' "
            "fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/></svg></button></header>"
            "<div class='inspection-content' tabindex='0'></div></dialog>"
            "<noscript><style>#report-records[hidden]{display:block!important}</style></noscript>")


INSPECTION_SCRIPT = r"""
(()=>{
  const records=document.getElementById('report-records'),dialog=document.getElementById('report-inspector');
  if(!records||!dialog||typeof dialog.showModal!=='function'){
    if(records)records.hidden=false;return;
  }
  const body=dialog.querySelector('.inspection-content'),title=document.getElementById('inspection-title');
  const close=dialog.querySelector('[data-inspection-close]'),back=dialog.querySelector('[data-inspection-back]');
  let current=null,placeholder=null,opener=null,history=[];
  function returnRecord(){
    if(current&&placeholder){placeholder.replaceWith(current);current=null;placeholder=null;}
  }
  function resetClosed(){
    for(const row of [...records.querySelectorAll('[data-chart-selected]'),...dialog.querySelectorAll('[data-chart-selected]')])row.removeAttribute('data-chart-selected');
    returnRecord();history=[];opener=null;
    document.documentElement.classList.remove('report-inspecting');
  }
  function show(id,trigger,{remember=true,scroll=0}={}){
    if(!dialog.open&&current)resetClosed();
    const next=document.getElementById(id);
    if(!next||!records.contains(next))return false;
    if(current&&remember)history.push({id:current.id,scroll:body.scrollTop});
    returnRecord();
    current=next;placeholder=document.createComment('retained-report-record');next.before(placeholder);
    title.textContent=next.dataset.inspectionTitle||next.querySelector('h2,h3,h4')?.textContent||'研究依据';
    body.replaceChildren(next);back.hidden=!history.length;
    if(!dialog.open){opener=trigger;dialog.showModal();document.documentElement.classList.add('report-inspecting');}
    body.scrollTop=scroll;close.focus({preventScroll:true});return true;
  }
  function selectObservations(refs){
    const ids=new Set(refs);
    for(const row of body.querySelectorAll('[data-observation-id]')){
      if(ids.has(row.dataset.observationId))row.setAttribute('data-chart-selected','true');
      else row.removeAttribute('data-chart-selected');
    }
    body.querySelector('[data-chart-selected]')?.scrollIntoView({block:'nearest'});
  }
  dialog.addEventListener('close',()=>{
    // A queued close event can arrive after a newer record opens.
    if(dialog.open)return;
    const focused=document.activeElement;
    const trigger=opener;
    const restoreFocus=focused===document.body||dialog.contains(focused)||focused===trigger;
    resetClosed();
    if(restoreFocus&&trigger?.isConnected&&trigger.getClientRects().length)trigger.focus({preventScroll:true});
  });
  close.addEventListener('click',()=>dialog.close());
  dialog.addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const controls=[...dialog.querySelectorAll('a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])')]
      .filter(node=>node.getClientRects().length);
    if(!controls.length)return;
    event.preventDefault();
    const index=controls.indexOf(document.activeElement),direction=event.shiftKey?-1:1;
    controls[(index+direction+controls.length)%controls.length].focus({preventScroll:true});
  });
  back.addEventListener('click',()=>{
    const previous=history.pop();if(previous)show(previous.id,null,{remember:false,scroll:previous.scroll});
  });
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog)return;const bounds=dialog.getBoundingClientRect();
    if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)dialog.close();
  });
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href^="#"]');if(!link)return;
    let id;try{id=decodeURIComponent(link.hash.slice(1));}catch{return;}
    if(show(id,link)){
      event.preventDefault();
      if(link.dataset.inspectionObservation)selectObservations(link.dataset.inspectionObservation.split(/\s+/));
      return;
    }
    const target=document.getElementById(id);
    if(target&&dialog.contains(target)){event.preventDefault();target.scrollIntoView({block:'start'});}
  });
  document.addEventListener('researchhelper:inspect',event=>{
    const detail=event.detail;
    if(!detail||typeof detail.target!=='string'||!(detail.opener instanceof Element)||!detail.opener.isConnected)return;
    if(show(detail.target,detail.opener)&&Array.isArray(detail.observations))selectObservations(detail.observations);
  });
  function followHash(){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}show(id,null);}
  window.addEventListener('hashchange',followHash);if(location.hash)followHash();
})();
"""


def reading_paragraphs(text):
    """Separate complete prose sentences without summarizing or losing a byte.

    Short answers and literal quotations keep their current presentation.
    Decimal points, quoted Chinese full stops and every original space remain.
    """
    if len(text) <= 180:
        return [text]
    parts, start, quote_depth = [], 0, 0
    for index, character in enumerate(text):
        if character in '“「『':
            quote_depth += 1
        elif character in '”」』' and quote_depth:
            quote_depth -= 1
        if character == '。' and not quote_depth and index + 1 - start >= 40:
            parts.append(text[start:index + 1])
            start = index + 1
    if start < len(text):
        parts.append(text[start:])
    return parts or [text]


def knowledge_sources(item):
    """Render registered locators, not guessed URLs or source approvals."""
    escape=lambda value:html.escape(str(value),quote=True)
    primary=item.get('source_reference')
    references=list(primary) if isinstance(primary,list) else [primary] if primary else []
    additional=item.get('additional_source_references')
    if isinstance(additional,list):references+=additional
    links=[];seen=set()
    for reference in references:
        if not isinstance(reference,dict) or not isinstance(reference.get('url'),str):continue
        raw=reference['url'].strip()
        if any(ord(char)<=32 or ord(char)==127 or char=='\\' for char in raw):continue
        try:
            url=urlsplit(raw)
            if url.scheme not in ('https','http') or not url.hostname or url.username or url.password:continue
            url.port  # Reject malformed ports; no connection or hostname lookup.
        except ValueError:continue
        publisher=reference.get('publisher')
        publisher=publisher.strip() if isinstance(publisher,str) and publisher.strip() else url.hostname
        sections=reference.get('sections')
        sections=list(dict.fromkeys(value for value in sections if isinstance(value,str) and value.strip())) if isinstance(sections,list) else []
        key=(raw,publisher,tuple(sections))
        if key in seen:continue
        seen.add(key)
        links.append('<li><a href="'+escape(raw)+'" target="_blank" rel="noopener noreferrer">'+escape(publisher)+'</a>'+
                     ('<p>定位：'+escape('；'.join(sections))+'</p>' if sections else '')+'</li>')
    return '<ul>'+''.join(links)+'</ul>' if links else escape(item.get('source') or '来源尚未登记')


def knowledge_reading(item):
    """Keep conditions and counterexamples beside the locked knowledge source."""
    escape=lambda value:html.escape(str(value),quote=True)
    rows=[('适用条件',item.get('conditions')),('反例与相反渠道',item.get('counterexample')),
          ('时点限制',item.get('temporal_scope'))]
    notes=''.join('<dt>'+title+'</dt><dd>'+escape(value)+'</dd>' for title,value in rows if value)
    notes+='<dt>来源</dt><dd>'+knowledge_sources(item)
    if item.get('publication_date_status'):notes+='<p>'+escape(item['publication_date_status'])+'</p>'
    notes+='</dd>'
    return ('<article class="knowledge-reference" data-knowledge="'+escape(item.get('id',''))+'"><h3>'+escape(item['title'])+'</h3>'+
            '<p>版本'+escape(item['version'])+'</p><p>'+escape(item.get('content',''))+'</p><dl>'+notes+'</dl></article>')


def _verified_price_rows(content, binding, evidence, *, minimum=3):
    """Select verified rows from one bound iFinD price snapshot, never fill it.

    Intermediate prices need no invented calculation context: the exact
    original request binds CNY, unadjusted close and security identity. Each
    displayed number must still be individually verified and equal its cell.
    Unsupported sources or inconsistent/ambiguous rows keep the endpoint view.
    """
    bindings = binding['input_bindings']
    start, end = (evidence[bindings[key]] for key in ('start_price', 'end_price'))
    if (not content.get('task_id') or content.get('research_version') is None
            or start.get('artifact_id') != end.get('artifact_id')
            or start.get('snapshot_hash') != end.get('snapshot_hash')):
        return None
    origins = [source for source in content.get('source_records', [])
               if source.get('id') == start.get('artifact_id')]
    if len(origins) != 1:
        return None
    source = origins[0]
    parameters = source.get('parameters', {})
    context = start.get('calculation_context', {})
    if (source.get('kind') != 'api' or source.get('source') != 'ifind'
            or source.get('operation') != 'cmd_history_quotation'
            or source.get('hash') != start.get('snapshot_hash')
            or source.get('task_id') != content['task_id']
            or source.get('research_version') != content['research_version']
            or parameters.get('codes') != context.get('security_id')
            or parameters.get('indicators') != 'close'
            or parameters.get('functionpara', {}).get('Currency') != 'RMB'
            or parameters.get('functionpara', {}).get('CPS') != '1'):
        return None
    tables = source.get('snapshot', {}).get('tables', [])
    wanted_questions = set(binding.get('question_ids', []))
    window = binding['parameters']
    rows = []
    for item in content['evidence']:
        if (item.get('artifact_id') != source['id'] or item.get('snapshot_hash') != source['hash']
                or item.get('task_id') != content['task_id']
                or item.get('research_version') != content['research_version']
                or item.get('source_check') != '通过' or item.get('applicability_check') != '通过'
                or item.get('nature') != '已实现事实'
                or item.get('source') != 'ifind' or item.get('object') != start.get('object')
                or not wanted_questions.intersection(item.get('question_ids', []))):
            continue
        locator = item.get('record_locator', {})
        if locator.get('field') != 'close':
            continue
        table_index, row_index = locator.get('table'), locator.get('row')
        if (type(table_index) is not int or type(row_index) is not int
                or not 0 <= table_index < len(tables) or row_index < 0):
            return None
        table = tables[table_index]
        dates, values = table.get('time', []), table.get('table', {}).get('close', [])
        if (table.get('thscode') != context.get('security_id')
                or row_index >= len(dates) or row_index >= len(values)):
            return None
        day, value = dates[row_index], values[row_index]
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                return None
        except ValueError:
            return None
        if not window['start_date'] <= day <= window['end_date']:
            continue
        if (type(value) not in (int, float) or not math.isfinite(value)
                or item.get('value') != value or item.get('raw_value') != value
                or item.get('period') != day or item.get('unit') != '元'
                or item.get('raw_unit') != '元' or item.get('conversion_factor') != 1):
            return None
        rows.append({'label': day, 'value': value, 'unit': '元', 'ref': item['id']})
    if (len(rows) < minimum or len({row['label'] for row in rows}) != len(rows)
            or not set(bindings.values()).issubset({row['ref'] for row in rows})):
        return None
    return sorted(rows, key=lambda row: row['label'])


def verified_price_observations(content, calculation, evidence):
    return _verified_price_rows(content, calculation, evidence)


def calendar_observation_figure(content, figure, *, full_query_window=False):
    """Nulls mark verified calendar slots, never invented price observations."""
    if (content.get('market_calendar_coverage_version') != 1
            or figure.get('kind') != 'observations'):
        return figure
    evidence = {item['id']: item for item in content['evidence']}
    artifacts = {evidence[ref]['artifact_id'] for ref in figure['refs'] if ref in evidence}
    panels = [panel for panel in content.get('market_coverage', {}).get('panels', [])
              if panel['artifact_id'] in artifacts and panel['object'] in figure['objects']]
    if len(panels) != 1:
        return figure
    result = copy.deepcopy(figure)
    panel = panels[0]
    if not panel.get('calendar_ref') or panel['status'] == '日期覆盖不可核对':
        result['note'] += '交易日历未核验，不能据此确认区间完整性。'
        return result
    start, end = ((panel['start_date'], panel['end_date']) if full_query_window else
                  (min(figure['periods']), max(figure['periods'])))
    expected = [day for day in panel['calendar_dates'] if start <= day <= end]
    observed = {row['label'] for row in figure['rows']}
    result['categories'] = sorted(set(expected) | observed)
    result['calendar_gap_dates'] = sorted(set(expected) - observed)
    result['calendar_reference'] = copy.deepcopy(panel['calendar_ref'])
    if result['calendar_gap_dates']:
        result['note'] += ('日历核对后有' + str(len(result['calendar_gap_dates']))
            + '个交易日未展示价格，原因未识别；图中留空，不补值。')
    elif panel['unexpected_dates']:
        result['note'] += '存在交易所日历之外的价格日期，覆盖口径待核对。'
    else:
        result['note'] += '展示日期与本次取得的交易所日历一致，不代表个券成交或含分红回报。'
    return result


def question_observation_figures(content, question):
    """Display cited observations without inventing a calculation or return.

    The two anchors only locate the already-verified source rows. No result,
    method, value, missing date or source approval is created here.
    """
    if question.get('status') != '已回答':
        return []
    cited = set(question.get('refs', []))
    records = [item for item in content['evidence'] if item['id'] in cited]
    groups = {}
    for item in records:
        if item.get('record_locator', {}).get('field') == 'close':
            key = (item.get('artifact_id'), item.get('snapshot_hash'), item.get('object'))
            groups.setdefault(key, []).append(item)
    figures = []
    for members in groups.values():
        members = sorted(members, key=lambda item: item.get('period', ''))
        if len(members) < 2:
            continue
        first, last = members[0], members[-1]
        if not first.get('calculation_context'):
            continue
        locator_binding = {'input_bindings': {'start_price': first['id'], 'end_price': last['id']},
            'parameters': {'start_date': first['period'], 'end_date': last['period']},
            'question_ids': [question['id']]}
        subset = dict(content, evidence=members)
        rows = _verified_price_rows(subset, locator_binding, {item['id']: item for item in members}, minimum=2)
        if not rows:
            continue
        title = str(first['object']) + '未复权收盘价'
        figure = {'kind': 'observations', 'presentation': 'chart', 'title': title,
            'unit': '元', 'rows': rows, 'refs': [row['ref'] for row in rows],
            'categories': [row['label'] for row in rows],
            'groups': [{'name': title, 'indices': list(range(len(rows)))}],
            'objects': [first['object']], 'periods': [rows[0]['label'], rows[-1]['label']],
            'sources': ['ifind'], 'basis': ['人民币', '未复权'],
            'note': '只展示已核验观测，不补值，不代表持有总回报。'}
        if content.get('market_calendar_coverage_version') != 1:
            figure['note'] = '只连接已核验观测，不补齐缺失日期，不代表完整交易日历或持有总回报。'
        figures.append(calendar_observation_figure(content, figure, full_query_window=True)
                       if len(rows) >= 3 else dict(figure, note='两期未复权收盘价。'))
    return figures


def adopted_numeric_evidence(content):
    """Read same-version adopted facts, without accepting rejected prose."""
    from .evidence import evidence_binding
    result=[]
    for item in content.get('evidence',[]):
        if (type(item.get('value')) not in (int,float) or not math.isfinite(item['value'])
                or item.get('source_check')!='通过' or item.get('applicability_check')!='通过'
                or item.get('task_id')!=content['task_id']
                or item.get('research_version')!=content['research_version']):
            continue
        receipts=[item.get(name,{}) for name in ('source_receipt','applicability_receipt')]
        if not all(receipt.get('status')=='通过' and receipt.get('task_id')==content['task_id']
                and receipt.get('research_version')==content['research_version']
                and receipt.get('input_hash')==content['input_hash']
                and receipt.get('snapshot_hash')==item.get('snapshot_hash')
                and receipt.get('evidence_hash')==evidence_binding(item) for receipt in receipts):
            continue
        result.append(item)
    return result


def economic_observation_figures(content, question, *, adopted_only=False):
    """Plot cited adopted observations, never raw or guessed statistical data."""
    if content.get('economic_observation_version') != 1 or not adopted_only and question.get('status') != '已回答':
        return []
    selected = set(question.get('refs', []))
    grouped = {}
    for item in adopted_numeric_evidence(content):
        context = item.get('economic_context', {})
        if (item['id'] not in selected or item.get('field_definition', {}).get('registry') != 'ifind.edb_service.observation'
                or context.get('indicator') != item.get('object') or context.get('unit') != item.get('unit')
                or context.get('period') != item.get('period')):
            continue
        key = (item.get('artifact_id'), item.get('snapshot_hash'), item['object'], item['unit'],
               context.get('frequency'), fingerprint(context.get('metadata_reference')))
        grouped.setdefault(key, []).append(item)
    figures = []
    for members in grouped.values():
        by_period, conflict = {}, False
        for item in members:
            prior = by_period.get(item['period'])
            if prior is not None and prior['value'] != item['value']:
                conflict = True
            by_period.setdefault(item['period'], item)
        if conflict:
            continue
        ordered = [by_period[period] for period in sorted(by_period)]
        first = ordered[0]
        context = first['economic_context']
        periods = [item['period'] for item in ordered]
        monthly = context['frequency'] == 'M' and all(re.fullmatch(r'\d{4}-\d{2}', period) for period in periods)
        months = [int(period[:4])*12+int(period[5:7]) for period in periods] if monthly else []
        continuous = monthly and all(end-start == 1 for start, end in zip(months, months[1:]))
        rows = [dict(label=item['period'], value=item['value'], unit=item['unit'], ref=item['id']) for item in ordered]
        figures.append(dict(template='rh-economic-observations-1', kind='observations' if continuous else 'levels',
            presentation='chart', title=context['name'], unit=first['unit'], rows=rows, series_color='blue',
            categories=periods, groups=[dict(name=context['name'], indices=list(range(len(rows))))],
            refs=[row['ref'] for row in rows], objects=[first['object']], periods=[periods[0], periods[-1]],
            sources=['ifind'], publisher=context['data_source'], row_heading='统计期', value_heading='原始观测值',
            basis=[context['data_source'], {'M': '月度', 'Y': '年度', 'Q': '季度', 'W': '周度', 'D': '日度'}[context['frequency']]],
            note=('只连接连续月份的已核验观测，当前取得版本不证明历史首发。' if continuous
                  else '仅展示实际已核验观测，不补齐统计期，不绘制连续趋势。')))
    return figures


def annual_fee_observation_figures(content, question):
    """Compare cited annual policy rates without manufacturing a calculation.

    The literal fee clause supplies the component and annual basis; the adopted
    evidence supplies the value, unit, source and date. Only common components
    from the same observed period are comparable. Missing cells stay missing.
    """
    from .fee_sources import annual_fee_mentions
    if question.get('status') != '已回答':
        return []
    cited = set(question.get('refs', []))
    grouped, conflicts = {}, set()
    for item in adopted_numeric_evidence(content):
        if (item['id'] not in cited or item.get('unit') != '%'
                or item.get('raw_unit') != '%' or item.get('conversion_factor') != 1
                or item.get('value') != item.get('raw_value')
                or item.get('nature') != '合同政策条款'
                or not re.fullmatch(r'\d{6}\.(?:SH|SZ)', str(item.get('object', '')))):
            continue
        mentions = [mention for mention in annual_fee_mentions(item.get('text', ''))
            if mention['raw_token'] == item.get('raw_token') and mention['net_assets']]
        metrics = {mention['metric'] for mention in mentions}
        if len(metrics) != 1:
            continue
        metric = next(iter(metrics))
        context = item.get('calculation_context')
        if context and (context.get('metric') != metric or context.get('rate_period') != 'annual'
                        or context.get('fee_basis') != 'net_assets'):
            continue
        period = item.get('period', '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:/\d{4}-\d{2}-\d{2})?', period):
            continue
        key = (item['object'], metric)
        cells = grouped.setdefault(period, {})
        if key in cells and cells[key]['value'] != item['value']:
            conflicts.add(period)
        else:
            cells.setdefault(key, item)
    figures = []
    labels = {'management_fee': '管理费', 'custody_fee': '托管费'}
    for period, cells in grouped.items():
        objects = sorted({obj for obj, metric in cells})
        if period in conflicts or len(objects) != 2:
            continue
        metrics = [metric for metric in labels if all((obj, metric) in cells for obj in objects)]
        if not metrics:
            continue
        rows, groups = [], []
        for obj in objects:
            start = len(rows)
            rows.extend(dict(label=obj+labels[metric], value=cells[obj, metric]['value'], unit='%',
                ref=cells[obj, metric]['id']) for metric in metrics)
            groups.append(dict(name=obj, indices=list(range(start, len(rows)))))
        selected = [cells[obj, metric] for obj in objects for metric in metrics]
        figures.append(dict(template='rh-annual-fee-observations-1', kind='levels', presentation='chart',
            title='报告披露年费率对比', unit='%', rows=rows, groups=groups,
            categories=[labels[metric] for metric in metrics], refs=[row['ref'] for row in rows],
            objects=objects, periods=[period], sources=list(dict.fromkeys(item['source'] for item in selected)),
            row_heading='基金与费用项目', value_heading='原文年费率', basis=['前一日基金资产净值'],
            note='仅展示已核验的报告披露年费率，不代表全部费用、实际扣费或持有收益；不推定报告期外仍适用。'))
    return figures


def _bound_pmi_observation_figures(content, selected_ids):
    """Pair adopted narrative levels from their respective retained releases.

    Typed selectors need not encode a metric or date in the object label.
    Reuse the registered identity validator, not display-text inference.
    """
    from .statistical_metrics import level_identity
    from .contracts import ContractError
    labels = {'headline': '制造业PMI', 'production': '生产指数', 'new_orders': '新订单指数'}
    cells, conflicts = {}, set()
    for item in adopted_numeric_evidence(content):
        if selected_ids is not None and item['id'] not in selected_ids: continue
        try:
            identity = level_identity(item, content,
                item.get('applicability_review', {}).get('evidence_need_ids', {}))
        except (ContractError, KeyError, TypeError, ValueError):
            continue
        if identity is None or identity['metric'] not in labels: continue
        artifact = item.get('artifact', {})
        if (artifact.get('task_id') != content['task_id'] or artifact.get('research_version') != content['research_version']
                or artifact.get('id') != item.get('artifact_id')
                or fingerprint(artifact.get('snapshot')) != item.get('snapshot_hash')): continue
        key = (identity['period'], labels[identity['metric']])
        if key in cells and cells[key]['value'] != item['value']: conflicts.add(key)
        else: cells.setdefault(key, item)
    periods = sorted({period for period, _ in cells})[-2:]
    categories = list(labels.values())
    if len(periods) != 2 or any((period, metric) not in cells or (period, metric) in conflicts
            for period in periods for metric in categories): return []
    rows, groups = [], []
    for period in periods:
        start = len(rows)
        rows.extend(dict(label=period+' '+metric, value=cells[period, metric]['value'], unit='%',
            ref=cells[period, metric]['id']) for metric in categories)
        groups.append(dict(name=period, indices=list(range(start, len(rows)))))
    return [dict(template='rh-pmi-comparison-1', kind='levels', presentation='chart',
        title='制造业PMI与供需指标', unit='%', rows=rows, groups=groups, categories=categories,
        refs=[row['ref'] for row in rows], objects=['制造业'], periods=periods, sources=['official'],
        publisher='国家统计局', row_heading='指标与月份', value_heading='指数原值', basis=['扩散指数，非增长率'],
        note='各期指标来自对应已核验原件，不补缺月；指数水平不是产出或订单增长率。')]


def pmi_observation_figures(content, selected_ids=None):
    """Registered two-period PMI comparison of adopted literal observations.

    Registered identities support separate retained NBS releases. Legacy
    reports keep their original single-release, explicitly named route.
    No raw response is promoted, no rate of growth or threshold is calculated,
    and missing months/metrics are never filled or connected with a line.
    """
    if ('statistical_metric_identity_version' in content and content.get('research_mode') == 'current'
            and content.get('intent') != '原件内容核对'):
        return _bound_pmi_observation_figures(content, selected_ids)
    from .official_sources import article_url
    from .contracts import ContractError
    labels={'中国制造业采购经理指数':'制造业PMI','制造业采购经理指数':'制造业PMI',
        '制造业PMI':'制造业PMI','制造业生产指数':'生产指数','制造业新订单指数':'新订单指数'}
    categories=['制造业PMI','生产指数','新订单指数']
    grouped={};conflicts=set()
    for item in adopted_numeric_evidence(content):
        if selected_ids is not None and item['id'] not in selected_ids:continue
        period=item.get('period','');observed=item.get('observation_period',{})
        if (item.get('source')!='official' or item.get('unit')!='%'
                or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',period)
                or observed!={'frequency':'month','value':period}):continue
        artifact=item.get('artifact',{});origin=artifact.get('snapshot',{}).get('official_origin',{})
        if (artifact.get('operation')!='release_original' or artifact.get('id')!=item.get('artifact_id')
                or artifact.get('task_id')!=content['task_id'] or artifact.get('research_version')!=content['research_version']
                or fingerprint(artifact.get('snapshot'))!=item.get('snapshot_hash')
                or not re.fullmatch(r'\d{4}年\d{1,2}月中国采购经理指数运行情况',origin.get('title',''))):continue
        try:article_url(origin.get('url'),channel='nbs_release')
        except (ContractError,ValueError,TypeError):continue
        year,month=period.split('-')
        matched=re.fullmatch(re.escape(year)+r'年0?'+str(int(month))+r'月(.+)',str(item.get('object','')))
        metric=labels.get(matched[1]) if matched else None
        if not metric:continue
        key=(item['artifact_id'],item['snapshot_hash']);cell=(period,metric)
        cells=grouped.setdefault(key,{})
        if cell in cells and cells[cell]['value']!=item['value']:conflicts.add(key)
        else:cells.setdefault(cell,item)
    figures=[]
    for key,cells in grouped.items():
        if key in conflicts:continue
        periods=sorted({period for period,metric in cells
            if all((period,label) in cells for label in categories)})[-2:]
        if len(periods)!=2:continue
        rows=[];groups=[]
        for period in periods:
            start=len(rows)
            rows.extend(dict(label=period+' '+metric,value=cells[period,metric]['value'],unit='%',
                ref=cells[period,metric]['id']) for metric in categories)
            groups.append(dict(name=period,indices=list(range(start,len(rows)))))
        figures.append(dict(template='rh-pmi-comparison-1',kind='levels',presentation='chart',
            title='制造业PMI与供需指标',unit='%',rows=rows,groups=groups,categories=categories,
            refs=[row['ref'] for row in rows],objects=['制造业'],periods=periods,sources=['official'],
            publisher='国家统计局',row_heading='指标与月份',value_heading='指数原值',basis=['扩散指数，非增长率'],
            note='展示已核验的最近两期完整指标组，不补缺月；指数水平不是产出或订单增长率。'))
    return figures


def source_locator_label(item):
    """Readable exact anchors; never change the stored locator or its indices."""
    locator = item.get("record_locator")
    if not isinstance(locator, dict) or not locator:
        return str(item.get("locator", ""))
    parts = []
    if isinstance(locator.get("part"), str):
        parts.append(locator["part"])
    for key, label in (("table", "表"), ("row", "行")):
        value = locator.get(key)
        if type(value) is int and value >= 0:
            parts.append(f"{label}{value + 1}")
    if isinstance(locator.get("field"), str):
        parts.append("字段" + locator["field"])
    if isinstance(locator.get("unit_part"), str):
        parts.append("单位依据" + locator["unit_part"])
    # Unsupported legacy shapes keep their original display, not a guessed
    # location. Exact structured anchors remain in the frozen result package.
    return "，".join(parts) if parts else str(item.get("locator", ""))


def reading_dependency_order(question_ids, questions):
    """Follow only frozen, explicit prerequisites; never guess from prose.

    This is a reading order, not a new research plan or a merged conclusion.
    Older reports may omit dependency metadata. Keep their existing bindings
    and do not infer a link from similar words or nearby calculation records.
    """
    by_id = {question['id']: question for question in questions}
    visited, visiting, ordered = set(), set(), []

    def visit(ident):
        if ident in visited or ident in visiting or ident not in by_id:
            return
        visiting.add(ident)
        for prerequisite in by_id[ident].get('dependencies', []):
            visit(prerequisite)
        visiting.remove(ident)
        visited.add(ident)
        ordered.append(ident)

    for ident in question_ids:
        visit(ident)
    return ordered


def thesis_reading_calculations(content):
    """Keep explicit links; inherit question figures only for a unique owner.

    Shared main questions must not donate every chart to the first argument.
    This groups reading context, not financial support or new calculations.
    """
    questions = {question['id']: question for question in content['questions']}
    orders, owners = {}, {}
    for thesis in content['theses']:
        order = reading_dependency_order(thesis.get('question_ids', []), content['questions'])
        orders[thesis['id']] = order
        for ident in order:
            owners.setdefault(ident, set()).add(thesis['id'])
    result = {}
    for thesis in content['theses']:
        references = list(thesis.get('calculation_refs', []))
        for ident in orders[thesis['id']]:
            if owners[ident] == {thesis['id']} and questions[ident]['status'] == '已回答':
                references.extend(questions[ident].get('calculation_refs', []))
        result[thesis['id']] = list(dict.fromkeys(references))
    return result


def research_figures(content):
    """Choose figures from checked, frozen calculations, not model chart code.

    Each calculation stays independent. Endpoints are observations, never an
    interpolated history; dates, identities and units come from its bindings.
    The caller checks the complete content before using these descriptors.
    """
    evidence = {item['id']: item for item in content['evidence']}
    figures = {}
    labels = {
        'gross_margin_v1': {'revenue': '营业收入', 'cost': '营业成本'},
        'net_profit_margin_v1': {'revenue': '营业收入', 'net_profit': '报表净利润'},
        'operating_cash_profit_ratio_v1': {'operating_cash_flow': '经营现金流净额', 'net_profit': '报表净利润'},
        'fee_difference_v1': {'fee_a': '对象A年度费用率', 'fee_b': '对象B年度费用率'},
        'fee_difference_v2': {'fee_a': '对象A年度费率', 'fee_b': '对象B年度费率'},
        'management_custody_fee_comparison_v1': {
            'management_a': '基金A管理费', 'custody_a': '基金A托管费',
            'management_b': '基金B管理费', 'custody_b': '基金B托管费'},
        'etf_nav_premium_v1': {'price': '收盘价', 'nav': '官方单位净值'},
        'etf_daily_nav_premium_v1': {'price': '收盘价', 'nav': '该估值日官方单位净值'},
    }
    fee_components = ('management_custody_fee_comparison_v1', 'management_custody_fee_summary_v1')
    labels['management_custody_fee_summary_v1'] = labels['management_custody_fee_comparison_v1']
    for calculation in content['calculations']:
        method = calculation['method']
        fields, parameters = calculation['inputs'], calculation['parameters']
        bindings = calculation.get('input_bindings', {})
        rows, kind = [], 'bar'
        if method in ('observed_price_return_v1', 'observed_index_change_v1'):
            kind = 'points'
            keys = ('start_price', 'end_price') if method == 'observed_price_return_v1' else ('start_level', 'end_level')
            for key, date_key in zip(keys, ('start_date', 'end_date')):
                rows.append({'label': parameters[date_key], 'value': fields[key],
                             'unit': evidence[bindings[key]]['unit'], 'ref': bindings[key]})
            unit = METHOD_REGISTRY[method]['input_units'][keys[0]]
            title = '未复权收盘价起终点' if method == 'observed_price_return_v1' else '同版指数起终点'
            note = '仅展示两个已观测时点，不表示期间完整路径；变化不等于持有总回报、事件因果或预测概率。'
            if method == 'observed_price_return_v1':
                observations = verified_price_observations(content, calculation, evidence)
                if observations:
                    rows, kind = observations, 'observations'
                    title = '未复权收盘价格观测'
                    note = ('连线仅连接已核验观测，不补齐缺失日期，不证明完整交易日历。'
                            '公司行为未核验时不解释为投资表现；价格变化不等于持有总回报、事件因果或预测概率。')
        elif method in labels:
            for key, label in labels[method].items():
                record = evidence.get(bindings.get(key), {})
                if method in ('fee_difference_v1', 'fee_difference_v2', *fee_components) and record.get('object'):
                    label = record['object'] + label.split('A', 1)[-1].split('B', 1)[-1]
                rows.append({'label': label, 'value': fields[key],
                             'unit': record['unit'], 'ref': bindings[key]})
            unit = next(iter(METHOD_REGISTRY[method]['input_units'].values()))
            if METHOD_REGISTRY[method].get('same_input_unit'):
                unit = parameters['amount_unit']
            title = {'management_custody_fee_comparison_v1': '管理费与托管费分项对比',
                     'management_custody_fee_summary_v1': '管理费与托管费分项及合计',
                     'fee_difference_v1': '同口径年度费用率对比',
                     'fee_difference_v2': '同口径单项年费率对比',
                     'etf_nav_premium_v1': '同估值时点价格与官方净值',
                     'etf_daily_nav_premium_v1': '收盘价与同估值日官方净值',
                     'gross_margin_v1': '同口径收入与营业成本',
                     'net_profit_margin_v1': '同口径收入与报表净利润',
                     'operating_cash_profit_ratio_v1': '同口径经营现金流与报表净利润'}[method]
            if method == 'fee_difference_v2':
                component = {'management_fee': '管理费', 'custody_fee': '托管费'}[parameters['fee_kind']]
                title = component + '年费率对比'
                for row in rows:
                    row['label'] = evidence[row['ref']]['object'] + component
            note = calculation.get('limitations') or METHOD_REGISTRY[method].get('limitations') or METHOD_REGISTRY[method]['conditions']
            if isinstance(note, list):
                note = '；'.join(note)
            if method == 'management_custody_fee_summary_v1':
                # Display the actual registered outputs, never recompute totals
                # in the renderer or present them as original disclosures.
                rows.extend({'label': evidence[bindings['management_'+side]]['object']+'两项合计',
                    'value': calculation['output']['total_'+side],
                    'unit': calculation['output']['total_unit'], 'ref': None}
                    for side in ('a', 'b'))
        elif method == 'residual_present_value_v1':
            unit, title = calculation['output']['unit'], '剩余现金流情景现值'
            rows = [{'label': calculation.get('scenario') or '本项情景',
                     'value': calculation['output']['value'], 'unit': unit, 'ref': None}]
            note = '本项已登记假设下的计算结果，不赋予概率，不与其他主体或情景自动合并。'
        else:
            continue  # No registered visual semantics: keep the original tables.
        figure_refs = [row['ref'] for row in rows] if kind == 'observations' else calculation['input_refs']
        records = [evidence[ref] for ref in figure_refs]
        identities = list(dict.fromkeys(item['object'] for item in records))
        periods = list(dict.fromkeys(item['period'] for item in records))
        if method in ('observed_price_return_v1', 'observed_index_change_v1'):
            periods = [parameters['start_date'], parameters['end_date']]
        sources = list(dict.fromkeys(item['source'] for item in records))
        basis = []
        if method == 'observed_index_change_v1':
            context = evidence[bindings['start_level']]['calculation_context']
            basis = [{'price': '价格指数', 'gross_total_return': '全收益指数', 'net_total_return': '净全收益指数'}[context['index_variant']],
                     context['index_currency'], context['index_base']]
        elif method == 'etf_nav_premium_v1':
            context = evidence[bindings['price']]['calculation_context']
            basis = [context['share_class'], context['currency'], context['valuation_time']]
        elif method == 'etf_daily_nav_premium_v1':
            context = evidence[bindings['price']]['calculation_context']
            comparison_date = parameters['valuation_date'] if calculation.get('context_contract') else context['valuation_date']
            basis = [context['share_class'], context['currency'], comparison_date, '日度事后比较，非同刻可成交价差']
        elif method in ('gross_margin_v1', 'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'):
            basis = [{'consolidated': '合并口径', 'parent': '母公司口径'}[parameters['accounting_scope']], parameters['accounting_standard']]
        elif method == 'fee_difference_v2':
            basis = ['年度频率已核验', '净资产计费基数', '原始百分比单位保留']
        figures[calculation['id']] = {
            'kind': kind, 'title': title, 'unit': unit, 'rows': rows,
            # Sparse observations read better as a table. A registered result
            # alone never earns a graph, much less an interpolated price path.
            'presentation': 'table',
            'objects': identities, 'periods': periods, 'sources': sources,
            'refs': figure_refs, 'note': note, 'basis': basis,
            'result_label': calculation.get('method_title') or METHOD_REGISTRY[method]['title'],
            'result_value': calculation['output']['value'], 'result_unit': calculation['output']['unit'],
        }
        if method in fee_components:
            figures[calculation['id']]['unit_type'] = 'annual_fee_rate'
            group_names = [evidence[bindings['management_' + side]]['object'] for side in ('a', 'b')]
            if group_names[0] == group_names[1]:
                group_names = [name + '，' + evidence[bindings['management_' + side]]['calculation_context']['share_class']
                               for side, name in zip(('a', 'b'), group_names)]
            figures[calculation['id']]['groups'] = [
                {'name': name, 'indices': [offset, offset + 1]} for name, offset in zip(group_names, (0, 2))]
            figures[calculation['id']]['categories'] = ['管理费', '托管费']
            if method == 'management_custody_fee_summary_v1':
                for group, total_index in zip(figures[calculation['id']]['groups'], (4, 5)):
                    group['indices'].append(total_index)
                figures[calculation['id']]['categories'].append('两项合计')
            figures[calculation['id']]['presentation'] = 'chart'
        elif method in ('observed_price_return_v1', 'observed_index_change_v1',
                        'gross_margin_v1', 'net_profit_margin_v1',
                        'operating_cash_profit_ratio_v1', 'fee_difference_v1', 'fee_difference_v2',
                        'etf_nav_premium_v1','etf_daily_nav_premium_v1'):
            figures[calculation['id']]['presentation'] = 'chart'
            figures[calculation['id']]['categories'] = [row['label'] for row in rows]
            figures[calculation['id']]['groups'] = [{'name': title, 'indices': list(range(len(rows)))}]
            if method in ('etf_nav_premium_v1','etf_daily_nav_premium_v1'):
                figures[calculation['id']]['unit_type']='fund_unit_price'
        figure = calendar_observation_figure(content, figures[calculation['id']])
        if figure['presentation'] == 'chart' and not report_chart_supported(figure):
            figure['presentation'] = 'table'
        figures[calculation['id']] = figure
    return figures


def valuation_scenario_figures(references, calculations, evidence, figures):
    """Compare co-cited outputs on the same frozen per-share basis."""
    fixed = ('shares', 'rate', 'years', 'price')
    cohorts = {}
    for ref in references:
        calculation = calculations.get(ref)
        if not calculation or calculation['method'] != 'residual_present_value_v1' or ref not in figures:
            continue
        inputs, bindings = calculation.get('inputs'), calculation.get('input_bindings')
        overrides = calculation.get('scenario_overrides')
        if (not isinstance(inputs, dict) or not set(fixed).issubset(inputs) or not isinstance(bindings, dict)
                or bindings.get('price') not in evidence or not calculation.get('input_versions')
                or not isinstance(overrides, dict) or set(overrides) - {'receipt', 'debt', 'cost'}):
            continue
        if any(not isinstance(row, dict) or not row.get('reason') or row.get('base_ref') != bindings.get(field)
               or row.get('value') != inputs.get(field) for field, row in overrides.items()):
            continue
        basis = {name: calculation.get(name) for name in ('task_id', 'research_version',
            'implementation_version', 'input_bindings', 'input_versions', 'parameters', 'assumptions')}
        basis['fixed_inputs'] = {name: inputs[name] for name in fixed}
        cohorts.setdefault(fingerprint(basis), []).append(calculation)

    comparisons = {}
    for members in cohorts.values():
        bases = [item for item in members if not item.get('scenario_overrides')]
        if len(bases) != 1:
            continue
        base, = bases
        original = figures[base['id']]
        price = evidence[base['input_bindings']['price']]
        unit = base['output']['unit']
        if (len(original['objects']) != 1 or not original['objects'][0] or price['unit'] != unit
                or price['value'] != base['inputs']['price']):
            continue
        variants = [item for item in members if item.get('scenario_overrides') and
            item['output']['unit'] == unit and item['inputs'] != base['inputs']]
        if not variants:
            continue
        rows = [dict(label='基准现值', value=base['output']['value'], unit=unit, ref=None, calculation_ref=base['id'])]
        conditions = []
        for index, item in enumerate(variants, 1):
            title = item['scenario'] if isinstance(item.get('scenario'), str) else ''
            label = (('压力现值' if '压力' in title else '情景现值') if len(variants) == 1
                     else f'情景{index}现值')
            rows.append(dict(label=label, value=item['output']['value'], unit=unit, ref=None, calculation_ref=item['id']))
            reasons = [row['reason'] for row in item['scenario_overrides'].values()]
            conditions.append(dict(label=label, descriptions=list(dict.fromkeys(filter(None, [title, *reasons])))))
        rows.append(dict(label='参考价格', value=price['value'], unit=unit, ref=price['id']))
        values = [row['value'] for row in rows]
        if not plot_values_fit_browser(values) or not all(type(value) in (int, float) and math.isfinite(value) for value in values):
            continue
        refs = [base['id'], *(item['id'] for item in variants)]
        figure = dict(original, kind='valuation_scenarios', title='情景现值与参考价',
            presentation='chart', rows=rows, categories=[row['label'] for row in rows],
            groups=[dict(name='每股金额', indices=list(range(len(rows))))],
            calculation_refs=refs, scenario_conditions=conditions,
            note='现值按所列假设测算，情景不赋予概率。')
        for ref in refs:
            comparisons[ref] = figure
    return comparisons


def render(content, resources, visual_profile=None, report_profile='standard'):
    if content.get('report_kind') == 'research_draft':
        from .drafts import render as render_draft
        return render_draft(content, resources, visual_profile=visual_profile, report_profile=report_profile)
    return render_research(content, resources, visual_profile, report_profile)[0]


def render_research(content, resources, visual_profile=None, report_profile='standard'):
    """Return the report markup and the chart plan used to render it."""
    from .reports import (check_content, report_heading, research_date_label, version_basis,
                          TEMPLATE_VERSION, READER_VERSION, calculation_notation, source_publisher_label)
    from .knowledge import cited_definitions, definition_only
    check_content(content)
    escape = lambda value: html.escape(str(value), quote=True)
    sections, inspection_records = [], []
    document_only = content.get("intent") == DOCUMENT_CONTENT_INTENT
    concept_only = definition_only(content)
    concepts = {(item['id'], item['version']): item for item in cited_definitions(content)} if concept_only else {}
    display_title = report_heading(content)
    evidence_by_id = {item["id"]: item for item in content["evidence"]}
    evidence_numbers = {item["id"]: index + 1 for index, item in enumerate(content["evidence"])}
    calculations_by_id = {item['id']: item for item in content['calculations']}
    calculation_numbers = {item['id']: index + 1 for index, item in enumerate(content['calculations'])}
    figure_specs, chart_data = research_figures(content), []
    drawn_figures = set()
    drawn_observations = set()
    table_count = 0

    def evidence_link(ref):
        if ref not in evidence_by_id:
            return ''
        # Identity remains in the target; the reader sees a stable report-local number.
        label = '证据' + str(evidence_numbers.get(ref, ref))
        return inspection_link('ev-' + ref, label, '查看' + label + '的原文与核验')

    def calculation_link(ref):
        if ref not in calculations_by_id:
            return ''
        label = '计算' + str(calculation_numbers.get(ref, ref))
        return inspection_link(ref, label, '查看' + label + '的公式、原值与口径')

    def paper_references(refs, calculation_refs, anchor, label_prefix='研究依据'):
        """Resolve citations within the frozen report; retain unavailable IDs in its inspector."""
        missing_evidence = list(dict.fromkeys(ref for ref in refs if ref not in evidence_by_id))
        missing_calculations = list(dict.fromkeys(ref for ref in calculation_refs if ref not in calculations_by_id))
        refs = [ref for ref in refs if ref in evidence_by_id]
        calculation_refs = [ref for ref in calculation_refs if ref in calculations_by_id]
        groups = {}
        for ref in refs:
            item = evidence_by_id[ref]
            artifact = item.get('artifact', {})
            key = (artifact.get('id'), item.get('snapshot_hash'))
            if not all(isinstance(value, str) and value for value in key):
                key = ('individual', ref)
            groups.setdefault(key, []).append(ref)
        if document_only or len(refs) <= 8 and all(len(members) == 1 for members in groups.values()):
            citations = ' '.join(evidence_link(ref) for ref in refs)
        else:
            links = []
            for number, members in enumerate(groups.values(), start=1):
                target = anchor + '-source-' + str(number)
                label = label_prefix + (str(number) if len(groups) > 1 else '')
                rows = ''.join('<tr><td>' + escape(evidence_by_id[ref]['object']) + '</td><td>'
                    + escape(evidence_by_id[ref]['period']) + '</td><td>' + escape(evidence_by_id[ref]['text'])
                    + '</td><td>' + evidence_link(ref) + '</td></tr>' for ref in members)
                inspection_records.append("<section id='" + escape(target) + "' data-inspection-title='" + escape(label)
                    + "'><h3>" + escape(label) + "</h3><p>同一原始载体的引用汇总，不代表多份独立确认。各项原值与核验记录分别保留。</p>"
                    + "<div class='scroll' tabindex='0'><table><thead><tr><th>对象</th><th>期间</th><th>记录</th><th>核验依据</th></tr></thead><tbody>"
                    + rows + '</tbody></table></div></section>')
                links.append(inspection_link(target, label))
            citations = ' '.join(links)
        references = citations + ' '.join(calculation_link(ref) for ref in calculation_refs)
        if missing_evidence or missing_calculations:
            target = anchor + '-unavailable'
            records = ''.join('<dt>' + label + '</dt><dd>' + escape('、'.join(values)) + '</dd>'
                for label, values in (('证据引用', missing_evidence), ('计算引用', missing_calculations)) if values)
            inspection_records.append("<section id='" + escape(target) + "' data-inspection-title='引用说明' aria-label='引用说明'>"
                + '<p>本版报告未包含下列引用对应的记录。正文按原交付保留，不能据此确认这些依据。</p><dl>'
                + records + '</dl></section>')
            references += (' ' if references else '') + inspection_link(target, '引用说明')
        return references

    def admit_chart(figure, admitted):
        if not report_chart_supported(figure):
            return False
        if admitted is not None:
            if len(argument_charts([*admitted, figure], evidence_by_id)) <= len(admitted):
                return False
            admitted.append(figure)
        return True

    def event_timeline(refs, heading, admitted):
        if not any(word in heading for word in ('公告', '事件', '发布', '生效', '进展')):
            return ''
        from .evidence_visuals import announcement_timeline
        figure = announcement_timeline(content, (), refs)
        if not figure:
            return ''
        figure = select_figures(content, [figure])[0]
        if not admit_chart(figure, admitted):
            return ''
        number = len(chart_data) + 1
        target = 'research-chart-' + str(number)
        detail = 'figure-detail-' + target
        events = figure['data']['events']
        caption = '图' + str(number) + ' 公告时间线'
        chart_data.append(dict(figure, target=target, refs=[event['ref'] for event in events], inspection_target=detail))
        rows = ''.join('<li>' + escape(event['date']) + ' ' + escape(event['name']) + ' '
                       + evidence_link(event['ref']) + '</li>' for event in events)
        inspection_records.append("<section id='" + detail + "' data-inspection-title='公告时间线'><ol>"
                                  + rows + '</ol></section>')
        return ("<figure class='research-figure' data-presentation='chart'><figcaption>"
                + inspection_link(detail, caption) + "</figcaption><div id='" + target
                + "' class='chart chart-grouped' role='img' aria-label='公告时间线' aria-details='"
                + detail + "'></div></figure>")

    def figures_for(refs, admitted=None):
        nonlocal table_count
        markup = []
        available = [ref for ref in dict.fromkeys(refs) if ref in figure_specs and ref not in drawn_figures]
        comparisons = valuation_scenario_figures(available, calculations_by_id, evidence_by_id, figure_specs)
        for requested in available:
            if requested in drawn_figures:
                continue
            figure = copy.deepcopy(comparisons.get(requested, figure_specs[requested]))
            calculation_refs = figure.get('calculation_refs', [requested])
            ref = calculation_refs[0]
            drawn_figures.update(calculation_refs)
            scenario = figure['kind'] == 'valuation_scenarios'
            chart = figure['presentation'] == 'chart' and admit_chart(figure, admitted)
            if chart:
                number = len(chart_data) + 1
                figure = research_chart_spec(content, figure, 'research-chart-' + str(number))
                chart_data.append(figure)
            else:
                table_count += 1
                number = table_count
            method = calculations_by_id[ref]['method']
            identity_context = '、'.join(figure['objects']) + '；' + '、'.join(figure['periods'])
            source_units = list(dict.fromkeys(row['unit'] for row in figure['rows'])) or [figure['unit']]
            source_context = identity_context + '；' + (
                '单位' + source_units[0] if len(source_units) == 1 else '各项单位' + '、'.join(source_units))
            context = identity_context + '；单位' + figure['unit']
            if chart and figure['display']['unit']!=figure['unit']:
                context='、'.join(figure['objects'])+'；'+'、'.join(figure['periods'])+'；图示单位'+figure['display']['unit']
            detail_context = source_context + ('；' + '，'.join(figure['basis']) if figure['basis'] else '')
            displayed_basis = figure['basis']
            if method == 'etf_daily_nav_premium_v1':
                displayed_basis = [
                    '上市ETF交易份额' if re.fullmatch(r'listed_etf_unit:\d{6}\.(?:SH|SZ)', item)
                    else '人民币' if item == 'CNY' else item
                    for item in figure['basis']
                    if item not in figure['periods']
                    and item != '日度事后比较，非同刻可成交价差']
            if figure['basis']:
                context += '；' + '，'.join(displayed_basis)
            # A verified annual frequency establishes chart comparability;
            # it does not rewrite the original disclosure's percent unit.
            # Derived scenario values use the same readable precision as their
            # result label. Literal source values retain source precision;
            # raw derived floats remain in the same-version calculation record.
            def row_number(row):
                value = row['value']
                if row['ref']:
                    return str(value)
                if scenario:
                    return _shown(value)
                return format(value, '.4f').rstrip('0').rstrip('.')
            rows = ''.join(("<tr data-observation-id='" + escape(row['ref']) + "'>" if row['ref'] else '<tr>')
                           + f"<td>{escape(row['label'])}</td><td class='num' data-raw-value='{escape(row['value'])}'>{escape(row_number(row))}{escape(row['unit'])}</td><td>"
                           + (evidence_link(row['ref']) if row['ref'] else calculation_link(row.get('calculation_ref', ref)))
                           + '</td></tr>' for row in figure['rows'])
            precision = ('.'+str(percentage_reading_digits(method, figure['result_value']))+'f'
                         if figure['result_unit'] == '%' else '.4f')
            result_number = format(figure['result_value'], precision).rstrip('0').rstrip('.')
            result = f"{figure['result_label']}：{result_number}{figure['result_unit']}"
            if scenario:
                result = '；'.join(row['label'] + row_number(row) + row['unit'] for row in figure['rows'])
            accessible = figure['title'] + '；' + context + '；' + result
            # The plot may use a readable money scale; this table retains
            # original amounts and therefore names its own source unit.
            table_accessible = figure['title'] + '；' + detail_context + '；' + result
            data_table = (f"<div class='scroll' tabindex='0'><table aria-label='{escape(table_accessible)}'><thead><tr><th scope='col'>项目</th><th scope='col' class='num'>数值</th><th scope='col'>依据</th></tr></thead><tbody>"
                          + rows + '</tbody></table></div>')
            inline = not chart and report_profile == 'standard' and len(figure['rows']) <= 2
            if chart:
                visual = f"<div id='{figure['target']}' class='chart chart-grouped' role='img' aria-label='{escape(accessible)}' aria-details='figure-detail-{escape(ref)}'></div>"
                if report_profile=='detailed':visual=ledger_html(figure,visual,'figure-detail-'+ref)
            elif inline:
                visual = "<p class='measured-reading'>" + '，'.join(
                    "<span data-observation-id='" + escape(row['ref']) + "' data-raw-value='" + escape(row['value']) + "'>"
                    + escape(row['label']) + '为' + inspection_link('figure-detail-'+ref, row_number(row)+row['unit'],
                        '查看' + row['label'] + '的原始数据') + '</span>' if row['ref']
                    else escape(row['label']+row_number(row)+row['unit']) for row in figure['rows']) + '。</p>'
            else:
                visual = data_table
            # Financial method instructions are not a second body paragraph.
            # Keep a short, registered reading boundary visible and retain
            # every original condition under the same figure's provenance.
            financial_notes = {
                'gross_margin_v1': '同一期间与报表口径，不代表同行可比或盈利预测。',
                'net_profit_margin_v1': '同一期间与报表口径，未年化，不推断证券收益。',
                'operating_cash_profit_ratio_v1': '两项采用同一会计期间和报表口径。',
                'etf_daily_nav_premium_v1': '日度事后比较，不代表同刻可成交价差或套利机会，不作估值与收益预测。',
            }
            common_note = (METHOD_REGISTRY[method].get('limitations')
                           or METHOD_REGISTRY[method].get('conditions'))
            if isinstance(common_note, list):
                common_note = '；'.join(common_note)
            # Never fold an unfamiliar or case-specific limitation merely
            # because its calculation belongs to this financial method.
            reading_note = (financial_notes.get(method, figure['note'])
                            if figure['note'] == common_note else figure['note'])
            caption = figure['title'] if inline else ('图' if chart else '表') + str(number) + ' ' + figure['title']
            detail_id = 'figure-detail-' + ref
            conditions = ''.join('<p>' + escape(item['label'] + '：' + '；'.join(item['descriptions'])) + '</p>'
                                 for item in figure.get('scenario_conditions', []))
            inspection_records.append(f"<section id='{escape(detail_id)}' data-inspection-title='{escape(caption)}'><h3>{escape(caption)}</h3>"
                                      f"<p>{escape(detail_context)}</p>" + ('' if scenario else f'<p>{escape(result)}</p>') + data_table + conditions
                                      + '<p>' + escape(figure['note']) + '</p><div class="report-references">'
                                      + ' '.join(evidence_link(r) for r in figure['refs']) + ' '
                                      + ' '.join(calculation_link(r) for r in calculation_refs) + '</div></section>')
            result_band = ('' if scenario else f"<p class='figure-result'><span>{escape(figure['result_label'])}</span>"
                           f"<strong>{escape(result_number)}{escape(figure['result_unit'])}</strong></p>")
            presentation = 'chart' if chart else 'inline' if inline else 'table'
            markup.append(f"<figure class='research-figure' data-calculation-ref='{escape(ref)}' data-presentation='{presentation}'><figcaption>"
                          + inspection_link(detail_id, caption, '查看' + caption + '的数据与口径') + '</figcaption>' +
                          f"<p class='figure-context'>{escape(context)}</p>" + result_band + visual +
                          f"<p class='figure-note'>{escape(reading_note)}</p>"
                          "<p class='figure-source'>来源：" + inspection_link(detail_id,
                              '、'.join(dict.fromkeys(source_publisher_label(evidence_by_id[item]) for item in figure['refs'])),
                              '查看图表来源与口径') + '</p>'
                          + '</figure>')
        # Adjacent sparse tables remain independent measurements. Keep each
        # caption and provenance; do not merge scenarios or fabricate a series.
        if len(markup) > 1 and all("data-presentation='chart'" not in item for item in markup):
            return "<div class='measurement-figures'>" + ''.join(markup) + '</div>'
        if len(markup) > 1:
            return f"<div class='comparison-figures' data-figure-count='{len(markup)}'>" + ''.join(markup) + '</div>'
        return ''.join(markup)

    def query_detail(question):
        records = question.get("provenance_records", [])
        if not records:
            return ""
        parts = []
        for record in records:
            metadata = [("来源", record["source"]), ("操作", record["operation"]),
                        ("取得时间", record["obtained_at"]),
                        ("返回字段", "、".join(record["response_fields"]) or "NA")]
            for key, value in record["parameters"].items():
                if isinstance(value, dict):
                    metadata.extend((key + "." + field, setting) for field, setting in value.items())
                else:
                    metadata.append((key, value))
            metadata.extend((("请求标识", record["request_hash"]), ("业务快照标识", record["snapshot_hash"])))
            parts.append("<dl>" + "".join(f"<dt>{escape(key)}</dt><dd>{escape(value)}</dd>" for key, value in metadata) +
                         "</dl><p>" + escape(record["limitation"]) + "</p>")
        ident = 'query-' + question['id']
        inspection_records.append("<section id='" + escape(ident) + "' data-inspection-title='查询留痕'><h3>查询留痕</h3>" + ''.join(parts) + '</section>')
        return "<p class='query-reference'>" + inspection_link(ident, '查询口径') + '</p>'

    def observations_for(question, *, adopted_only=False, include_tables=True, admitted=None):
        nonlocal table_count
        markup = []
        figures=question_observation_figures(content, question)
        figures+=pmi_observation_figures(content,set(question.get('refs',[])))
        figures+=economic_observation_figures(content, question, adopted_only=adopted_only)
        if not question.get('calculation_refs'):
            figures+=annual_fee_observation_figures(content, question)
        for index, figure in enumerate(figures, start=1):
            identity = fingerprint(figure)
            if identity in drawn_observations:
                continue
            chart = admit_chart(figure, admitted)
            inline = not chart and report_profile == 'standard' and len(figure['rows']) <= 2
            if not chart and not include_tables:
                continue
            drawn_observations.add(identity)
            ref = 'observation-' + question['id'] + '-' + str(index)
            detail = 'figure-detail-' + ref
            if chart:
                number = len(chart_data) + 1
                figure = research_chart_spec(content, figure, 'research-chart-' + str(number))
                chart_data.append(figure)
            else:
                table_count += 1
                number = table_count
            caption = figure['title'] if inline else ('图' if chart else '表') + str(number) + ' ' + figure['title']
            context = '至'.join(figure['periods']) + '，单位：'+figure['unit']
            rows = ''.join("<tr data-observation-id='" + escape(row['ref']) + "'><td>" + escape(row['label']) + "</td><td class='num'>"
                + escape(row['value']) + '</td><td>' + evidence_link(row['ref']) + '</td></tr>' for row in figure['rows'])
            table = ('<div class="scroll" tabindex="0"><table aria-label="' + escape(caption)
                + '"><thead><tr><th scope="col">' + escape(figure.get('row_heading','日期')) + '</th><th scope="col">'
                + escape(figure.get('value_heading','收盘价')) + '</th><th scope="col">原始依据</th></tr></thead><tbody>'
                + rows + '</tbody></table></div>')
            inspection_records.append("<section id='" + escape(detail) + "' data-inspection-title='" + escape(caption)
                + "'><h3>" + escape(caption) + '</h3><p>' + escape(context) + '</p>' + table
                + '<p>' + escape(figure['note']) + '</p></section>')
            if chart:
                visual = ("<div id='" + escape(figure['target']) + "' class='chart chart-grouped' role='img' aria-label='"
                          + escape(caption + '，' + context) + "' aria-details='" + escape(detail) + "'></div>")
            elif inline:
                visual = "<p class='measured-reading'>" + '，'.join(
                    "<span data-observation-id='" + escape(row['ref']) + "'>" + escape(row['label']) + '为'
                    + inspection_link(detail, str(row['value'])+row['unit']) + '</span>' for row in figure['rows']) + '。</p>'
            else:
                visual = table
            source = figure.get('publisher') or '、'.join(dict.fromkeys(source_publisher_label(evidence_by_id[item])
                for item in figure['refs']))
            markup.append("<figure class='research-figure' data-evidence-series-ref='" + escape(ref)
                + "' data-presentation='" + ('chart' if chart else 'inline' if inline else 'table') + "'><figcaption>" + inspection_link(detail, caption)
                + "</figcaption><p class='figure-context'>" + escape(context) + "</p>" + visual
                + "<p class='figure-note'>" + escape(figure['note']) + "</p><p class='figure-source'>来源："
                + inspection_link(detail, source) + '</p></figure>')
        return ''.join(markup)

    quote_class = " class='document-content-quote'" if document_only else ""
    theses = content["theses"]
    # Composition already checks adopted prose against its independent review.
    # A missing obligation must not hide that reviewed prose in the appendix.
    answers = [q for q in content["questions"] if q.get("answer") and (
        q["status"] == "已回答" or q.get('research_coverage', {}).get('status')
        in ('answered', 'requires_evidence', 'unidentifiable'))]
    main_answers = [question for question in answers if question.get('required')
                    and question['status'] == '已回答' and question['question'] == content.get('question')]
    main_answer = main_answers[0] if theses and not document_only and len(main_answers) == 1 else None
    core_conclusion = (''.join('<p data-core-answer-part>' + part + '</p>'
        for part in statement_paragraphs(main_answer, 'answer', content)) if main_answer else '')
    # A fact answer is already the conclusion. Do not concatenate complete
    # answers into a second lead or derive an unreviewed shorter financial claim.
    # Document scope and actual thesis summaries still have distinct purposes.
    if document_only:
        summary = "<p>" + escape(content["scope"]) + "</p>"
        sections.append(("summary", "1.核对范围", summary))
    argument_number = len(sections) + 1
    arguments = []
    excerpt_questions = {}
    for index, question in enumerate(answers, start=1):
        refs = question["refs"]
        # Only elide the exact, generated quotation bundle. A legacy or distinct
        # answer is retained verbatim, even if the report has similar excerpts.
        quoted_below = (document_only and bool(refs) and all(ref in evidence_by_id for ref in refs)
                        and question["answer"] == document_content_answer([evidence_by_id[ref] for ref in refs]))
        if concept_only:
            entries = [concepts[(ref['id'], ref['version'])] for ref in question['knowledge_refs']]
            paragraphs = [escape(entry['content']) for entry in entries]
            if len(answers) > 1 or len(entries) > 1:
                paragraphs = ['<strong>' + escape(entry['title']) + '</strong><br>' + paragraph
                              for entry, paragraph in zip(entries, paragraphs)]
            paragraphs += [label + escape(entry[field]) for entry in entries
                           for label, field in (('<strong>适用条件</strong> ', 'conditions'),
                                                ('<strong>使用限制</strong> ', 'counterexample'))]
        else:
            paragraphs = ([escape(question['answer'])] if document_only else statement_paragraphs(question,'answer',content))
        answer_parts = [] if quoted_below else [f"<p{quote_class} data-answer-part>{part}</p>" for part in paragraphs]
        answer = ''.join(answer_parts)
        # A generated document answer is an index into the literal excerpts,
        # not another full-size answer card. Distinct answers retain the normal
        # reading layout and their complete text. Map titles only through
        # explicit references; do not summarize the quoted financial content.
        row_class = " class='document-answer-index'" if quoted_below else ""
        heading = f"{argument_number}.{index} " if document_only else ""
        if document_only:
            for ref in refs:
                titles = excerpt_questions.setdefault(ref, [])
                if question["question"] not in titles:
                    titles.append(question["question"])
        # With one ordinary answer, the paper title and answer identify the
        # subject. Keep the full request in the existing scope appendix rather
        # than printing the user's entire prompt as a second display heading.
        question_heading = ("" if len(answers) == 1 and not document_only and not theses
                            else f"<h3>{heading}{escape(question['question'])}</h3>")
        bound_figures = ''
        if not document_only and not theses:
            admitted = []
            bound_figures = figures_for(question.get('calculation_refs', []), admitted)
            if not admitted:
                bound_figures += observations_for(question, include_tables=not bound_figures, admitted=admitted)
            bound_figures += event_timeline(refs, question['question'], admitted)
        if bound_figures and not document_only:
            # A chart must be reachable before a long answer has consumed the
            # paper. Its first complete paragraph supplies context; all later
            # sentences follow at full measure, in their original order.
            layout_class=' argument-comparisons' if "class='comparison-figures'" in bound_figures else ''
            answer = ("<div class='argument-reading"+layout_class+"'><div class='argument-introduction'>"
                      + ''.join(answer_parts[:1]) + "</div><div class='argument-visual'>"
                      + bound_figures + "</div><div class='argument-continuation'>"
                      + ''.join(answer_parts[1:]) + '</div></div>')
            bound_figures = ''
            row_class = " class='research-argument'"
        references = paper_references(refs, question.get('calculation_refs', []), 'references-' + question['id'])
        reference_markup = "<div class='report-references'>" + references + '</div>'
        boundary = ("<p class='figure-context'>已核实部分，问题仍有缺口。</p>"
                    if question['status'] != '已回答' else '')
        arguments.append(f"<article{row_class} id='answer-{escape(question['id'])}' data-question-ref='{escape(question['id'])}'>{question_heading}{boundary}{answer}" + reference_markup
                         + query_detail(question) + bound_figures + "</article>")
    if theses and not document_only:
        # Research conclusions lead the paper. The complete question audit is
        # supporting content, not a second copy of the same investment logic.
        sections.append(("questions", "问题核对", ''.join(arguments)))
        arguments = []
    presented_questions = {main_answer['id']} if main_answer else set()
    reading_calculations = thesis_reading_calculations(content)
    for index, thesis in enumerate(theses):
        refs = paper_references(thesis['support'], [], 'support-' + str(index + 1), '支持依据')
        counter = paper_references(thesis.get('counter', []), [], 'counter-' + str(index + 1), '反证依据')
        # A mechanism's actual prerequisites belong beside its explanation,
        # not buried in the question audit. The saved dependency graph is the
        # only authority for those links. Presentation never upgrades their
        # nature, changes the thesis support or synthesizes a causal statement.
        related = thesis.get('question_ids', [])
        ordered = reading_dependency_order(related, content['questions'])
        answered = {question['id']: question for question in answers if question['status'] == '已回答'}
        prerequisites, explanations, calculation_refs = [], [], reading_calculations[thesis['id']]
        for ident in ordered:
            if ident not in answered or ident in presented_questions:
                continue
            question = answered[ident]
            presented_questions.add(ident)
            prerequisite = ident not in related
            body = ("<div class='thesis-answer' data-main-question-ref='" + escape(ident)
                + "' data-answer-role='" + ('prerequisite' if prerequisite else 'reasoning') + "'>"
                + ''.join('<p data-main-answer-part>' + part + '</p>'
                          for part in statement_paragraphs(question,'answer',content)))
            if prerequisite:
                # A prerequisite keeps its own checked sources. Do not label
                # those sources as thesis support when the Agent did not.
                independent_refs = [ref for ref in question.get('refs', [])
                    if ref not in thesis['support'] and ref not in thesis.get('counter', [])]
                if independent_refs:
                    body += "<p class='basis-references'>依据：" + ' '.join(evidence_link(ref) for ref in independent_refs) + '</p>'
            (prerequisites if prerequisite else explanations).append(body + '</div>')
        narrative = ''.join('<p>' + part + '</p>'
            for part in statement_paragraphs(thesis,'investment_meaning',content))
        basis = ''.join(prerequisites) + ''.join(explanations)
        admitted = []
        bound_figures = figures_for(calculation_refs, admitted)
        if not admitted:
            # A supported mechanism can have literal observations but no
            # calculation. Keep those answered questions' cited data beside
            # the thesis instead of silently omitting all source figures.
            bound_figures += ''.join(observations_for(answered[ident], include_tables=not bound_figures, admitted=admitted)
                                     for ident in ordered if ident in answered)
        if "data-presentation='chart'" not in bound_figures and thesis['support']:
            bound_figures += observations_for(dict(id='thesis-'+str(index+1), status='已回答',
                refs=thesis['support']), include_tables=not bound_figures, admitted=admitted)
        bound_figures += event_timeline(thesis['support'], thesis['title'], admitted)
        # Keep the conclusion, comparison evidence and complete reasoning in
        # reading order, even when the evidence is a table or inline values.
        if bound_figures:
            argument = ("<div class='argument-reading argument-comparisons'><div class='argument-introduction'>"
                + narrative + "</div><div class='argument-visual'>" + bound_figures + '</div>'
                + ("<div class='argument-continuation'>" + basis + '</div>' if basis else '') + '</div>')
        else:
            argument = narrative + basis + bound_figures
        proof_id = 'thesis-basis-' + str(index + 1)
        inspection_records.append(f"<section id='{proof_id}' data-inspection-title='{escape(thesis['title'])}'><h3>{escape(thesis['title'])}</h3>"
            f"<p>判断：{escape(thesis['judgment'])}</p><p>支持与评估依据：{refs}</p>"
            f"<p>反证与相反渠道：{counter or '已评估范围内无独立反证；不能解释为风险不存在。'}</p>"
            + "<div class='report-references'>" + ' '.join(inspection_link('answer-' + ident, answered[ident]['question'])
                for ident in ordered if ident in answered) + '</div>'
            + f"<p>{escape(thesis['investment_meaning'])}</p><p>限制：{escape(thesis['limitation'])}</p></section>")
        display_finding = statement_display_text(thesis,'title',content)
        if core_conclusion:
            investment = any(question['kind'] == 'investment' and question['id'] in related
                             for question in content['questions'])
            display_finding = ('策略逻辑' if investment else '研究逻辑') + logic_ordinal(index + 1) + '：' + display_finding
        heading = 'h2' if core_conclusion else 'h3'
        status = thesis['judgment'] if core_conclusion else '判断：' + thesis['judgment']
        finding = ("<div class='argument-heading'>" + '<' + heading + '>' + inspection_link(proof_id, display_finding, '查看' + thesis['title'] + '的论证依据') + '</' + heading + '>'
                   + f"<span class='argument-judgment'>{escape(status)}</span></div>")
        if index == 0 and not core_conclusion:
            # Preserve the report's conclusion anchor without displaying the
            # same claim twice. It remains the one actual finding, not a copy.
            finding = "<header id='summary' class='argument-summary'>" + finding + '</header>'
        direct_refs = "<div class='report-references argument-references'>" + refs + '</div>'
        arguments.append("<article class='research-argument' data-thesis-ref='" + escape(thesis['id']) + "'>" + finding + direct_refs
                         + argument + "<div class='argument-boundary'>" + ''.join('<p>限制：' + part + '</p>'
                             if index == 0 else '<p>' + part + '</p>' for index,part in enumerate(
                                 statement_paragraphs(thesis,'limitation',content))) + '</div>'
                         + ("<p class='counter-references'>反证与相反渠道：" + counter + '</p>' if counter else '') + '</article>')
    no_answer = ('本次已取得原始资料，尚不足以形成财务判断。' if content.get('collection_coverage')
                 else '必要证据尚未完成，以下为受限材料报告。')
    fact_only = not arguments and not theses and not document_only
    retained_numbers=adopted_numeric_evidence(content) if fact_only else []
    if retained_numbers:
        table_rows = []
        for item in retained_numbers:
            title = observation_title(item)
            value = observation_value(item) + observation_raw_unit(item)
            original = inspection_link('ev-' + item['id'], value, '查看' + title + '的原文')
            table_rows.append('<tr data-observation-id="' + escape(item['id']) + '"><th scope="row">'
                + escape(title) + '</th><td>' + escape(reader_period_label(item['period']))
                + '</td><td class="num">' + original + '</td></tr>')
        comparison=observations_for(dict(id='adopted-data',status='未回答',refs=[item['id'] for item in retained_numbers]), adopted_only=True)
        arguments.append('<p>以下观测值已核验，研究分析尚未完成，不构成投资结论。</p>'+comparison
            +'<div class="scroll adopted-data-scroll" tabindex="0"><table class="adopted-data-table" aria-label="已核验观测值"><thead><tr><th scope="col">对象与指标</th>'
            +'<th scope="col">所属期</th><th scope="col">原值</th></tr></thead><tbody>'
            +''.join(table_rows)+'</tbody></table></div>')
    sections.append(("theses", f"{argument_number}." + ("问题回答与研究论点" if theses else "问题回答"), "".join(arguments) or '<p>' + no_answer + '</p>'))
    collections = []
    for index, collection in enumerate(content.get('collection_coverage', []), start=1):
        detail = 'collection-records-' + str(index)
        count_rows = ''.join('<tr><th scope="row">' + escape(field['name']) + '</th><td class="num">'
            + escape(field['received']) + '</td><td class="num">' + escape(field['missing']) + '</td></tr>'
            for field in collection['fields'])
        field_names = [field['name'] for field in collection['fields']]
        headers = ''.join('<th scope="col">' + escape(name) + '</th>' for name in field_names)
        record_rows = ''.join('<tr><td>' + escape(row['code']) + '</td><td>' + escape(row['name'] or '未返回')
            + '</td><td>' + escape(row['classification'] or '未返回') + '</td>'
            + ''.join('<td>' + ('已返回，待核验' if row['fields'][name] else '未返回') + '</td>' for name in field_names)
            + '</tr>' for row in collection['rows'])
        limitation = '<p>' + escape(collection['limitation']) + '</p>'
        inspection_records.append("<section id='" + detail + "' data-inspection-title='接口返回记录'><h3>接口返回记录</h3>"
            + limitation + '<p>取得时间：' + escape(collection['obtained_at']) + '</p>'
            + '<div class="scroll" tabindex="0"><table><thead><tr><th scope="col">接口代码</th><th scope="col">接口名称</th>'
            + '<th scope="col">接口分类</th>' + headers + '</tr></thead><tbody>' + record_rows + '</tbody></table></div></section>')
        collections.append('<article><p>iFinD本次返回' + escape(collection['row_count']) + '条记录，包含'
            + escape(collection['returned_unique_codes']) + '个不同接口代码。请求报告期：'
            + escape('-'.join((collection['requested_period'][:4], collection['requested_period'][4:6], collection['requested_period'][6:]))) + '。</p>' + limitation
            + '<div class="scroll" tabindex="0"><table><caption>原始字段接收情况，非财务核验结果</caption>'
            + '<thead><tr><th scope="col">请求字段</th><th scope="col">已返回记录</th><th scope="col">未返回记录</th></tr></thead>'
            + '<tbody>' + count_rows + '</tbody></table></div><p>'
            + inspection_link(detail, '查看返回记录', '查看本次实际返回的完整记录及字段接收状态') + '</p></article>')
    if collections:
        sections.append(('collection', str(len(sections) + 1) + '.资料取得情况', ''.join(collections)))
    fact_number = sum(key != 'questions' for key, _, _ in sections) + 1
    facts = []
    for index, item in enumerate(content["evidence"], start=1):
        # PDF extraction retains page-layout line breaks. Reflow only their
        # presentation; the exact quotation and frozen source remain intact.
        material_name = item.get("artifact", {}).get("name", "")
        source_format = " data-source-format='pdf'" if document_only and isinstance(material_name, str) and Path(material_name).suffix.lower() == ".pdf" else ""
        number = "" if item.get("value") is None else f"<p>原始值：{escape(item.get('raw_value', item['value']))}{escape(item.get('raw_unit', item['unit']))}</p>"
        if item.get("value") is not None and item.get("conversion_factor", 1) != 1:
            number += f"<p>换算值：{escape(item['value'])}{escape(item['unit'])}，换算倍数{escape(item['conversion_factor'])}</p>"
        review = item.get("applicability_review", {})
        checks = ""
        if review:
            checks = f"<dt>采用依据</dt><dd>{escape(review.get('reason', ''))}</dd><dt>适用条件</dt><dd>{escape('；'.join(review.get('conditions', [])))}</dd>"
        availability = item.get("availability")
        if availability and availability.get("kind") in ("current_snapshot", "current_original"):
            boundary = '当前版本研究，不证明历史当时可得或事件因果。' if availability['kind']=='current_original' else '当前版本研究，不证明历史当时可得。'
            checks = f"<dt>当前版本取得时间</dt><dd>{escape(availability['obtained_at'])}</dd><dt>可用范围</dt><dd>{boundary}</dd>" + checks
        if item.get('observation_date'):
            checks = f"<dt>观测日期</dt><dd>{escape(item['observation_date'])}</dd>" + checks
        excerpt_heading = ("；".join(excerpt_questions.get(item["id"], [])) or item["object"]
                           if document_only else observation_title(item))
        period = "" if document_only else f"<p class='figure-context'>{escape(item['period'])}</p>"
        metadata = (f"<dl><dt>来源</dt><dd>{escape(item['source'])}，{escape(source_locator_label(item))}</dd>"
                     f"<dt>公开时间</dt><dd>{escape(item['published_at'] or '未核验')}</dd><dt>所属期与性质</dt><dd>{escape(item['period'])}，{escape(item['nature'])}</dd>"
                     f"<dt>两类核验</dt><dd>来源{escape(item['source_check'])}；适用性{escape(item['applicability_check'])}</dd>{checks}</dl>"
                     f"<p>内容版本{escape(item['content_version'])}，快照哈希{escape(item['snapshot_hash'])}</p>")
        if document_only:
            ident = 'source-ev-' + item['id']
            inspection_records.append(f"<section id='{escape(ident)}' data-inspection-title='{escape(excerpt_heading)}'><h3>{escape(excerpt_heading)}</h3>"
                f"<p{quote_class}{source_format}>{escape(item['text'])}</p>{number}{metadata}</section>")
            metadata = "<p class='figure-source'>来源：" + inspection_link(ident, item['source'], '查看原件来源与核验') + '</p>'
        facts.append(f"<article id='ev-{escape(item['id'])}'><h3>{fact_number}.{index} {escape(excerpt_heading)}</h3>{period}<p{quote_class}{source_format}>{escape(item['text'])}</p>{number}"
                     + metadata + '</article>')
    sections.append(("facts", f"{fact_number}." + ("原件摘录" if document_only else "事实与证据"), "".join(facts) or "<p>无通过两类核验的证据。</p>"))
    calculations, unattached_figures = [], []
    for item in content["calculations"]:
        output = item["output"]
        title, notation, variables = calculation_notation(item)
        binding = item.get("method_binding", {})
        version = (f"方法知识{escape(binding['knowledge_id'])}，锁定版本{escape(binding['knowledge_version'])}。"
                   if binding else "历史计算未记录方法知识绑定，保留原交付。")
        formula_detail = ("<div class='eq'>" + notation + "</div><p>" + escape(variables) + "</p>") if notation else ""
        limitations = item.get("limitations", "")
        if isinstance(limitations, list):
            limitations = "；".join(limitations)
        input_labels = {
            'management_a': '基金A管理费年费率', 'custody_a': '基金A托管费年费率',
            'management_b': '基金B管理费年费率', 'custody_b': '基金B托管费年费率',
            'fee_a': '对象A年度费用率', 'fee_b': '对象B年度费用率',
            'start_price': '起点未复权收盘价', 'end_price': '终点未复权收盘价',
            'start_level': '起点指数点位', 'end_level': '终点指数点位',
            'revenue': '营业收入', 'cost': '营业成本', 'net_profit': '报表净利润',
            'operating_cash_flow': '经营现金流净额', 'price': '收盘价', 'nav': '官方单位净值',
        }
        if item['method'] == 'residual_present_value_v1':
            input_labels = {'receipt': '收款', 'debt': '债务', 'cost': '费用',
                            'shares': '股数', 'price': '参考价格'}
        rows = []
        for key, value in item['inputs'].items():
            ref = item.get('input_bindings', {}).get(key)
            record = evidence_by_id.get(ref, {})
            # The displayed number is the calculation input, including any
            # registered scenario override. Its unit comes only from its
            # frozen evidence binding, never today's method defaults.
            unit = record.get('unit') or ''
            source = evidence_link(ref) if record else '未登记字段依据'
            if not unit:
                source += '；单位未登记'
            rows.append(f"<tr><td>{escape(input_labels.get(key, key))}</td>"
                        f"<td class='num'>{escape(value)}{escape(unit)}</td><td>{source}</td></tr>")
        rows = ''.join(rows)
        calculations.append(f"<article id='{escape(item['id'])}'><h3>{escape(item.get('scenario', title))}</h3>"
                            f"<p>计算值{output['value']:.4f}{escape(output['unit'])}</p>"
                            + (f"<p>适用限制：{escape(limitations)}</p>" if limitations else "") +
                            f"{formula_detail}<p>{version}</p>"
                            f"<div class='scroll' tabindex='0'><table><caption>计算输入与参数</caption><thead><tr><th scope='col'>字段</th><th scope='col'>计算输入</th><th scope='col'>依据</th></tr></thead><tbody>{rows}</tbody></table></div>"
                            f"<p>实现版本{escape(item['implementation_version'])}，输入引用{escape('、'.join(item['input_refs']))}。参数{escape(json.dumps(item['parameters'], ensure_ascii=False))}</p></article>")
        unattached = figures_for([item['id']])
        if unattached:
            unattached_figures.append(unattached)
    if calculations:
        sections.append(("methods", f"{len(sections)+1}.方法与可复算结果", "".join(calculations)))
    if unattached_figures:
        sections.append(('figures', f'{len(sections)+1}.数据与测算',
                         "<div class='measurement-figures'>" + ''.join(unattached_figures) + '</div>'))
    risk_number = len(sections) + 1
    technical_reading = reading_diagnostics(content)
    client_limits = [item for item in content['limitations'] + content.get('optional_findings', [])
                     if item not in technical_reading]
    limits = "".join("<li>" + escape(item) + "</li>" for item in client_limits)
    incomplete = [q for q in content['questions'] if q.get('required', True) and q['status'] != '已回答']
    if incomplete and content['limitations'] and not document_only:
        # Client limits explain unfinished research, not interpreter/query
        # internals. Preserve every original diagnostic in the linked record;
        # do not relabel any question, remove a missing need, or claim success.
        diagnosis = 'research-run-diagnostics'
        original_question = content.get('question', content['title'])
        pending = [q for q in incomplete if not (len(incomplete) > 1 and q['kind'] == 'investment'
                   and q['question'] == original_question)] or incomplete
        inspection_records.append("<section id='" + diagnosis + "' data-inspection-title='研究核验记录'><h3>研究核验记录</h3><ul>"
            + limits + '</ul><h4>未完成的必要问题</h4><ul>'
            + ''.join('<li>' + escape(q['question']) + '</li>' for q in pending) + '</ul></section>')
        notice = '研究状态：尚未完成。'
        notice += ('仅可采用已核验内容，不能替代完整投资判断。' if any(q['kind'] == 'investment' for q in incomplete)
                   else '部分必要回答未完成，请结合核验底稿阅读。')
        findings = ''.join('<li>' + escape(item) + '</li>' for item in content.get('optional_findings', [])
                           if item not in technical_reading)
        limits = ('<p>' + escape(notice) + '</p>' + ('<ul>' + findings + '</ul>' if findings else '')
                  + '<p>' + inspection_link(diagnosis, '查看核验底稿') + '</p>')
    tracks = "".join(f"<li>{escape(t['indicator'])}；来源{escape(t['source'])}；窗口{escape(t['window'])}；{escape(t['condition'])}"
                     + ("；比较基准：" + escape(t['benchmark']) if t.get('benchmark') else "") + "</li>" for t in content["tracking"])
    # Do not manufacture a risk paragraph to fill a template slot. Actual
    # limits and tracking rules stay visible; absence explanations and reading
    # coverage belong to the lossless supporting record below.
    if limits or tracks:
        sections.append(("risk", f"{risk_number}.限制与验证条件",
                         (limits if incomplete and content['limitations'] and not document_only else "<ul>" + limits + "</ul>" if limits else "") +
                         ("<ul>" + tracks + "</ul>" if tracks else "")))
    reading_scope = ""
    if content.get("reading_coverage"):
        rows = []
        for item in content["reading_coverage"]:
            scope = (str(item["read_characters"]) + "/" + str(item["total_characters"]) + "字") if "total_characters" in item else ("完整结构" if item.get("included") else "未读取")
            rows.append("<tr><td>" + escape(item.get("name") or item["artifact_id"]) + "</td><td>" + escape(scope) + "</td></tr>")
        reading_scope = ('<h3>原件读取范围</h3><div class="scroll"><table><thead><tr><th>材料</th><th>本轮读取</th></tr></thead><tbody>' +
                         "".join(rows) + "</tbody></table></div><p>读取范围不等于核验通过，未读部分保留在原件。</p>")
    if technical_reading:
        reading_scope += '<h3>原件阅读记录</h3><ul>' + ''.join('<li>' + escape(note) + '</li>'
            for note in dict.fromkeys(content['limitations'] + content.get('optional_findings', []))
            if note in technical_reading) + '</ul>'
    if content.get("changes"):
        changes = content["changes"]
        previous = content["update_from"]["version"]
        difference = f"<p>对比发布版本{previous}，原报告和原验证条件不覆盖。</p>"
        evidence_difference = "<ul>"
        for key, title in (("added", "新增记录"), ("revised", "修订记录"), ("missing", "本版未取得的原记录")):
            basis = changes.get("prior_basis" if key == "missing" else "current_basis", {})
            records = {item["id"]: item for item in basis.get("evidence", [])}
            names = [records[ref]["object"] + "：" + (records[ref].get("text") or str(records[ref].get("value")) + " " + records[ref].get("unit", ""))
                     if ref in records else ref for ref in changes[key]]
            evidence_difference += f"<li>{title}：{escape('；'.join(names) or '无')}</li>"
        evidence_difference += "</ul>"
        inspection_records.append("<section id='version-evidence-changes' data-inspection-title='取证记录对照'>"
            "<h3>取证记录对照</h3>" + evidence_difference + '</section>')
        difference += '<p>' + inspection_link('version-evidence-changes', '取证记录对照') + '</p>'
        if changes.get('calculation_changes'):
            delta = changes['calculation_changes']
            difference += '<h3>计算变化</h3>'
            if not delta['baseline_available']:
                difference += '<p>原版本未留存计算基准，无法核对变化。</p>'
            elif not changes.get('calculation_rows'):
                difference += '<p>已留存计算未发生变化。</p>'
            calculation_links = []
            for number, row in enumerate(changes.get('calculation_rows', [])):
                item = row['after'] or row['before']
                label = row['status'] + '：' + (item.get('method_title') or item['method'])
                target = 'version-calculation-' + str(number)
                detail_record = '<section id="' + target + '" data-inspection-title="计算变化"><h3>' + escape(label) + '</h3>'
                for detail in row['details']:
                    detail_record += '<p><strong>' + escape(detail['label']) + '</strong></p><p>原版本' + str(previous) + '：' + escape(
                        detail['before_text']) + '</p><p>本版本' + str(content['research_version']) + '：' + escape(detail['after_text']) + '</p>'
                inspection_records.append(detail_record + '</section>')
                calculation_links.append('<li>' + inspection_link(target, label, '查看计算变化的完整对照') + '</li>')
            if calculation_links:
                difference += '<ul>' + ''.join(calculation_links) + '</ul>'
        tracked_review = content.get('judgment_review_contract') == 1
        if tracked_review:
            from .judgment_review import DIMENSIONS
            difference += '<h3>原判断复核</h3>'
            for number, record in enumerate(content.get('judgment_reviews', [])):
                difference += '<article><h4>' + escape(record['prior_title']) + '：' + escape(record['assessment']) + '</h4>'
                current = '；'.join(item['title'] + '，' + item['judgment'] for item in record['current_theses'])
                difference += '<p>对应本版：' + escape(current or '未形成替代判断') + '</p>'
                labels, cells = [], []
                for key, label in DIMENSIONS.items():
                    item = record['dimensions'][key]
                    target = 'judgment-' + str(number) + '-' + key
                    labels.append('<th scope="col">' + label + '</th>')
                    cells.append('<td>' + inspection_link(target, item['state'], '查看' + label + '的依据') + '</td>')
                    citations = paper_references(item['refs'], item['calculation_refs'], target)
                    inspection_records.append('<section id="' + target + '" data-inspection-title="' + label
                        + '"><h3>' + label + '：' + escape(item['state']) + '</h3>'
                        + ('<p>预期口径：' + escape(item['comparison_kind']) + '</p>' if item.get('comparison_kind') else '')
                        + '<p>' + escape(item['answer']) + '</p>' + ('<p>' + citations + '</p>' if citations else '') + '</section>')
                difference += '<div class="scroll"><table><thead><tr>' + ''.join(labels) + '</tr></thead><tbody><tr>' + ''.join(cells) + '</tr></tbody></table></div></article>'
            if content.get('judgment_review_pending'):
                difference += '<p>部分原判断尚待复核，本版不判定其已经兑现。</p>'
        if report_profile == 'detailed' or not tracked_review:
            if changes.get("prior_basis") and changes.get("current_basis"):
                for key, version in (("prior_basis", previous), ("current_basis", content["research_version"])):
                    difference += f"<h3>版本{version}判断</h3>"
                    theses = changes[key]["theses"]
                    difference += "".join(f"<article><h4>{escape(t['title'])}：{escape(t['judgment'])}</h4><p>{escape(t['investment_meaning'])}</p><p>{escape(t['limitation'])}</p></article>" for t in theses) or "<p>该版未形成可核验论点。</p>"
            else:
                difference += "<p>原判断：" + escape('、'.join(changes["prior_judgments"]) or '无') + "；本版判断：" + escape('、'.join(changes["current_judgments"]) or '无') + "。</p>"
        if changes.get("knowledge_changes"):
            knowledge_difference = "<h3>采用的知识版本变化</h3><ul>" + "".join(
                f"<li>{escape(item['id'])}：原版本{escape(item['prior_version'] or '未采用')}，本版{escape(item['current_version'] or '未采用')}</li>"
                for item in changes["knowledge_changes"]) + "</ul>"
            inspection_records.append("<section id='version-knowledge-changes' data-inspection-title='知识版本对照'>"
                + knowledge_difference + '</section>')
            difference += '<p>' + inspection_link('version-knowledge-changes', '知识版本对照') + '</p>'
        original_conditions = "<h3>原发布条件</h3>" + ("<ul>" + "".join(
            f"<li>{escape(t['indicator'])}：{escape(t['condition'])}；来源：{escape(t['source'])}；窗口：{escape(t['window'])}"
            + ("；比较基准：" + escape(t['benchmark']) if t.get('benchmark') else "") + "</li>"
            for t in changes["original_conditions"]) + "</ul>" if changes["original_conditions"] else "<p>原版本未登记适用的验证条件。</p>")
        conditions = "本版条件见限制与验证条件。" if tracks else "本版未登记适用的验证条件。"
        original_conditions += "<p>" + conditions + "记录修订不等于事实新增或判断兑现，论点不按编号自动对应。</p>"
        if report_profile == 'detailed':
            difference += original_conditions
        else:
            inspection_records.append("<section id='version-original-conditions' data-inspection-title='原验证条件'>"
                + original_conditions + '</section>')
            difference += '<p>' + inspection_link('version-original-conditions', '原验证条件') + '</p>'
        sections.append(("changes", f"{len(sections)+1}.版本变化", difference))
    if concept_only:
        scope = '<p>' + escape(content['scope']) + '</p>'
    else:
        scope = f"<p>对象：{escape('、'.join(content['objects']) or '待消歧')}。截止时点：{escape(content['cutoff'])}。</p><p>意图：{escape(content['intent'])}；研究版本{content['research_version']}；模板{escape(content['template_version'])}。</p>"
        if not document_only and not content["theses"]:
            scope = "<p>" + escape(content["scope"]) + "</p>" + scope
    answered_ids = {question["id"] for question in answers}
    question = content.get("question", content["title"])
    question_label = "原始问题" if content.get("question") or not content.get("synthetic") else "留存报告题目"
    scope += "<h3>完整问题与知识依据</h3><p>" + question_label + "：" + escape(question) + "</p>"
    calendar_panels = content.get('market_coverage', {}).get('panels', [])
    if calendar_panels:
        scope += '<h3>价格日期覆盖</h3><p>' + escape(content['market_coverage']['limitations']) + '</p>'
        scope += '<div class="scroll" tabindex="0"><table aria-label="价格与独立交易所日历覆盖"><thead><tr><th scope="col">对象与窗口</th><th scope="col">核对状态</th><th scope="col">未观测日期</th></tr></thead><tbody>'
        for panel in calendar_panels:
            missing = '、'.join(panel['missing_dates']) or ('无' if panel['calendar_ref'] else '尚未核验')
            scope += '<tr><td>' + escape(panel['object'] + '，' + panel['start_date'] + '至' + panel['end_date']) + '</td><td>' + escape(panel['status']) + '</td><td>' + escape(missing) + '</td></tr>'
        scope += '</tbody></table></div>'
    if content["title"] not in (question, display_title):
        scope += "<p>留存报告题目：" + escape(content["title"]) + "</p>"
    scope += "<ul>"
    scope += "".join(f"<li>{escape(q['question'])}：{escape(q['status'])}" +
        (f"<p>{escape(q['answer'])}</p>" if q.get("answer") and q["id"] not in answered_ids else "") +
        "</li>" for q in content["questions"]) + "</ul>"
    knowledge = list(concepts.values()) if concept_only else content.get("knowledge", {}).get("entries", [])
    scope += "".join(knowledge_reading(item) for item in knowledge)
    if not tracks and content.get("tracking_reason"):
        scope += "<p>判断追踪：" + escape(content["tracking_reason"]) + "</p>"
    scope += reading_scope
    sections.append(("scope", f"{len(sections)+1}.研究范围与知识依据", scope))
    # A research paper is not the workbench. Keep the principal findings and
    # risks on the reading surface; preserve full supporting records on demand.
    principal_keys = {'summary', 'theses', 'figures', 'collection', 'risk', 'changes'}
    if document_only:
        principal_keys.add('facts')  # Literal document answers live in these excerpts.
    principal_titles = {'summary': '核对范围' if document_only else '核心结论',
                        'theses': ('通用口径' if concept_only else '问题核对' if document_only
                                   else '投资逻辑' if content['theses'] else '研究结论'),
                        'facts': '原件摘录', 'figures': '数据与测算', 'collection': '资料取得情况',
                        'risk': '风险与验证条件', 'changes': '版本变化'}
    if incomplete and not tracks and not document_only:
        principal_titles['risk'] = '研究限制'
    if not answers and content.get('collection_coverage'):
        principal_titles['theses'] = '研究状态'
    if retained_numbers:
        principal_titles['theses'] = '核验数据'
    principal, supporting = [], []
    if core_conclusion:
        principal.append("<section id='summary' class='core-conclusion' data-main-question-ref='" + escape(main_answer['id'])
                         + "'><h2>核心结论</h2>" + core_conclusion + '</section>')
    for key, title, text in sections:
        if concept_only and key == 'facts' and not content['evidence']:
            continue
        if key in principal_keys:
            if key == 'theses' and core_conclusion:
                principal.append(f"<section id='{key}' class='research-logics'>{text}</section>")
            else:
                principal.append(f"<section id='{key}'><h2>{principal_titles[key]}</h2>{text}</section>")
        else:
            supporting.append(f"<section id='{key}'><h2>{title.split('.', 1)[-1]}</h2>{text}</section>")
    body = ''.join(principal)
    inspection_records.extend(supporting)
    source_names = list(dict.fromkeys(source_publisher_label(item) for item in content['evidence']))
    source_line = '来源：' + escape('、'.join(source_names) or '尚无已核验来源') + '。'
    if concept_only:
        source_line = '知识来源：' + inspection_link('scope', '查看条目与原始出处') if knowledge else ''
    if not source_names and content.get('collection_coverage'):
        received_sources = list(dict.fromkeys(source_label(row['source'])
                                             for row in content['collection_coverage']))
        source_line = '已取得资料：' + escape('、'.join(received_sources)) + '；金融适用性待核验。'
    body += "<footer class='report-source-line'><p>" + source_line + (
        '' if concept_only and knowledge else inspection_link('scope', '研究范围')) + '</p></footer>'
    css = (resource_directory(resources, 'frontend') / 'report.css').read_text(encoding='utf-8')
    vendor = (resource_directory(resources, 'vendor') / 'echarts.min.js').read_text(encoding='utf-8') if chart_data else ''
    data = json.dumps(chart_data, ensure_ascii=False).replace("<", "\\u003c")
    script = r"""
const figures=CHART_DATA,CHARTS=[],CHART_LAYOUTS=new Map();
const s=getComputedStyle(document.documentElement);
const theme={red:s.getPropertyValue('--red').trim(),blue:s.getPropertyValue('--blue').trim(),ink:s.getPropertyValue('--ink').trim(),rule:s.getPropertyValue('--rule').trim()};
const reportAxes=figures.length?RHVisuals.axisFrame(RHVisuals.theme()):{};
function normalizeChartOption(option){return {animation:false,textStyle:{fontFamily:'Arial, PingFang SC, sans-serif',fontSize:14,color:theme.ink},grid:{left:8,right:68,top:12,bottom:42,containLabel:true},tooltip:{show:false},...option};}
function exactNumber(value){const [whole,fraction]=String(value).split('.');return /e/i.test(String(value))?String(value):whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction===undefined?'':'.'+fraction);}
function chartNumber(value,display,label=false){
  if(!display.monetary)return String(value);
  const scaled=value/display.divisor;
  if(scaled!==0&&Math.abs(scaled)<.01)return scaled.toExponential(1);
  return scaled.toLocaleString('zh-CN',{minimumFractionDigits:label?2:0,maximumFractionDigits:2});
}
for(const figure of figures){
  const node=document.getElementById(figure.target);if(!node||!window.echarts)continue;
  const chart=echarts.init(node,null,{renderer:'svg'});
  const display=figure.display;
  const originalTooltip=point=>{
    const row=figure.kind==='points'?figure.rows[point.dataIndex]:figure.rows[figure.groups[point.seriesIndex].indices[point.dataIndex]];
    return row.label+'\n'+(row.ref?'原值：':'计算值：')+exactNumber(row.value)+row.unit;
  };
  const signed=figure.rows.some(row=>row.value<0);
  const gradient=color=>new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:color+'99'},{offset:1,color}]);
  let series=figure.groups.map((group,index)=>({
    type:'bar',name:group.name,barMaxWidth:22,barGap:'45%',
    itemStyle:{color:gradient(figure.series_color==='blue'?theme.blue:index?theme.blue:theme.red)},
    data:group.indices.map((row,position)=>({value:figure.rows[row].value,
      itemStyle:{color:gradient(figure.series_color==='blue'?theme.blue:figure.groups.length===1&&position%2?theme.blue:index?theme.blue:theme.red)}})),
    label:{show:true,position:'right',fontSize:14,color:theme.ink,
      formatter:point=>chartNumber(figure.rows[group.indices[point.dataIndex]].value,display,true)}
  }));
  if(figure.kind==='points')series=[{type:'scatter',silent:true,symbolSize:11,
    data:figure.rows.map((row,index)=>({value:[row.value,index],itemStyle:{color:index?theme.blue:theme.red}})),
    label:{show:true,position:'right',distance:10,fontSize:14,color:theme.ink,
      formatter:point=>String(figure.rows[point.dataIndex].value)}}];
  const observations=figure.kind==='observations';
  const observationValues=observations?new Map(figure.rows.map(row=>[row.label,row.value])):null;
  if(observations)series=[{type:'line',name:figure.title,smooth:false,connectNulls:false,
    showSymbol:false,lineStyle:{width:2.5,color:theme.blue},
    data:figure.categories.map(day=>observationValues.get(day)??null)}];
  const option=normalizeChartOption({
    grid:{left:8,right:observations?20:68,top:42,bottom:42,containLabel:true},
    tooltip:observations?{trigger:'axis',confine:true,axisPointer:{type:'none'}}:{trigger:'item',confine:true,renderMode:'richText',formatter:originalTooltip},
    legend:{show:figure.groups.length>1,type:'scroll',top:0,left:0,icon:'rect',itemWidth:16,itemHeight:5,itemGap:24,textStyle:{fontSize:14,color:theme.ink}},
    xAxis:observations?{type:'category',data:figure.categories,boundaryGap:false,
      axisLabel:{fontSize:13,hideOverlap:true,color:theme.ink,formatter:value=>value.slice(5)},
      ...reportAxes}:
      {type:'value',name:display.unit,nameLocation:'middle',nameGap:32,min:signed?undefined:0,
      splitNumber:3,axisLabel:{fontSize:14,hideOverlap:true,color:'#64758A',formatter:value=>chartNumber(value,display)},...reportAxes},
    yAxis:observations?{type:'value',scale:true,name:figure.unit,splitNumber:4,
      axisLabel:{fontSize:13,color:theme.ink},...reportAxes}:
      {type:'category',inverse:true,data:figure.categories,
      ...reportAxes,axisLabel:{fontSize:14,width:110,overflow:'break'}},
    series
  });
  let layoutKey=null;
  const applyLayout=width=>{
    if(figure.auto_visual&&window.RHVisuals){
      const item=RHVisuals.adapt(figure),profile=RHVisuals.profile(),scheme=profile.schemes[figure.target]||item.scheme;
      chart.setOption(RHVisuals.option(scheme,RHVisuals.materialize(item,scheme),profile.theme,{width,height:chart.getHeight(),chart}),{notMerge:true});return;
    }
    // Two nonnegative amounts need room for both names and the plotted bars.
    // At narrow paper widths, move names below vertical bars rather than
    // sacrificing the plot to a long horizontal category axis.
    const compact=!observations&&figure.kind!=='points'&&!signed&&figure.groups.length===1&&figure.rows.length===2&&width<280;
    const characters=Math.max(3,Math.floor(width/28)-1);
    const key=compact?'columns-'+characters:'horizontal';
    if(key===layoutKey)return;
    layoutKey=key;
    if(!compact){chart.setOption(option,{notMerge:true});return;}
    chart.setOption({...option,
      grid:{left:8,right:12,top:36,bottom:8,containLabel:true},
      xAxis:{type:'category',data:figure.categories,...reportAxes,
        axisLabel:{fontSize:14,interval:0,color:theme.ink,formatter:value=>{
          const letters=Array.from(value),lines=[];
          for(let i=0;i<letters.length;i+=characters)lines.push(letters.slice(i,i+characters).join(''));
          return lines.join('\n');
        }}},
      yAxis:{type:'value',min:0,name:display.unit,splitNumber:3,...reportAxes,
        axisLabel:{fontSize:14,hideOverlap:true,color:'#64758A',formatter:value=>chartNumber(value,display)}},
      series:series.map(item=>({...item,barMaxWidth:36,label:{...item.label,position:'top'}}))
    },{notMerge:true});
  };
  CHART_LAYOUTS.set(chart,applyLayout);applyLayout(node.getBoundingClientRect().width);CHARTS.push(chart);
}
if(window.RHVisuals)RHVisuals.bindReports(figures,CHARTS,JSON.parse(document.getElementById('rh-visual-profile').textContent));
function resizeCharts(){
  for(const chart of CHARTS){
    const bounds=chart.getDom().getBoundingClientRect();
    if(bounds.width<=0||bounds.height<=0)continue;
    if(chart.getWidth()!==Math.round(bounds.width)||chart.getHeight()!==Math.round(bounds.height))
      chart.resize({width:Math.round(bounds.width),height:Math.round(bounds.height),animation:{duration:0}});
    CHART_LAYOUTS.get(chart)(bounds.width);
    chart.getZr().flush();
  }
}
// Print layout may be applied after beforeprint, and paper columns can change
// without a window resize. Observe the real containers and the media boundary;
// redraw SVG at the new width instead of scaling or clipping its text.
if(typeof ResizeObserver!=='undefined'){
  const chartObserver=new ResizeObserver(resizeCharts);
  CHARTS.forEach(chart=>chartObserver.observe(chart.getDom()));
}
const printMedia=window.matchMedia('print');
function prepareReportPrint(){
  resizeCharts();
}
function restoreReportReading(){
  requestAnimationFrame(resizeCharts);
}
printMedia.addEventListener('change',event=>event.matches?prepareReportPrint():restoreReportReading());
let timer;window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(resizeCharts,150)});
window.addEventListener('beforeprint',prepareReportPrint);
window.addEventListener('afterprint',restoreReportReading);
""".replace("CHART_DATA", data) + INSPECTION_SCRIPT
    visual_assets=''.join('<script>'+(resource_directory(resources,'frontend')/name).read_text()+'</script>' for name in ('visual-catalog-data.js','visual-library.js')) if chart_data else ''
    # The header dates the research boundary, not an unknown supplier first
    # publication time. Machine versions stay in the existing scope section.
    report_date = f"研究截止：<time datetime='{escape(content['cutoff'])}'>{escape(research_date_label(content['cutoff'], compact=True))}</time>"
    if content.get('synthetic'):
        report_date += "<span>合成示例，非真实研究</span>"
    header = f"<header class='report-header'><h1>{escape(display_title)}</h1><p class='report-meta'>{report_date}</p></header>"
    from .visual_catalog import theme
    selected_profile=visual_profile or dict(theme='research')
    if theme(selected_profile.get('theme')) is None:raise ValueError('配色未登记')
    profile_json = json.dumps(selected_profile,ensure_ascii=False).replace('<','\\u003c')
    renderer_version = escape(content.get('template_version', TEMPLATE_VERSION))
    document = f"<!doctype html><html lang='zh-CN' data-renderer-version='{renderer_version}' data-reader-version='{READER_VERSION}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{escape(display_title)}</title><style>{css}</style></head><body><a class='skip' href='#main'>跳转正文</a><main id='main' class='single-page-paper' tabindex='-1'>{header}{body}</main>{inspection_surface(inspection_records)}<script>{vendor}</script>{visual_assets}<script id='rh-visual-profile' type='application/json'>{profile_json}</script><script>{script}</script></body></html>"
    return document, chart_data

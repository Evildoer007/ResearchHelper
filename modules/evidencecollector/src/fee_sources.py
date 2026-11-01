"""Literal annual-fee and listed-share addresses, never evidence approval.

No ticker, rate, reporting year or identity is supplied by this registry.
Candidates are derived only from this task's already-read original characters.
"""
import re
from datetime import date

from .contracts import ContractError
from .financial_sources import original_line_index,original_date_anchors,selected_original_span

ROLE=re.compile(r'管理人报酬|年?管理费(?:率)?|年?托管费(?:率)?')
PERCENT=re.compile(r'(?<![\d.,+\-−])(?P<token>\d+(?:\.\d+)?)\s*(?P<unit>%/年|%)')
DATE=r'(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日'
PERIOD=re.compile(r'(?P<start>'+DATE+r')\s*至\s*(?P<end>'+DATE+r')')


def fee_rate_mentions(text):
    """Bind a literal percentage to one fee clause, not all labels on a page."""
    result=[]
    for number in PERCENT.finditer(text):
        if re.search(r'[+\-−]\s*$',text[max(0,number.start()-20):number.start()]):continue
        left=max(0,number.start()-240)
        roles=list(ROLE.finditer(text,left,number.start()))
        if not roles:continue
        role=roles[-1]
        between=text[role.end():number.start()]
        if re.search(r'[。；;]',between):continue
        after=text[number.end():number.end()+100]
        next_role=ROLE.search(after)
        if next_role:after=after[:next_role.start()]
        annual=(number['unit']=='%/年' or role.group().startswith('年')
            or '年费率' in between or bool(re.match(r'\s*的?\s*年费率',after)))
        end=number.end()+len(after.split('。')[0].split('；')[0].split(';')[0])
        excerpt=text[role.start():end]
        result.append(dict(metric='custody_fee' if '托管' in role.group() else 'management_fee',
            raw_token=number['token'],raw_unit=number['unit'],start=role.start(),end=end,
            annual=annual,net_assets=bool(re.search(r'基金资产净值|基金净资产',excerpt))))
    return result


def annual_fee_mentions(text):
    return [item for item in fee_rate_mentions(text) if item['annual']]


def validate_fee_component_binding(item):
    metric=item.get('calculation_context',{}).get('metric')
    if metric not in ('management_fee','custody_fee'):return
    matches=fee_rate_mentions(item.get('text',''))
    if not any(match['metric']==metric and match['raw_token']==item.get('raw_token') for match in matches):
        raise ContractError('费率数字须属于当前费用条款，不能借用同段其他费用行的数字')


def original_fee_choices(view,*,object_code):
    """Finite local refs preserve literal values and original line positions."""
    lines=original_line_index(view);dates=original_date_anchors(view)
    result=dict(rates=[],funds=[],shares=[],periods=[],candidate_is_approval=False)

    def span(fragment,start,end):
        selected=[line for line in lines if line['fragment_index']==fragment]
        offset=0;indices=[]
        for line in selected:
            following=offset+len(line['text'])
            if following>start and offset<end:indices.append(line['index'])
            offset=following
        if not indices or indices[-1]-indices[0]>=200:return None
        return dict(start=indices[0],end=indices[-1])

    def add(kind,value):
        if len(result[kind])>=128:return
        result[kind].append(dict(reference=kind+'_'+str(len(result[kind])+1),**value,
            candidate_is_approval=False))

    for fragment,part in enumerate(view.get('parts',[])):
        text=part['text']
        for mention in annual_fee_mentions(text):
            location=span(fragment,mention['start'],mention['end'])
            if location and mention['net_assets']:
                add('rates',dict(metric=mention['metric'],raw_token=mention['raw_token'],
                    raw_unit=mention['raw_unit'],span=location,
                    basis=selected_original_span(lines,location)))
        # A listed share is identified by its explicit trading code, never by
        # guessing that a fund's main code is a share-class code.
        for kind,label in (('funds','基金主代码'),('shares','交易代码')):
            for match in re.finditer(label+r'\s*[:：]?\s*(\d{6})(?!\d)',text):
                if match[1]!=object_code:continue
                location=span(fragment,match.start(),match.end())
                if location:
                    add(kind,dict(value=match[1],role='fund_main_code' if kind=='funds' else 'listed_share_code',
                        span=location,basis=selected_original_span(lines,location)))
        # Some ETF reports identify only the fund, not a trading/share code.
        # Offer that explicit reported scope, without asserting a share class.
        # A class-specific question must still obtain its actual class proof.
        if (re.search(r'基金主代码\s*[:：]?\s*'+re.escape(object_code)+r'(?!\d)',text)
                and re.search(r'基金运作方式\s*[:：]?\s*交易型开放式',text)):
            for match in re.finditer(r'基金简称[ \t]*[:：]?[ \t]+([^\r\n]+)',text):
                value=match[1].strip()
                location=span(fragment,match.start(),match.end())
                if value and len(value)<=200 and location:
                    add('shares',dict(value=value,role='reported_fund_scope_not_share_class',span=location,
                        basis=selected_original_span(lines,location),
                        limitation='仅原件所列基金整体范围，不证明A/C等份额类别，不将基金简称当交易代码。'))
        for match in PERIOD.finditer(text):
            start=tuple(map(int,re.findall(r'\d+',match['start'])))
            end=tuple(map(int,re.findall(r'\d+',match['end'])))
            try:date(*start);endpoint=date(*end).isoformat()
            except ValueError:continue
            if start>end:continue
            anchor=next((index for index,value in enumerate(dates)
                if value['part']==part['locator'] and value['date']==endpoint
                and value['quote'] in match['end']),None)
            location=span(fragment,match.start(),match.end())
            if location is not None and anchor is not None:
                add('periods',dict(value=re.sub(r'\s+','',match.group()),
                    observation_date=endpoint,observation_anchor=anchor,span=location,
                    basis=selected_original_span(lines,location)))
    result['eligible']=all(result[kind] for kind in ('rates','funds','shares','periods'))
    result['limits']=dict(per_family=128,truncation_does_not_prove_absence=True)
    return result

"""Query only missing official parameter definitions, retaining prior discovery."""
from pathlib import Path
import hashlib,json,os,tempfile,time
from urllib.parse import urlencode
from urllib.request import Request
from tests import ROOT
from tests.accept_one_sentence_research import PRODUCTION,production_digest
from researchhelper.settings import DataSettings
from researchhelper.financial_sources import metadata_response,IFIND_METADATA_BASE
from researchhelper.https_transport import open_verified_https

DISCOVERY=ROOT/'result/acceptance/working-capital-fields-live-cxxudagw'
NAMES=['应收账款','存货','应付账款']


def selected_fields():
    payload=json.loads((DISCOVERY/'2-get_indicators.json').read_text())
    rows=metadata_response('get_indicators',dict(indicators=NAMES,market='A股'),payload)
    selected=[]
    for name in NAMES:
        choices={row['indicator']:row for row in rows if row['name']==name}
        if len(choices)!=1:raise ValueError('目录不唯一，保留原始候选')
        selected.append(next(iter(choices.values())))
    return selected


def validate_parameters(indicator,payload):
    rows=metadata_response('get_indicator_params',dict(indicator=indicator,func_name='基础函数'),payload)
    if [row['name'] for row in rows]!=['D','C'] or [row['describe'] for row in rows]!=['报告期','报表类型']:
        raise ValueError('参数不是D/C，不登记为固定财务字段')
    if rows[1]['choice']!={'合并报表':'1','母公司报表':'2','合并报表(调整)':'3','母公司报表(调整)':'4'}:
        raise ValueError('报表范围与现有定义不同，保留原件')
    return rows


def run():
    selected=selected_fields()
    # Parse and validate the same shape offline before consuming a request.
    sample=json.loads((ROOT/'result/acceptance/ifind-financial-metadata-20261023/4-get_indicator_params.json').read_text())
    for row in selected:validate_parameters(row['indicator'],sample)
    base=ROOT/'result/acceptance';os.close(os.open(base/'working-capital-parameters-r183-20261027-v1.guard',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
    out=Path(tempfile.mkdtemp(prefix='working-capital-parameters-live-',dir=base));out.chmod(0o700)
    before=production_digest();refresh=access=''
    record=dict(status='running',discovery=str(DISCOVERY.relative_to(ROOT)),selected=selected,requests=0,maximum_requests=4,
        model_calls=0,pdf_downloads=0,repeated_discovery_requests=0,financial_values_requested=False,calls=[])
    receipt=out/'receipt.json'
    def save():receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    def request(url,headers,method):
        if record['requests']>=4:raise ValueError('Metadata request budget exceeded')
        record['requests']+=1;save();req=Request(url,data=b'' if method=='POST' else None,method=method)
        for key,value in headers.items():req.add_unredirected_header(key,value)
        with open_verified_https(req,timeout=25) as response:
            if response.geturl()!=url:raise ValueError('Unexpected redirect')
            raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Metadata response too large')
        return raw,json.loads(raw)
    save()
    try:
        refresh=DataSettings(PRODUCTION).read(private=True)['ifind']['refresh_token']
        _,auth=request('https://quantapi.51ifind.com/api/v1/get_access_token',{'refresh_token':refresh,'Content-Type':'application/json'},'POST')
        if auth.get('errorcode') not in (0,'0'):raise ValueError('iFinD authentication failed')
        access=auth['data']['access_token'];refresh=''
        for index,row in enumerate(selected,1):
            time.sleep(.25)
            params=dict(indicator=row['indicator'],func_name='基础函数')
            raw,payload=request(IFIND_METADATA_BASE+'get_indicator_params?'+urlencode(params),{'access_token':access},'GET')
            filename=str(index)+'-get_indicator_params.json';(out/filename).write_bytes(raw)
            parsed=validate_parameters(row['indicator'],payload)
            record['calls'].append(dict(indicator=row['indicator'],artifact=filename,sha256=hashlib.sha256(raw).hexdigest(),
                parameter_names=[x['name'] for x in parsed]));save()
        record['status']='three_parameter_definitions_verified'
    except Exception as error:record.update(status='failed',error_type=type(error).__name__,error=str(error)[:180])
    finally:
        access=refresh='';record.update(temporary_credentials_cleared=True,production_unchanged=production_digest()==before);save()
    print(json.dumps(dict(status=record['status'],requests=record['requests'],parameter_definitions=len(record['calls']),receipt=str(receipt)),ensure_ascii=False))
    return receipt


if __name__=='__main__':run()

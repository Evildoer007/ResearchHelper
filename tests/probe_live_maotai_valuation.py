"""One separately recorded provider check, never presented as autonomous planning."""
from pathlib import Path
import json,urllib.request
from tests import ROOT
from researchhelper.settings import DataSettings


def run():
    out=ROOT/'result/acceptance/live-maotai-harness-current/valuation-probe.json'
    if out.exists():return json.loads(out.read_text())
    token=DataSettings(Path.home()/'Library/Application Support/ResearchHelper/research-v1').read(private=True)['ifind']['access_token']
    params={'codes':'600519.SH','startdate':'2026-10-28','enddate':'2026-10-28','functionpara':{'Fill':'Blank'},'indipara':[{'indicator':'ths_pe_ttm_stock','indiparams':['','100']}]}
    request=urllib.request.Request('https://quantapi.51ifind.com/api/v1/date_sequence',data=json.dumps(params).encode(),headers={'Content-Type':'application/json'},method='POST');request.add_unredirected_header('access_token',token)
    with urllib.request.urlopen(request,timeout=20) as response:payload=json.load(response)
    record=dict(source='ifind',operation='date_sequence',parameters=params,errorcode=payload.get('errorcode'),tables=payload.get('tables',[]),automatic_harness_request=False,separate_probe=True,data_requests=1,model_calls=0)
    text=json.dumps(record,ensure_ascii=False,indent=2);assert token not in text;out.write_text(text);return record

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))

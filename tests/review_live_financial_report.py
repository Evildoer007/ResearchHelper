"""Independent decimal/PDF/visual review; never rerun or rewrite the study."""
import base64
from decimal import Decimal,localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tests import ROOT

NODE=Path('/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
METHODS={'net_profit_margin_v1':('net_profit','revenue'),
         'operating_cash_profit_ratio_v1':('operating_cash_flow','net_profit')}
LABELS={'revenue':'营业收入','net_profit':'净利润','operating_cash_flow':'经营活动产生的现金流量净额'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(folder,*,require_report=True):
    folder=Path(folder).resolve()
    assert folder.is_relative_to(ROOT/'result/acceptance') and folder.name.startswith('one-sentence-research-')
    task=json.loads((folder/'task.json').read_text())
    study=json.loads((folder/'receipt.json').read_text())
    files=[folder/name for name in ('task.json','receipt.json','report.html','report.json') if (folder/name).is_file()]
    protected={str(path):digest(path) for path in files}
    output=Path(tempfile.mkdtemp(prefix='live-financial-report-review-',dir=ROOT/'result/acceptance'))
    record={'status':'failed','task_id':task['id'],'source':str(folder),'source_hashes':protected,
        'model_calls':0,'provider_calls':0,'study_reopened':False,'report_accepted':False,
        'native_window_opened':False,'system_print_accepted':False,'checks':[]}
    try:
        from pypdf import PdfReader
        by_id={item['id']:item for item in task['evidence']}
        originals={item['id']:item for item in task['source_records']}
        calculations={item['method']:item for item in task['calculations']}
        assert set(METHODS).issubset(calculations)
        readers={};values=[];results={}
        for method,(numerator,denominator) in METHODS.items():
            calculation=calculations[method]
            for metric,amount in calculation['inputs'].items():
                item=by_id[calculation['input_bindings'][metric]]
                assert item['source_check']==item['applicability_check']=='通过'
                assert item['calculation_context']['metric']==metric
                assert item['value']==amount and item['unit']==calculation['parameters']['amount_unit']
                for key in ('currency','period','accounting_scope','accounting_standard'):
                    assert item['calculation_context'][key]==calculation['parameters'][key]
                original=originals[item['artifact_id']];material=original['snapshot']['material']
                payload=base64.b64decode(material['original_base64'],validate=True)
                assert hashlib.sha256(payload).hexdigest()==material['hash']
                locator=item['record_locator'];column=locator['financial_column']
                part=next(part for part in material['parts'] if part['locator']==locator['part'])
                assert column['row_quote'] in part['text']
                label=column['row_quote'].split('\t')[0]
                assert LABELS[metric] in label
                if metric=='net_profit':assert not any(word in label for word in ('归属','扣非','每股'))
                token=column['row_quote'].split('\t')[column['column_index']].strip().replace(',','')
                assert Decimal(token)==Decimal(str(amount))
                header=column['header_quote'].split('\t')[column['column_index']]
                assert ''.join(header.split())==''.join(calculation['parameters']['period'].split())
                if original['id'] not in readers:readers[original['id']]=PdfReader(io.BytesIO(payload))
                page=readers[original['id']].pages[part['pdf_table']['page']-1].extract_text()
                assert token in ''.join(page.replace(',','').split())
                values.append(amount)
            with localcontext() as context:
                context.prec=40
                expected=Decimal(str(calculation['inputs'][numerator]))/Decimal(str(calculation['inputs'][denominator]))*100
            actual=Decimal(str(calculation['output']['value']))
            assert abs(expected-actual)<=Decimal(str(calculation['output']['tolerance']))
            results[method]={'independent_value':str(expected),'saved_value':str(actual),'unit':'%'}
        record['checks']+=['raw amounts found in retained PDF pages','exact original table columns and fiscal periods',
            'same approved currency/unit/scope/standard','independent decimal recomputation of both ratios']
        record['independent_calculations']=results
        if require_report:
            assert task['status']=='已完成' and study['ordinary_chat_report_completed'] and study['financial_research_completed']
            assert (folder/'report.html').is_file() and (folder/'report.json').is_file()
            manifest={'report':str(folder/'report.html'),'content':str(folder/'report.json'),'values':list(dict.fromkeys(values))}
            (output/'input.json').write_text(json.dumps(manifest,ensure_ascii=False))
            process=subprocess.run([str(NODE),str(ROOT/'tests/accept_financial_chart_layout.mjs')],cwd=ROOT,
                env=dict(os.environ,RESEARCHHELPER_CHART_REVIEW=str(output/'input.json')),
                capture_output=True,text=True,timeout=90)
            (output/'browser.log').write_text(process.stdout+process.stderr)
            browser=json.loads((output/'browser-receipt.json').read_text())
            assert process.returncode==0 and browser['status']=='passed',browser.get('failures')
            record.update(browser=browser,report_accepted=True)
        assert all(digest(path)==expected for path,expected in protected.items())
        record.update(status='scoped_passed' if require_report else 'math_only_passed',study_unchanged=True)
    except Exception as error:
        record.update(error_type=type(error).__name__,error=str(error)[:1000])
    finally:
        target=output/'receipt.json';target.write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps({'status':record['status'],'receipt':str(target),'error':record.get('error')},ensure_ascii=False),flush=True)
    return record


if __name__=='__main__':run(Path(os.environ['RESEARCHHELPER_ACCEPTANCE_DIR']))

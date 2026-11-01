"""Actual PDF word boxes and a detached layout projection, never a new study."""
import base64
import copy
import io
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ORIGINAL=ROOT/'result/acceptance/one-sentence-research-8s4to77o/task.json'


def retained_duration_layout():
    import pdfplumber
    from researchhelper.contracts import fingerprint
    from researchhelper.pdf_material import duration_statement_part
    task=json.loads(ORIGINAL.read_bytes())
    task['financial_statement_unit_scope_version']=1
    record=next(r for r in task['source_records'] if r['kind']=='filing')
    material=record['snapshot']['material']
    raw=base64.b64decode(material['original_base64'],validate=True)
    # The retained successor includes the same original PDF. Derive only
    # these two layout parts here, instead of duplicating a previous projection.
    material['parts']=[part for part in material['parts'] if part.get('locator') not in ('第67页期间列式文本','第71页期间列式文本')]
    words={}
    with pdfplumber.open(io.BytesIO(raw)) as document:
        for number in (67,71):
            page=document.pages[number-1]
            tokens=page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False)
            words[number]=dict(words=tokens,width=page.width,height=page.height)
            part=duration_statement_part(tokens,page.width,page.height,number)
            assert part is not None
            material['parts'].append(part)
    # This is an explicit isolated representation test. No download receipt or
    # event is rewritten, no original acquisition is reopened, and the changed
    # snapshot is never presented as a fresh, approved external response.
    material['extract_version']='pdf-tables-5'
    record['hash']=record['snapshot_hash']=fingerprint(record['snapshot'])
    view=dict(id=record['id'],kind=record['kind'],snapshot_hash=record['hash'],parts=copy.deepcopy(material['parts']))
    return task,record,view,words

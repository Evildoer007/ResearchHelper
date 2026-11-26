"""Preserve PDF prose, ruled cells and explicitly aligned statement words.

Tables are a reproducible representation of actual PDF geometry. They are not
financial facts, selected periods, publication proofs or semantic approvals.
"""
import base64
import hashlib
import io
import math
import re
from datetime import date

EXTRACT_VERSION = 'pdf-tables-7'
STATEMENT_DETAIL_EXTRACT_VERSIONS = ('pdf-tables-6', EXTRACT_VERSION)
DURATION_EXTRACT_VERSIONS = ('pdf-tables-5', *STATEMENT_DETAIL_EXTRACT_VERSIONS)
FINANCIAL_CONTEXT_EXTRACT_VERSIONS = ('pdf-tables-4', *DURATION_EXTRACT_VERSIONS)
ALIGNED_EXTRACT_VERSIONS = ('pdf-tables-3', *FINANCIAL_CONTEXT_EXTRACT_VERSIONS)
TABLE_EXTRACT_VERSIONS = ('pdf-tables-2', *ALIGNED_EXTRACT_VERSIONS)
TABLE_VERSION = 1
MAX_PAGES = 500
COLUMN_DRIFT_POINTS = 1.0
TEXT_TABLE_VERSION = 2
_UNDECODED = re.compile(r'\(cid:\d+\)|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufffd\ufffe\uffff]')


def legacy_pdf_text_damaged(material):
    """Flag known decoder artifacts for an explicit retry, without editing the archive."""
    version = material.get('extract_version')
    return (version in ('1', *TABLE_EXTRACT_VERSIONS[:-1])
            and str(material.get('name', '')).lower().endswith('.pdf')
            and any(isinstance(part, dict) and isinstance(part.get('text'), str) and _UNDECODED.search(part['text'])
                    for part in material.get('parts', [])))


def pdf_reparse_replacements(task):
    """Resolve obsolete PDF text to a unique readable parse of the same original."""
    from .contracts import fingerprint

    earlier, current = [], {}
    for record in task.get('source_records', []) + task.get('materials', []):
        if (record.get('kind') not in ('material', 'filing') or record.get('task_id') != task.get('id')
                or record.get('research_version') != task.get('version')):
            continue
        snapshot = record.get('snapshot')
        if not isinstance(snapshot, dict):
            continue
        material = snapshot.get('material') if record['kind'] == 'filing' else snapshot
        if not isinstance(material, dict) or not str(material.get('name', '')).lower().endswith('.pdf'):
            continue
        readable = (material.get('extract_version') == EXTRACT_VERSION and material.get('status') == '已解析'
                    and any(isinstance(part, dict) and isinstance(part.get('text'), str)
                            and part['text'].strip() and not part.get('pdf_text_limited')
                            for part in material.get('parts', [])))
        if not readable and material.get('extract_version') not in ('1', *TABLE_EXTRACT_VERSIONS[:-1]):
            continue
        source = record.get('source')
        scope = (record['kind'], source, record.get('operation'), material['name'], material.get('hash'),
                 fingerprint([record.get('parameters'), record.get('directory'), material.get('official_origin')]),
                 record.get('obtained_at') if source == 'official' else None)
        if readable:
            current.setdefault(scope, []).append((record, material))
        else:
            earlier.append((scope, record, material))

    replacements = {}
    for scope, old, material in earlier:
        matches = current.get(scope, [])
        if len(matches) != 1:
            continue
        new, extracted = matches[0]
        if not legacy_pdf_text_damaged(material) and not any(
                part.get('pdf_text_recovered') or part.get('pdf_text_limited') for part in extracted['parts']):
            continue
        encoded = material.get('original_base64')
        if (not encoded or encoded != extracted.get('original_base64')
                or any(fingerprint(record['snapshot']) != record.get('snapshot_hash', record.get('hash'))
                       for record in (old, new))):
            continue
        try:
            original_hash = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
        except (ValueError, TypeError):
            continue
        if original_hash == extracted.get('hash'):
            replacements[old['id']] = new['id']
    return replacements


def _decoded_page(plain, page):
    from pypdf.errors import PdfReadError
    from pdfplumber.utils.exceptions import PdfminerException

    unmapped = set()

    def note_font(text, _cm, _tm, font, _size):
        if (text.strip() and font and font.get('/Subtype') == '/Type0'
                and font.get('/Encoding') in ('/Identity-H', '/Identity-V') and not font.get('/ToUnicode')):
            name = font.get('/BaseFont', '')
            descendants = font.get('/DescendantFonts', [])
            if descendants:
                descriptor = descendants[0].get_object().get('/FontDescriptor')
                if descriptor:
                    name = descriptor.get_object().get('/FontName', name)
            unmapped.add(str(name).lstrip('/'))

    try:
        text = plain.extract_text(visitor_text=note_font) or ''
    except PdfReadError:
        text = None
    if text is not None and not unmapped and not _UNDECODED.search(text):
        return dict(text=text)
    try:
        alternate = page.extract_text() or ''
        if alternate.strip() and not _UNDECODED.search(alternate):
            # CID codes are not Unicode. The layout decoder must actually
            # map every affected font, not just return the surviving digits.
            if unmapped:
                decoded = {char['fontname'] for char in page.chars
                           if char['text'].strip() and not _UNDECODED.search(char['text'])}
                if unmapped <= decoded:
                    return dict(text=alternate, pdf_text_recovered=True)
            elif any(char.isalpha() for char in alternate):
                return dict(text=alternate, pdf_text_recovered=True)
    except PdfminerException:
        pass
    return dict(text='', pdf_text_limited=True)


def aligned_statement_part(words, width, height, number):
    """Locate an explicitly captioned statement using actual PDF word boxes.

    This is not a ruled grid or OCR. A unique dated caption, two or more year
    headers, right-aligned amounts and a complete row are required. Ambiguity
    yields no representation; prose remains in the original physical page.
    Only the year explicitly covered by the dated caption gets a period link.
    """
    rows = []
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        box = [word[k] for k in ('x0', 'top', 'x1', 'bottom')]
        if (not isinstance(word.get('text'), str) or not word['text']
                or any(c in word['text'] for c in '\t\r\n')
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
            return None
        matches = [row for row in rows if abs((box[1]+box[3])/2-row['center']) <= 4
            and min(box[3], row['bottom'])-max(box[1], row['top']) >= min(box[3]-box[1], row['bottom']-row['top'])/2]
        if len(matches) > 1:
            return None
        if matches:
            matches[0]['words'].append(word)
        else:
            rows.append(dict(center=(box[1]+box[3])/2, top=box[1], bottom=box[3], words=[word]))
    for row in rows:
        row['words'].sort(key=lambda w: w['x0'])
        if any(a['x1'] > b['x0']+0.5 for a,b in zip(row['words'], row['words'][1:])):
            return None
        row['text'] = ' '.join(w['text'] for w in row['words'])
    captions = [i for i,r in enumerate(rows) if re.fullmatch(
        r'(?:合并|母公司)(?:利润表|现金流量表)(?:[（(]续[)）])?', re.sub(r'\s+', '', r['text']))]
    if len(captions) != 1:
        return None
    caption_index = captions[0]
    periods = []
    for i,row in enumerate(rows):
        value = re.sub(r'\s+', '', row['text'])
        match = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日至(\d{1,2})月(\d{1,2})日止期间', value)
        if not match or not caption_index < i <= caption_index+3:
            continue
        year,sm,sd,em,ed = map(int, match.groups())
        try:
            if date(year,sm,sd) > date(year,em,ed):
                return None
        except ValueError:
            return None
        periods.append((i,year,row['text'],value))
    if len(periods) != 1:
        return None
    period_index, year, period_quote, period = periods[0]
    header_choices = []
    for i,row in enumerate(rows):
        if not period_index < i or row['top'] > height*.4:
            continue
        groups = []
        j = 0
        while j < len(row['words']):
            word = row['words'][j]
            group = [word]
            if (re.fullmatch(r'\d{4}', word['text']) and j+1 < len(row['words'])
                    and row['words'][j+1]['text'] == '年'):
                group.append(row['words'][j+1]); j += 1
            groups.append(group); j += 1
        years = [g for g in groups if re.fullmatch(r'\d{4}年', ''.join(w['text'] for w in g))]
        others = [g for g in groups if g not in years]
        if (not 2 <= len(years) <= 4 or len(others) > 1
                or others and not re.fullmatch(r'附注[一二三四五六七八九十\d]*', ''.join(w['text'] for w in others[0]))):
            continue
        values = [int(''.join(w['text'] for w in g)[:4]) for g in years]
        edges = [g[-1]['x1'] for g in years]
        if (len(set(values)) != len(values) or values.count(year) != 1
                or any(b-a < 35 for a,b in zip(edges,edges[1:]))):
            return None
        header_choices.append((i, years, others, values, edges))
    if len(header_choices) != 1:
        return None
    header_index, years, notes, values, edges = header_choices[0]
    # Preserve literal words; inserted tabs denote coordinates, never amounts
    # or new labels. Note numbers keep a separate explicit header column.
    header_cells = [[], *notes, *years]
    displayed = [rows[i]['text'] for i in range(max(0,caption_index-1), header_index)]
    header_row = len(displayed)
    displayed.append('\t'.join(' '.join(w['text'] for w in cell) for cell in header_cells))
    word_cells = {str(header_row): header_cells}
    amount_token = re.compile(r'(?:[+−-]?\d[\d,]*\.\d{2}|\(\d[\d,]*\.\d{2}\)|-)')
    for row_index, row in enumerate(rows[header_index+1:], header_index+1):
        if row['top'] > height*.9:
            continue
        numeric = []
        for edge in edges:
            matches = [w for w in row['words'] if abs(w['x1']-edge) <= 1.25
                       and amount_token.fullmatch(w['text'])]
            if len(matches) != 1:
                break
            numeric.append(matches)
        if len(numeric) != len(years):
            continue
        note_cells = []
        if notes:
            note = notes[0]
            note_cells = [[w for w in row['words'] if note[0]['x0']-2 <= (w['x0']+w['x1'])/2 <= note[-1]['x1']+2]]
            if len(note_cells[0]) > 1:
                continue
        left = notes[0][0]['x0']-2 if notes else min(w['x0'] for cell in numeric for w in cell)-2
        labels = [w for w in row['words'] if w['x1'] <= left]
        assigned = labels + [w for cell in note_cells+numeric for w in cell]
        if (not labels or len(assigned) != len(row['words'])
                or len({id(w) for w in assigned}) != len(assigned)):
            continue
        # A wrapped prefix such as "归属于母公司股东的" must not disappear
        # and turn the next line "净利润" into the total-profit metric.
        prefix, upper = [], row
        for previous in reversed(rows[header_index+1:row_index]):
            if (upper['top']-previous['bottom'] > 12
                    or re.search(r'(?:分类|[)）:：])$', previous['text'])
                    or any(w['x1'] > left or amount_token.fullmatch(w['text']) for w in previous['words'])):
                break
            prefix = previous['words'] + prefix
            upper = previous
        labels = prefix + labels
        cells = [labels, *note_cells, *numeric]
        word_cells[str(len(displayed))] = cells
        displayed.append('\t'.join(' '.join(w['text'] for w in cell) for cell in cells))
    if len(word_cells) < 2:
        return None
    locator = f'第{number}页列式文本'
    return dict(locator=locator, text='\n'.join(displayed), pdf_table=dict(
        version=TEXT_TABLE_VERSION, representation='aligned_words', geometry_only=True,
        page=number, group=locator, bbox=[0,0,width,height], ambiguous_rows=[],
        header_row=header_row, year_columns={str(1+len(notes)+i):value for i,value in enumerate(values)},
        period_columns={str(1+len(notes)+values.index(year)):period_quote}, period_caption=period_quote,
        word_cells={key:[[{k:w[k] for k in ('text','x0','x1','top','bottom')} for w in cell]
                         for cell in cells] for key,cells in word_cells.items()}))


def _accounting_right(word):
    """Use the observed digit edge, retaining the actual closing glyph."""
    if word['text'] in (')', '）'):
        return word['x0']
    if word['text'].endswith((')', '）')):
        chars = word.get('chars', [])
        if (not chars or ''.join(char['text'] for char in chars) != word['text']
                or chars[-1]['text'] not in (')', '）') or len(chars) < 2):
            return word['x1']
        return chars[-2]['x1']
    return word['x1']


def _accounting_chunk(row, left, edge, next_left):
    chunk = [word for word in row['words'] if (word['x0']+word['x1'])/2 >= left
             and _accounting_right(word) <= edge+1.25]
    if not chunk or abs(_accounting_right(chunk[-1])-edge) > 1.25:
        return None
    for index, word in enumerate(chunk):
        if word['x1'] > edge+1.25:
            if (not word['text'].endswith((')', '）')) or word['x1'] >= next_left
                    or word['x1']-_accounting_right(word) > word['bottom']-word['top']):
                return None
        if word['text'] in (')', '）'):
            if index == 0 or abs(word['x0']-chunk[index-1]['x1']) > .5:
                return None
    token = ''.join(word['text'] for word in chunk)
    if not re.fullmatch(r'(?:[+−-]?\d[\d,]*(?:\.\d+)?|[（(]\d[\d,]*(?:\.\d+)?[）)]|-)', token):
        return None
    return chunk


def duration_statement_part(words, width, height, number, *, accounting_parentheses=False):
    """Preserve unruled statements with explicit stacked duration columns.

    Numbered captions, aligned year/end-date/month-count headers and complete
    right-aligned amount rows are required. Literal header words, coordinates,
    signs, integer amounts and note columns remain separate. No dates, units,
    missing values or financial applicability are supplied here.
    """
    rows=[]
    for word in sorted(words,key=lambda w:(w['top'],w['x0'])):
        box=[word.get(k) for k in ('x0','top','x1','bottom')]
        if (not isinstance(word.get('text'),str) or not word['text']
                or any(c in word['text'] for c in '\t\r\n')
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in box)
                or not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height):
            return None
        hits=[r for r in rows if abs((box[1]+box[3])/2-r['center'])<=4
            and min(box[3],r['bottom'])-max(box[1],r['top'])>=min(box[3]-box[1],r['bottom']-r['top'])/2]
        if len(hits)>1:return None
        if hits:hits[0]['words'].append(word)
        else:rows.append(dict(center=(box[1]+box[3])/2,top=box[1],bottom=box[3],words=[word]))
    for row in rows:
        row['words'].sort(key=lambda w:w['x0'])
        if any(a['x1']>b['x0']+.5 for a,b in zip(row['words'],row['words'][1:])):return None
        row['text']=' '.join(w['text'] for w in row['words'])
    captions=[i for i,r in enumerate(rows) if re.fullmatch(
        r'(?:[一二三四五六七八九十\d]+[、.．])?(?:合并|母公司)(?:利润表|现金流量表)',re.sub(r'\s+','',r['text']))]
    if len(captions)!=1:return None
    caption=captions[0]
    if accounting_parentheses:
        # Join only complete literal header phrases split into PDF words.
        # Amount rows and missing header components are never joined/filled.
        patterns=(r'截至\d{4}年',r'\d{1,2}月\d{1,2}日止',r'\d{1,2}个月期间')
        for row in rows[caption+1:caption+6]:
            original=row['words'];merged=[];position=0
            while position<len(original):
                found=None
                for end in range(position+1,min(len(original),position+7)+1):
                    chunk=original[position:end];text=''.join(word['text'] for word in chunk)
                    if any(re.fullmatch(pattern,text) for pattern in patterns):
                        if all(b['x0']-a['x1']<=5 for a,b in zip(chunk,chunk[1:])):found=(end,chunk,text)
                if found is None:merged.append(original[position]);position+=1;continue
                end,chunk,text=found
                merged.append(dict(text=text,x0=chunk[0]['x0'],x1=chunk[-1]['x1'],
                    top=min(word['top'] for word in chunk),bottom=max(word['bottom'] for word in chunk),
                    chars=[char for word in chunk for char in word.get('chars',[])]))
                position=end
            row['words']=merged
    headers=[]
    for i,row in enumerate(rows):
        if not caption<i<=caption+5 or row['top']>height*.4:continue
        years=[w for w in row['words'] if re.fullmatch(r'截至\d{4}年',w['text'])]
        if not 2<=len(years)<=4:continue
        columns=[]
        for year_word in years:
            end=[w for r in rows[i+1:i+3] for w in r['words']
                 if re.fullmatch(r'\d{1,2}月\d{1,2}日止',w['text'])
                 and abs(w['x1']-year_word['x1'])<=1.25 and 0<w['top']-year_word['top']<=22]
            if len(end)!=1:break
            count=[w for r in rows[i+2:i+4] for w in r['words']
                   if re.fullmatch(r'\d{1,2}个月期间',w['text'])
                   and abs(w['x1']-year_word['x1'])<=1.25 and 0<w['top']-end[0]['top']<=22]
            if len(count)!=1:break
            literal=' '.join(w['text'] for w in (year_word,end[0],count[0]))
            from .data_requirements import reported_duration_label
            if not reported_duration_label(literal):break
            columns.append([year_word,end[0],count[0]])
        if len(columns)==len(years):headers.append((i,columns))
    if len(headers)!=1:return None
    header_index,columns=headers[0]
    years=[int(c[0]['text'][2:6]) for c in columns]
    edges=[c[0]['x1'] for c in columns]
    if len(set(years))!=len(years) or any(b-a<35 for a,b in zip(edges,edges[1:])):return None
    last_header=max(w['bottom'] for c in columns for w in c)
    notes=[w for r in rows[header_index:header_index+4] for w in r['words']
           if re.fullmatch(r'附注[一二三四五六七八九十\d]*',w['text']) and w['x1']<min(c[0]['x0'] for c in columns)]
    if len(notes)>1:return None
    note_columns=[[notes[0]]] if notes else []
    header_cells=[[],*note_columns,*columns]
    displayed=[rows[caption]['text'],'\t'.join(' '.join(w['text'] for w in c) for c in header_cells)]
    word_cells={'1':header_cells}
    amount=re.compile(r'(?:[+−-]?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\)|-)')
    for index,row in enumerate(rows):
        if row['top']<=last_header or row['top']>height*.9:continue
        if all(re.fullmatch(r'[（(](?:未经审计|经审计)[）)]',w['text']) for w in row['words']):continue
        numeric=[]
        for col,edge in enumerate(edges):
            left=(edges[col-1]+edge)/2 if col else (notes[0]['x1']+columns[0][0]['x0'])/2 if notes else columns[0][0]['x0']-40
            if accounting_parentheses:
                right = (edge+edges[col+1])/2 if col+1<len(edges) else width
                chunk = _accounting_chunk(row,left,edge,right)
            else:
                chunk=[w for w in row['words'] if (w['x0']+w['x1'])/2>=left and w['x1']<=edge+1.25]
                if not chunk or abs(chunk[-1]['x1']-edge)>1.25 or not amount.fullmatch(''.join(w['text'] for w in chunk)):break
            if chunk is None:break
            numeric.append(chunk)
        if len(numeric)!=len(columns):continue
        note_cells=[]
        if notes:
            note=notes[0]
            note_cells=[[w for w in row['words'] if note['x0']-2<=(w['x0']+w['x1'])/2<=note['x1']+2]]
            if len(note_cells[0])>1:continue
        left=notes[0]['x0']-2 if notes else min(w['x0'] for c in numeric for w in c)-2
        labels=[w for w in row['words'] if w['x1']<=left]
        assigned=labels+[w for c in note_cells+numeric for w in c]
        if not labels or len(assigned)!=len(row['words']) or len({id(w) for w in assigned})!=len(assigned):continue
        # Retain a wrapped qualifier; it must never turn parent/adjusted profit
        # into the total-profit row merely because it sits on the prior line.
        prefix=[];upper=row
        for previous in reversed(rows[header_index+1:index]):
            if (upper['top']-previous['bottom']>12 or re.search(r'(?:分类|[)）:：])$',previous['text'])
                    or any(w['x1']>left or amount.fullmatch(w['text']) for w in previous['words'])):break
            prefix=previous['words']+prefix;upper=previous
        cells=[prefix+labels,*note_cells,*numeric]
        word_cells[str(len(displayed))]=cells
        displayed.append('\t'.join(' '.join(w['text'] for w in c) for c in cells))
    if len(word_cells)<2:return None
    periods={str(1+len(notes)+i):' '.join(w['text'] for w in c) for i,c in enumerate(columns)}
    locator=f'第{number}页期间列式文本'
    return dict(locator=locator,text='\n'.join(displayed),pdf_table=dict(
        version=TEXT_TABLE_VERSION,representation='aligned_words',geometry_only=True,
        page=number,group=locator,bbox=[0,0,width,height],ambiguous_rows=[],header_row=1,
        year_columns={str(1+len(notes)+i):year for i,year in enumerate(years)},
        period_columns=periods,period_captions=periods,period_caption=next(iter(periods.values())),
        duration_columns=True,word_cells={key:[[{k:w[k] for k in ('text','x0','x1','top','bottom')}
            for w in c] for c in cells] for key,cells in word_cells.items()}))


def point_statement_part(words, width, height, number, *, continuation=None):
    """Represent an explicitly dated balance sheet using observed columns.

    A continuation requires the previous page's caption and identical literal
    dates. No previous date, note number or missing amount is inferred.
    """
    rows=[]
    for word in sorted(words,key=lambda value:(value['top'],value['x0'])):
        box=[word.get(key) for key in ('x0','top','x1','bottom')]
        if (not isinstance(word.get('text'),str) or not word['text'] or any(c in word['text'] for c in '\t\r\n')
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in box)
                or not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height):return None
        matches=[row for row in rows if abs((box[1]+box[3])/2-row['center'])<=4
                 and min(box[3],row['bottom'])-max(box[1],row['top'])>=min(box[3]-box[1],row['bottom']-row['top'])/2]
        if len(matches)>1:return None
        if matches:matches[0]['words'].append(word)
        else:rows.append(dict(center=(box[1]+box[3])/2,top=box[1],bottom=box[3],words=[word]))
    for row in rows:
        row['words'].sort(key=lambda value:value['x0'])
        if any(a['x1']>b['x0']+.5 for a,b in zip(row['words'],row['words'][1:])):return None
        row['text']=' '.join(word['text'] for word in row['words'])
    captions=[row for row in rows if re.fullmatch(r'(?:[一二三四五六七八九十\d]+[、.．])?(?:合并|母公司)资产负债表',re.sub(r'\s+','',row['text']))]
    if len(captions)>1 or not captions and not continuation:return None
    if captions:
        caption=dict(text=captions[0]['text'],part=f'第{number}页')
    else:
        if continuation.get('page')!=number-1:return None
        caption=continuation['caption']
    headers=[]
    for index,row in enumerate(rows):
        if row['top']>height*.4 or not re.match(r'^(?:资产|负债|所有者权益)\s',row['text']):continue
        groups=[]
        for start in range(len(row['words'])):
            for end in range(start+1,min(len(row['words']),start+9)+1):
                group=row['words'][start:end];text=''.join(word['text'] for word in group)
                match=re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日',text)
                if not match:continue
                try:value=date(*map(int,match.groups())).isoformat()
                except ValueError:return None
                groups.append((group,value))
        if len(groups)!=2 or groups[0][0][-1]['x1']+35>groups[1][0][-1]['x1']:continue
        if set(map(id,groups[0][0])) & set(map(id,groups[1][0])):return None
        notes=[word for word in row['words'] if re.fullmatch(r'附注[一二三四五六七八九十\d]*',word['text'])]
        if len(notes)>1:return None
        if len({value for _,value in groups})!=2:return None
        if continuation and not captions and [value for _,value in groups]!=continuation['dates']:return None
        headers.append((index,row,groups,notes))
    if len(headers)!=1:return None
    index,header,groups,notes=headers[0];edges=[group[-1]['x1'] for group,_ in groups]
    displayed=[caption['text'],'\t'.join(['',*([notes[0]['text']] if notes else []),*[' '.join(word['text'] for word in group) for group,_ in groups]])]
    cells={'1':[[],*([[notes[0]]] if notes else []),*[group for group,_ in groups]]}
    for row in rows[index+1:]:
        if row['top']>height*.95:continue
        numeric=[]
        for column,edge in enumerate(edges):
            left=(edges[column-1]+edge)/2 if column else (notes[0]['x1']+groups[0][0][0]['x0'])/2 if notes else groups[0][0][0]['x0']-40
            chunk=_accounting_chunk(row,left,edge,(edge+edges[column+1])/2 if column+1<len(edges) else width)
            if chunk is None:break
            numeric.append(chunk)
        if len(numeric)!=len(groups):continue
        note_cells=([[word for word in row['words'] if notes[0]['x0']-2<=(word['x0']+word['x1'])/2<=notes[0]['x1']+2]] if notes else [])
        if note_cells and len(note_cells[0])>1:continue
        left=notes[0]['x0']-2 if notes else min(word['x0'] for chunk in numeric for word in chunk)-2
        labels=[word for word in row['words'] if word['x1']<=left]
        assigned=labels+[word for chunk in note_cells+numeric for word in chunk]
        if not labels or len(assigned)!=len(row['words']) or len({id(word) for word in assigned})!=len(assigned):continue
        row_cells=[labels,*note_cells,*numeric];cells[str(len(displayed))]=row_cells
        displayed.append('\t'.join(' '.join(word['text'] for word in chunk) for chunk in row_cells))
    if len(cells)<2:return None
    locator=f'第{number}页时点列式文本'
    return dict(locator=locator,text='\n'.join(displayed),pdf_table=dict(version=TEXT_TABLE_VERSION,
        representation='aligned_words',geometry_only=True,page=number,group=locator,bbox=[0,0,width,height],
        ambiguous_rows=[],header_row=1,point_columns=True,statement_caption=caption,
        dates=[value for _,value in groups],period_columns={str(1+len(notes)+i):value for i,(_,value) in enumerate(groups)},
        word_cells={key:[[{field:word[field] for field in ('text','x0','x1','top','bottom')} for word in chunk] for chunk in value] for key,value in cells.items()}))


def _display_cell(value):
    # Retain wrapping as spaces and preserve every digit, sign and empty column.
    return '' if value is None else value.replace('\r\n', '\n').replace('\n', ' ')


def _boundaries(row):
    if not row.cells or any(cell is None for cell in row.cells):
        return None
    cells = row.cells
    if any(abs(left[2]-right[0]) > 0.5 for left, right in zip(cells, cells[1:])):
        return None
    return [round(cells[0][0], 4), *[round(cell[2], 4) for cell in cells]]


def _same_columns(first, second):
    # PDF rules can differ by sub-point stroke/snapping coordinates across
    # pages. Keep order and every boundary; never permit a whole-cell shift.
    return len(first) == len(second) and all(abs(a-b) <= COLUMN_DRIFT_POINTS for a, b in zip(first, second))


def physical_rule_settings(page):
    """Separate actual rules from unstroked cell-background rectangles.

    Some PDFs paint both cell backgrounds and narrow border strips as filled
    rectangles. Treating every background edge as a rule creates phantom
    padding columns. Stroked outlines, actual lines, and <=1pt ink strips are
    physical geometry; text position never supplies or merges a boundary.
    """
    rules = list(getattr(page, 'lines', []))
    rules.extend(rect for rect in getattr(page, 'rects', []) if rect.get('stroke') is True
                 or min(rect['width'], rect['height']) <= 1
                 and max(rect['width'], rect['height']) >= 3)
    if len(rules) < 2:
        return None
    return dict(vertical_strategy='explicit', horizontal_strategy='explicit',
                explicit_vertical_lines=rules, explicit_horizontal_lines=rules)


def _layout_parts(page, plain_text, number, previous, point_previous, version):
    """Commit one page's geometry together; a failed page cannot bridge tables."""
    parts=[]
    statement_details = version in STATEMENT_DETAIL_EXTRACT_VERSIONS
    # Only physical rules. Never use text-alignment inference or OCR.
    settings = (physical_rule_settings(page) if version in FINANCIAL_CONTEXT_EXTRACT_VERSIONS
                else {'vertical_strategy': 'lines', 'horizontal_strategy': 'lines'})
    found = page.find_tables(settings) if settings is not None else []
    page_tables = []
    for index, table in enumerate(found, 1):
        rows = table.extract()
        if not rows or len(rows) != len(table.rows):
            continue
        candidates = [_boundaries(row) for row in table.rows]
        usable = [bounds for bounds in candidates if bounds and len(bounds) >= 3]
        if not usable:
            continue
        columns = max(usable, key=lambda bounds: sum(_same_columns(bounds, other) for other in usable))
        width = len(columns)-1
        if any(len(row) != width for row in rows):
            continue
        ambiguous = [i for i, bounds in enumerate(candidates)
                     if not bounds or not _same_columns(bounds, columns)]
        displayed = ['\t'.join(_display_cell(cell) for cell in row) for row in rows]
        if any('\t' in cell or '\r' in cell for row in rows for cell in row if cell is not None):
            continue
        locator = f'第{number}页表{index}'
        group = locator
        # Adjacent bottom/top grids can continue only without a new
        # explicit header. Geometry links cells, not accounting scope.
        new_header = any(re.sub(r'\s+', '', line.split('\t')[0]) in ('项目', '科目', '项目名称')
                         for line in displayed)
        if (index == 1 and previous and previous['page'] == number-1
                and previous['bottom'] >= previous['height']*0.78
                and table.bbox[1] <= page.height*0.2 and not new_header
                and _same_columns(previous['columns'], columns)):
            group = previous['group']
        metadata = {'version': TABLE_VERSION, 'page': number, 'group': group,
            'bbox': [round(value, 4) for value in table.bbox], 'column_bounds': columns,
            'ambiguous_rows': ambiguous, 'geometry_only': True}
        parts.append({'locator': locator, 'text': '\n'.join(displayed), 'pdf_table': metadata})
        page_tables.append({'page': number, 'bottom': table.bbox[3], 'height': page.height,
                            'columns': columns, 'group': group})
    previous = page_tables[-1] if page_tables else None
    caption_prefix = r'(?:[一二三四五六七八九十\d]+[、.．]\s*)?' if version in DURATION_EXTRACT_VERSIONS else ''
    if version in ALIGNED_EXTRACT_VERSIONS and re.search(
            r'(?m)^'+caption_prefix+r'(?:合并|母公司)(?:利润表|现金流量表)(?:\s*[（(]续[)）])?\s*$', plain_text):
        ruled = [part for part in parts if part.get('pdf_table', {}).get('page') == number
            and len(part['pdf_table'].get('column_bounds', [])) >= 4
            and any(re.search(r'营业收入|净利润|经营活动产生的现金流量净', line.split('\t')[0])
                    for line in part['text'].splitlines())]
        if not ruled:
            words=page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False,**({'return_chars':True} if statement_details else {}))
            aligned = aligned_statement_part(words, page.width, page.height, number)
            if aligned is None and version in DURATION_EXTRACT_VERSIONS:
                aligned = duration_statement_part(words,page.width,page.height,number,accounting_parentheses=statement_details)
            if aligned is not None:
                parts.append(aligned)
    if statement_details:
        point_caption=re.search(r'(?m)^'+caption_prefix+r'(?:合并|母公司)资产负债表\s*$',plain_text)
        other_statement=re.search(r'(?m)^'+caption_prefix+r'(?:合并|母公司)(?:利润表|现金流量表|所有者权益变动表)\s*$',plain_text)
        point=None
        if (point_caption or point_previous) and not other_statement:
            words=page.extract_words(x_tolerance=1,y_tolerance=2,keep_blank_chars=False,return_chars=True)
            point=point_statement_part(words,page.width,page.height,number,continuation=point_previous)
        if point is not None:
            parts.append(point)
            point_previous=dict(page=number,caption=point['pdf_table']['statement_caption'],dates=point['pdf_table']['dates'])
        else:point_previous=None
    return parts,previous,point_previous


def extract_parts(payload, *, extract_version=None):
    from pypdf import PdfReader
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException
    from .contracts import ContractError

    version = EXTRACT_VERSION if extract_version is None else extract_version
    if version not in TABLE_EXTRACT_VERSIONS:
        raise ContractError('PDF表格提取版本未登记')
    reader = PdfReader(io.BytesIO(payload))
    if len(reader.pages) > MAX_PAGES:
        raise ContractError('PDF超过500页，请按研究需要拆分原件')
    parts, previous, point_previous = [], None, None
    with pdfplumber.open(io.BytesIO(payload)) as document:
        if len(document.pages) != len(reader.pages):
            raise ContractError('PDF正文与表格页数不一致，未采用表格')
        for number, (plain, page) in enumerate(zip(reader.pages, document.pages), 1):
            try:
                literal = {'locator': f'第{number}页'}
                literal.update(_decoded_page(plain, page) if version == EXTRACT_VERSION else
                               dict(text=plain.extract_text() or ''))
                parts.append(literal)
                if literal.get('pdf_text_limited'):
                    previous = point_previous = None
                    continue
                try:
                    page_parts, next_previous, next_point = _layout_parts(
                        page, literal['text'], number, previous, point_previous, version)
                except PdfminerException:
                    # The independently decoded literal text remains useful.
                    # Do not invent grids or carry a statement across this page.
                    literal['pdf_layout_limited'] = True
                    previous = point_previous = None
                else:
                    parts.extend(page_parts)
                    previous, point_previous = next_previous, next_point
            finally:
                page.close()
    return parts


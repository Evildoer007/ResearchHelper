"""ArtifactExporter: real, atomic offline files from one presentation document."""
from contextlib import contextmanager
from decimal import Decimal
from io import BytesIO,StringIO
from pathlib import Path
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
from .contracts import ContractError,canonical
from .report_designer import design, PROFILES, DEFAULT_TEMPLATE_ID, read_presentation_document


# XML 1.0 permits tabs and line breaks. Show other control characters
# as escapes in Office files while keeping the frozen HTML dataset intact.
_XML_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')
_SIGNED_DECIMAL = re.compile(r'[+-](?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_EXCEL_MIN = Decimal('2.2250738585072014e-308')
_EXCEL_MAX = Decimal('9.99999999999999e307')
_XLSX_CELL_TEXT_LIMIT = 32_767
_XLSX_CELL_LINE_FEEDS = 253

FORMATS={'html':'text/html','pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','csv':'text/csv'}

# Advance when a converter changes the output of the same saved HTML.
_CONVERSION_REVISION = 1
_WORD_CONVERSION_REVISION = 2
_WORKBOOK_CONVERSION_REVISION = 2
_FORMAT_SIGNATURES = {'pdf': (b'%PDF-', 'PDF文件无效'),
                      'docx': (b'PK', 'Office文件无效'), 'xlsx': (b'PK', 'Office文件无效'),
                      'csv': (b'\xef\xbb\xbf', 'CSV文件无效')}


def capabilities():
    return dict(profiles=PROFILES,templates=[dict(id='report-'+profile,output_type='report',profile=profile,label=label) for profile,label in PROFILES.items()],formats=FORMATS,default_template=DEFAULT_TEMPLATE_ID,default_profile='standard',default_format='html',local_only=True)


def data_rows(content):
    from .publication_content import publication_dataset
    return publication_dataset(content)['rows']


def _layout_segments(blocks):
    """Keep the template's figure panels together across static formats."""
    index=0
    while index<len(blocks):
        block=blocks[index];group=block.get('figure_group')
        if block.get('reading_group'):
            reading=block['reading_group'];panels={}
            while index<len(blocks) and blocks[index].get('reading_group')==reading:
                row=blocks[index];panels.setdefault(row['reading_column'],[]).append({key:value for key,value in row.items() if key not in ('reading_group','reading_column')});index+=1
            yield dict(kind='reading_columns',columns=len(panels),panels=list(panels.values()))
        elif group:
            panels={};columns=block.get('figure_columns',1)
            while index<len(blocks) and blocks[index].get('figure_group')==group:
                row=blocks[index];panels.setdefault(row['figure_index'],[]).append({key:value for key,value in row.items() if key not in ('figure_group','figure_columns','figure_index')});index+=1
            yield dict(kind='panels',columns=columns,panels=list(panels.values()))
        elif block.get('calculation_group'):
            group=block['calculation_group'];items=[]
            while index<len(blocks) and blocks[index].get('calculation_group')==group:
                items.append({key:value for key,value in blocks[index].items() if key!='calculation_group'});index+=1
            yield dict(kind='calculation',blocks=items)
        else:
            yield block;index+=1


def _category_values_table(spec):
    """Keep every value when a category label cannot fit a full paper page."""
    auto = spec.get('auto_visual', {})
    data = auto.get('data', spec.get('data', {}))
    unit = data.get('unit', '')
    labels = data['labels']
    scheme = auto.get('scheme', spec.get('scheme'))
    if scheme == 'heatmap':
        names, columns, values = data['rows'], labels, data['matrix']
    elif scheme == 'dumbbell':
        names, columns, values = labels, data['periods'], data['pairs']
    else:
        series = data.get('series') or [dict(name=data.get('name', '观测值'), values=data['values'])]
        names, columns = labels, [row['name'] or '观测值' for row in series]
        values = zip(*(row['values'] for row in series))
    from .publication_graphics import display_label
    suffix = f'（{unit}）' if unit else ''
    rows = [['对象', *(display_label(data, label)+suffix for label in columns)]]
    rows.extend([display_label(data, name), *('未返回' if value is None else str(value) for value in row)]
                for name, row in zip(names, values))
    return dict(kind='table', rows=rows)


def _fit_period_value_rows(rows, width):
    """Reflow the compact series table in complete, chronological pairs."""
    from .publication_graphics import label_width

    pairs_per_row = len(rows[0]) // 2
    cell_width = max(label_width(cell) for row in rows for cell in row) + 6
    visible_pairs = max(1, int(width / (2 * cell_width)))
    if visible_pairs >= pairs_per_row:
        return rows
    pairs = []
    for row in rows[1:]:
        padded = row + ['']*(2*pairs_per_row-len(row))
        pairs.extend((period,value) for period,value in zip(padded[::2],padded[1::2])
                     if period != '' or value != '')
    result = [['期间', '原值'] * visible_pairs]
    for start in range(0, len(pairs), visible_pairs):
        cells = [cell for pair in pairs[start:start+visible_pairs] for cell in pair]
        result.append(cells + ['']*(2*visible_pairs-len(cells)))
    return result



def _reference_anchors(blocks, *, word=False):
    """Office/PDF-safe local destinations; duplicate targets remain unlinked."""
    counts = {}
    def collect(items):
        for block in items:
            anchor = block.get('anchor')
            if isinstance(anchor, str) and anchor:
                counts[anchor] = counts.get(anchor, 0) + 1
            if word:
                for targets in block.get('row_anchors', {}).values():
                    for target in targets:
                        counts[target] = counts.get(target, 0) + 1
            if isinstance(block.get('blocks'), list):
                collect(block['blocks'])
    collect(blocks)
    return {anchor: dict(name='rh_' + hashlib.sha256(anchor.encode()).hexdigest()[:32], id=str(index))
        for index, (anchor, count) in enumerate(counts.items(), 1) if count == 1}


def _inline_references(block, anchors):
    links = block.get('inline_links')
    if (not isinstance(links, list) or not links or any(not isinstance(link, dict)
            or set(link) != {'text', 'target'} or not isinstance(link['text'], str) or not link['text']
            or not isinstance(link['target'], str) for link in links)
            or block.get('text') != '、'.join(link['text'] for link in links)):
        return None
    return [dict(text=link['text'], target=anchors.get(link['target'])) for link in links]


def _word_reference_blocks(blocks):
    """Resolve numeric links to content already visible in a detailed report."""
    requested = {run['target'] for block in blocks for run in block.get('inline_runs') or ()
                 if isinstance(run, dict) and isinstance(run.get('target'), str)}
    if not requested:
        return blocks
    result = []
    located = {block['anchor'] for block in blocks if isinstance(block.get('anchor'), str)}

    def locate(target):
        if target not in requested or target in located:
            return None
        located.add(target)
        return target

    for original in blocks:
        block = dict(original)
        if (block.get('kind') in ('heading', 'paragraph') and block.get('calculation_ref')
                and not block.get('anchor')):
            target = locate('calculation:' + block['calculation_ref'])
            if target:
                block['anchor'] = target
        if block.get('kind') == 'table':
            rows = block.get('observation_rows') or []
            calculations = block.get('calculation_rows') or []
            row_anchors = {}
            if len(rows) == len(block['rows']):
                row_anchors = {index: [target] for index, refs in enumerate(rows) if len(refs) == 1
                               and (target := locate('observation:' + refs[0]))}
            if len(calculations) == len(block['rows']) - 1:
                for index, calc in enumerate(calculations, 1):
                    if target := locate('calculation:' + calc['id']):
                        row_anchors.setdefault(index, []).append(target)
            if row_anchors:
                block['row_anchors'] = row_anchors
        result.append(block)
    return result


def _word_inline_runs(block, anchors):
    runs = block.get('inline_runs')
    if (isinstance(runs, list) and runs and all(isinstance(run, dict)
            and isinstance(run.get('text'), str) and (run.get('target') is None or isinstance(run['target'], str))
            for run in runs) and ''.join(run['text'] for run in runs) == block['text']):
        return [dict(text=run['text'], target=anchors.get(run['target'])) for run in runs]
    links = _inline_references(block, anchors)
    if not links:
        return []
    result = []
    for link in links:
        if result:
            result.append(dict(text='、', target=None))
        result.append(link)
    return result


def _pdf_body_size():
    from reportlab.lib.pagesizes import A4
    return A4[0]-56, A4[1]-32-29-12


def _pdf(document):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,Table,KeepTogether
    from .publication_graphics import FONT,EvidenceChart
    from .publication_math import MathFormula
    output=BytesIO();styles={
        'masthead':ParagraphStyle('rh-masthead',fontName=FONT,fontSize=9,leading=12,spaceAfter=3,
                                  keepWithNext=True,textColor=colors.HexColor('#902F3B')),
        'title':ParagraphStyle('rh-title',fontName=FONT,fontSize=13.5,leading=18,spaceAfter=7,textColor=colors.HexColor('#252525')),
        'heading':ParagraphStyle('rh-heading',fontName=FONT,fontSize=10.8,leading=14,spaceBefore=6,spaceAfter=4,keepWithNext=True),
        'paragraph':ParagraphStyle('rh-body',fontName=FONT,fontSize=9,leading=12,spaceAfter=4,wordWrap='CJK'),
        'caption':ParagraphStyle('rh-caption',fontName=FONT,fontSize=9,leading=12,spaceBefore=4,spaceAfter=3,keepWithNext=True),
        'cell':ParagraphStyle('rh-cell',fontName=FONT,fontSize=8.25,leading=10.5,wordWrap='CJK')}
    width, body_height = _pdf_body_size()
    chart_limit = body_height - 60
    anchors=_reference_anchors(document['blocks'])
    def paragraph_text(block):
        links=_inline_references(block,anchors)
        if links:
            text='、'.join('<link href="#'+link['target']['name']+'">'+html.escape(link['text'])+'</link>'
                if link['target'] else html.escape(link['text']) for link in links)
        else:text=html.escape(block['text'])
        value=block.get('anchor');anchor=anchors.get(value) if isinstance(value,str) else None
        return ('<a name="'+anchor['name']+'"/>' if anchor else '')+text
    def flow(blocks,available):
        result=[]
        for block in _layout_segments(blocks):
            kind=block['kind']
            if kind in styles:result.append(Paragraph(paragraph_text(block),styles[kind]))
            elif kind=='calculation':result.append(KeepTogether(flow(block['blocks'],available)))
            elif kind=='formula':result.append(MathFormula(block['mathml']))
            elif kind=='chart':
                chart = EvidenceChart(block['spec'], width=available, height=128 if available>300 else 96, palette=document['theme'])
                pages = list(chart.pages(chart_limit))
                result.extend(pages or flow([_category_values_table(block['spec'])], available))
            elif kind in ('panels','reading_columns'):
                panels=block['panels'];columns=min(block['columns'],len(panels));cell_width=available/columns
                cells=[flow(panel,cell_width-10) for panel in panels]
                table=Table([cells],colWidths=[cell_width]*columns,hAlign='LEFT')
                table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE' if kind=='reading_columns' else 'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
                if table.wrap(available, body_height)[1] > body_height:
                    # A one-row layout table cannot break across pages.
                    for panel in panels:
                        result.extend(flow(panel, available))
                else:
                    result.append(table)
            elif kind=='table':
                rows=block['rows'];columns=max(map(len,rows))
                paired_header = columns>=4 and rows[0] == ['期间','原值'] * (columns//2)
                if paired_header:
                    rows = _fit_period_value_rows(rows, available)
                    batches = [list(range(len(rows[0])))]
                else:
                    batches=[list(range(columns))] if columns<=7 else [[0]+list(range(start,min(start+6,columns))) for start in range(1,columns,6)]
                for indexes in batches:
                    cells=[[Paragraph(html.escape(str(row[i] if i<len(row) else '')),styles['cell']) for i in indexes] for row in rows]
                    table=LongTable(cells,colWidths=[available/len(indexes)]*len(indexes),repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=1)
                    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#F3F3F3')),('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#C8102E')),('LINEBELOW',(0,1),(-1,-1),.3,colors.HexColor('#DDDDDD')),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]));result.extend([table,Spacer(1,4)])
        return result
    def decorate(canvas,doc):
        canvas.saveState();canvas.setStrokeColor(colors.HexColor('#C8102E'));canvas.setLineWidth(.8);canvas.line(28,A4[1]-22,A4[0]-28,A4[1]-22)
        canvas.setFont(FONT,8.25);canvas.setFillColor(colors.HexColor('#6B6B6B'));canvas.drawRightString(A4[0]-28,15,str(doc.page));canvas.restoreState()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=28,rightMargin=28,topMargin=32,bottomMargin=29,title=document['title'],author='ResearchHelper').build(flow(document['blocks'],width),onFirstPage=decorate,onLaterPages=decorate)
    return output.getvalue()


def _omml(xml):
    from lxml import etree
    ns='http://schemas.openxmlformats.org/officeDocument/2006/math'
    def element(tag):return etree.Element('{'+ns+'}'+tag)
    def convert(n):
        name=etree.QName(n).localname;children=list(n)
        if name in ('mfrac','msub','msup') and len(children)==2:
            tag,a,b={'mfrac':('f','num','den'),'msub':('sSub','e','sub'),'msup':('sSup','e','sup')}[name]
            node=element(tag)
            for part,child in zip((a,b),children):
                box=element(part)
                for item in convert(child):box.append(item)
                node.append(box)
            return [node]
        if children:return [item for child in children for item in convert(child)]
        run=element('r');text=element('t');text.text=n.text or '';run.append(text);return [run]
    outer=element('oMath')
    root=etree.fromstring(xml.encode())
    for item in convert(root):outer.append(item)
    return outer


def _office_text(value):
    return _XML_CONTROL.sub(lambda character: f'\\u{ord(character[0]):04X}', value)


def _csv_cell(value, *, observation=False):
    if not isinstance(value, str):
        return value
    value = _office_text(value)
    start = 0
    while start < len(value) and (value[start].isspace() or unicodedata.category(value[start]) == 'Cf'):
        start += 1
    prefix = unicodedata.normalize('NFKC', value[start:start+1])
    if (prefix in ('=', '+', '-', '@', '−') or any(char in value[:start] for char in '\t\r\n')):
        if observation and _SIGNED_DECIMAL.fullmatch(value.strip(' ')):
            return value
        # Use a visible escape. A spreadsheet can remove a CSV text
        # qualifier such as an apostrophe when saving the file again.
        return '\\' + value
    return value


def _xlsx_cell(value):
    if isinstance(value, str):
        return _office_text(value)
    if type(value) in (int, float):
        original = str(value)
        number = Decimal(original)
        if (not number.is_finite() or number != Decimal(format(number, '.15g'))
                or number and not _EXCEL_MIN <= abs(number) <= _EXCEL_MAX):
            return original
    return value


def _xlsx_text_fits(value):
    return (value.count('\n') <= _XLSX_CELL_LINE_FEEDS
            and len(value.encode('utf-16-le')) // 2 <= _XLSX_CELL_TEXT_LIMIT)


def _xlsx_readable_fragments(value):
    """Split at Unicode character boundaries without dropping separators."""
    # The fragment column wraps within Excel's row-height limit.
    start = units = breaks = 0
    for index, character in enumerate(value):
        size = 2 if ord(character) > 0xffff else 1
        line_break = character in '\r\n'
        if units + size > 720 or breaks + line_break > 12:
            yield value[start:index]
            start, units, breaks = index, 0, 0
        units += size
        breaks += line_break
    yield value[start:]


def _docx(document):
    from docx import Document
    from docx.shared import Mm,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from .publication_graphics import EvidenceChart, chart_png
    output=BytesIO();doc=Document();section=doc.sections[0];section.page_width=Mm(210);section.page_height=Mm(297)
    section.left_margin=section.right_margin=Mm(12);section.top_margin=section.bottom_margin=Mm(12)
    for name in ('Normal','Title','Heading 1','Heading 2','Heading 3'):
        style=doc.styles[name];style.font.name='Arial';style.font.color.rgb=RGBColor.from_string('252525');style.font.size=Pt(9 if name=='Normal' else 13.5 if name=='Title' else 10.8)
        rpr=style.element.get_or_add_rPr();rfonts=rpr.find(qn('w:rFonts'))
        if rfonts is None:rfonts=OxmlElement('w:rFonts');rpr.append(rfonts)
        rfonts.set(qn('w:eastAsia'),'宋体');style.paragraph_format.space_after=Pt(4);style.paragraph_format.line_spacing=1.1
        for border in style.element.xpath('.//w:pBdr'):border.getparent().remove(border)
    blocks = _word_reference_blocks(document['blocks']) if document.get('profile') == 'detailed' else document['blocks']
    anchors = _reference_anchors(blocks, word=True)
    chart_limit = Mm(273).pt - 60
    def plot(spec, available):
        return EvidenceChart(spec, width=available*2.835, height=128 if available>120 else 115, palette=document['theme'])

    def add_bookmark(paragraph, target):
        if not (anchor := anchors.get(target)):
            return
        start=OxmlElement('w:bookmarkStart');start.set(qn('w:id'),anchor['id']);start.set(qn('w:name'),anchor['name'])
        end=OxmlElement('w:bookmarkEnd');end.set(qn('w:id'),anchor['id'])
        paragraph._p.insert(1 if paragraph._p.find(qn('w:pPr')) is not None else 0,start)
        paragraph._p.append(end)

    def add_paragraph(container, block, style=None):
        paragraph=container.add_paragraph(style=style)
        runs = _word_inline_runs(block, anchors)
        if runs:
            for run in runs:
                if not run['target']:
                    paragraph.add_run(_office_text(run['text']))
                    continue
                hyperlink=OxmlElement('w:hyperlink')
                hyperlink.set(qn('w:anchor'),run['target']['name'])
                hyperlink.set(qn('w:history'),'1')
                text_run=OxmlElement('w:r');properties=OxmlElement('w:rPr')
                color=OxmlElement('w:color');color.set(qn('w:themeColor'),'hyperlink');color.set(qn('w:val'),'0563C1')
                underline=OxmlElement('w:u');underline.set(qn('w:val'),'single')
                properties.extend((color,underline));text_run.append(properties)
                text=OxmlElement('w:t');text.text=_office_text(run['text']);text_run.append(text)
                hyperlink.append(text_run);paragraph._p.append(hyperlink)
        else:
            paragraph.add_run(_office_text(block['text']))
        add_bookmark(paragraph,block.get('anchor'))
        return paragraph
    def populate(container,blocks,available):
        for block in _layout_segments(blocks):
            kind=block['kind']
            if kind=='masthead':
                paragraph=add_paragraph(container,block)
                paragraph.paragraph_format.keep_with_next=True
                for run in paragraph.runs:
                    run.bold=True
                    run.font.color.rgb=RGBColor.from_string('902F3B')
            elif kind=='title':add_paragraph(container,block,'Title')
            elif kind=='heading':add_paragraph(container,block,'Heading '+str(min(3,max(1,block['level']-1))))
            elif kind in ('paragraph','caption'):add_paragraph(container,block)
            elif kind=='calculation':
                first=len(container.paragraphs);populate(container,block['blocks'],available)
                paragraphs=container.paragraphs[first:]
                for index,paragraph in enumerate(paragraphs):
                    paragraph.paragraph_format.keep_together=True
                    paragraph.paragraph_format.keep_with_next=index<len(paragraphs)-1
            elif kind=='formula':container.add_paragraph()._p.append(_omml(block['mathml']))
            elif kind=='chart':
                pages = list(plot(block['spec'], available).pages(chart_limit))
                if not pages:
                    populate(container, [_category_values_table(block['spec'])], available)
                for page in pages:
                    paragraph=container.add_paragraph();paragraph.add_run().add_picture(BytesIO(chart_png(page)),width=Mm(available))
            elif kind in ('panels','reading_columns'):
                panels=block['panels'];columns=min(block['columns'],len(panels))
                panel_width = available/columns-3
                if any(row['kind']=='chart' and plot(row['spec'], panel_width).height>chart_limit
                       for panel in panels for row in panel):
                    for panel in panels:
                        populate(container, panel, available)
                    continue
                if container is doc:table=container.add_table(rows=1,cols=columns)
                else:table=container.add_table(rows=1,cols=columns)
                table.autofit=False
                for cell,panel in zip(table.rows[0].cells,panels):
                    cell.width=Mm(available/columns);populate(cell,panel,panel_width)
                container.add_paragraph()
            elif kind=='table':
                rows=block['rows'];cols=max(map(len,rows))
                table=container.add_table(rows=0,cols=cols)
                table.style='Table Grid'
                for i,row in enumerate(rows):
                    cells=table.add_row().cells
                    for j,value in enumerate(row):cells[j].text=_office_text(str(value))
                    for anchor in block.get('row_anchors', {}).get(i, ()):
                        add_bookmark(cells[0].paragraphs[0], anchor)
                    if i==0:
                        for cell in cells:
                            shading=OxmlElement('w:shd');shading.set(qn('w:fill'),'F3F3F3');cell._tc.get_or_add_tcPr().append(shading)
                            for run in cell.paragraphs[0].runs:run.bold=True
                        repeat=OxmlElement('w:tblHeader');table.rows[-1]._tr.get_or_add_trPr().append(repeat)
                container.add_paragraph()
    populate(doc,blocks,182);doc.save(output);return output.getvalue()


def _workbook_references(calc, row_count):
    inputs = calc.get('input_rows')
    if isinstance(inputs, list) and all(
        isinstance(item, dict) and type(item.get('index')) is int and 0 <= item['index'] < row_count
        and isinstance(item.get('label'), str) and item['label']
        for item in inputs
    ):
        return inputs
    return []


def _tabular(content,format):
    rows=[['对象','指标','期间','原值','单位','来源']]+(content['export_rows'] if 'export_rows' in content else data_rows(content))
    if format=='csv':
        stream=StringIO(newline='');writer=csv.writer(stream, quoting=csv.QUOTE_NONNUMERIC)
        for row in rows:
            writer.writerow([_csv_cell(value, observation=index==3) for index,value in enumerate(row)])
        return ('\ufeff'+stream.getvalue()).encode('utf-8')
    from openpyxl import Workbook
    from openpyxl.styles import Alignment,Font,PatternFill
    from openpyxl.utils.cell import quote_sheetname
    from openpyxl.worksheet.hyperlink import Hyperlink
    book = Workbook()
    sheet = book.active
    sheet.title = '研究数据'
    calculations = content.get('export_calculations', content.get('draft', {}).get('calculations', content.get('calculations', [])))
    methods = book.create_sheet('计算依据') if calculations else None
    originals = None
    next_fragment_row = 2
    wrapped = Alignment(wrap_text=True, vertical='top')

    def link_to(cell, destination, coordinate):
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f'{quote_sheetname(destination.title)}!{coordinate}')
        cell.style = 'Hyperlink'

    def append(tab, row_number, values):
        nonlocal originals, next_fragment_row
        cells = [_xlsx_cell(value) for value in values]
        long_texts = {column: value for column, value in enumerate(cells, 1)
                      if isinstance(value, str) and not _xlsx_text_fits(value)}
        for column in long_texts:
            cells[column - 1] = '查看原文'
        tab.append(cells)
        for column, value in long_texts.items():
            if originals is None:
                originals = book.create_sheet('长文本原文')
                originals.append(['工作表', '单元格', '片段', '原文'])
            source = tab.cell(row_number, column)
            first = next_fragment_row
            link_to(source, originals, f'A{first}')
            for number, fragment in enumerate(_xlsx_readable_fragments(value), 1):
                originals.append([tab.title, source.coordinate, number, fragment])
                originals.cell(next_fragment_row, 4).alignment = wrapped
                next_fragment_row += 1
            link_to(originals.cell(first, 2), tab, source.coordinate)

    for row_number, row in enumerate(rows, 1):
        append(sheet, row_number, row)
    if methods is not None:
        from .publication_content import reader_period_label

        has_ranges = any(calc.get('method') == 'raw_range_v1' for calc in calculations)
        references = [_workbook_references(calc, len(rows) - 1) for calc in calculations]
        has_context = any(any(calc.get(key) for key in ('code', 'period', 'condition')) for calc in calculations)
        header = ['指标', '方法', '结果', '单位']
        if has_ranges:
            header.extend(['区间下界', '区间上界'])
        if has_context:
            header.extend(['对象', '基期', '报告期或区间', '适用条件'])
        if any(references):
            header.append('原值依据')
            inputs = book.create_sheet('引用原值', index=2)
            inputs.append(['返回测算', '引用项', *rows[0], '研究数据'])
        else:
            inputs = None
        if has_context:
            condition_index = header.index('适用条件') + 1
        methods.append(header)
        for row_number, calc in enumerate(calculations, 2):
            output = calc.get('output', {})
            title = calc.get('label', calc.get('method', ''))
            values = [title, calc.get('method', ''), calc.get('value', output.get('value')),
                      calc.get('unit', output.get('unit', ''))]
            if has_ranges:
                values.extend(calc.get(key, output.get(key)) for key in ('minimum', 'maximum'))
            if has_context:
                values.extend([calc.get('code'), reader_period_label(calc.get('base_period', '')),
                               reader_period_label(calc.get('period', '')), calc.get('condition')])
            linked = references[row_number - 2]
            if inputs is not None:
                values.append('查看原值' if linked else None)
            append(methods, row_number, values)
            if has_context:
                methods.cell(row_number, condition_index).alignment = wrapped
            if linked:
                link_to(methods.cell(row_number, len(header)), inputs, f'A{inputs.max_row + 1}')
                for ref in linked:
                    original_row = ref['index'] + 2
                    input_row = inputs.max_row + 1
                    append(inputs, input_row, ['查看测算', ref['label'], *rows[original_row - 1], '打开原值'])
                    # Use fixed destinations even when a long original value
                    # is displayed in the workbook's full-text worksheet.
                    link_to(inputs.cell(input_row, 9), sheet, f'D{original_row}')
                    link_to(inputs.cell(input_row, 1), methods, f'C{row_number}')
    for tab in book:
        for row in tab:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
        tab.freeze_panes='A2';tab.auto_filter.ref=tab.dimensions
        for cell in tab[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='C8102E')
        if tab is originals:
            for column, width in zip('ABCD', (16, 12, 10, 112)):
                tab.column_dimensions[column].width = width
        else:
            for col in tab.columns:tab.column_dimensions[col[0].column_letter].width=min(46,max(14,max(len(str(cell.value or '')) for cell in col)+2))
    stream=BytesIO();book.save(stream);return stream.getvalue()


def _atomic_write(file,data):
    file=Path(file);temporary_path=None
    try:
        with tempfile.NamedTemporaryFile(dir=file.parent,prefix='.rh-export-',suffix='.tmp',delete=False) as temporary:
            temporary_path=Path(temporary.name)
            temporary.write(data);temporary.flush();os.fsync(temporary.fileno())
        os.replace(temporary_path,file)
    finally:
        if temporary_path is not None:
            try:temporary_path.unlink()
            except FileNotFoundError:pass


@contextmanager
def _export_transaction(directory,name):
    """Publish a complete bundle with one same-volume directory rename."""
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    target=directory/name
    if target.exists() or target.is_symlink():raise FileExistsError(target)
    with tempfile.TemporaryDirectory(prefix='.export-',dir=directory) as temporary:
        stage=Path(temporary)
        yield stage,target
        if target.exists() or target.is_symlink():raise FileExistsError(target)
        try:
            os.replace(stage,target)
        except OSError as error:
            if target.exists() or target.is_symlink():
                raise FileExistsError(target) from error
            raise


def _matches_export_files(target, files):
    if target.is_symlink() or not target.is_dir():
        return False
    try:
        for name, expected in files.items():
            path = target/name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                return False
    except OSError:
        return False
    return True


def _bundle_location(directory, stem, reuse, *, legacy_frozen=False):
    """Find a complete copy before using any vacant export name."""
    primary = directory/stem
    occupied = primary.exists() or primary.is_symlink()
    if occupied:
        if existing := reuse(primary):
            return primary, existing

    prefix = stem+'-'
    numbered, legacy = {}, []
    try:
        siblings = list(directory.iterdir())
    except FileNotFoundError:
        siblings = []
    for path in siblings:
        name = path.name.casefold()
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        # On a case-insensitive volume the recorded spelling can still open a
        # renamed bundle. On a case-sensitive volume it is a distinct path.
        canonical_path = directory/name
        if path.name != name and not (canonical_path.exists() or canonical_path.is_symlink()):
            continue
        if re.fullmatch(r'[1-9][0-9]*', suffix):
            numbered[int(suffix)] = canonical_path
        elif legacy_frozen and re.fullmatch(r'[0-9a-f]{12}', suffix):
            legacy.append(canonical_path)
    for path in [numbered[index] for index in sorted(numbered)] + sorted(legacy):
        if existing := reuse(path):
            return path, existing

    if not occupied:
        return primary, None
    suffix = 1
    while True:
        candidate = directory/f'{stem}-{suffix}'
        if suffix not in numbered and not (candidate.exists() or candidate.is_symlink()):
            return candidate, None
        suffix += 1


def save_frozen_html(content,html,directory,filename):
    """Keep the original bytes and JSON together; never replace an older export."""
    if not isinstance(filename,str) or Path(filename).name!=filename or not filename.endswith('.html'):
        raise ContractError('原冻结报告文件名无效')
    files={filename:html.encode('utf-8'),Path(filename).with_suffix('.json').name:canonical(content).encode('utf-8')}
    digest=hashlib.sha256(filename.encode()+b'\0'+b'\0'.join(files.values())).hexdigest()[:24]
    directory=Path(directory).resolve()
    while True:
        target, existing = _bundle_location(directory, 'frozen-'+digest,
            lambda path: _matches_export_files(path, files), legacy_frozen=True)
        if existing:
            return target/filename
        try:
            with _export_transaction(directory,target.name) as (stage,target):
                for key,value in files.items():
                    _atomic_write(stage/key,value)
        except FileExistsError:
            if target.exists() or target.is_symlink():
                continue
            raise
        return target/filename


def convert_saved_html(path,format,*,expected_sha256,resources=None):
    """Convert the exact saved HTML; never regenerate research or its content."""
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ContractError('HTML版本已变化，未转换')
    tree,document=read_presentation_document(raw)
    from .report_designer import style_document
    visual_nodes=tree.xpath('//script[@id="rh-visual-profile"]')
    if len(visual_nodes)>1:raise ContractError('HTML图表设置不唯一')
    selected=json.loads(visual_nodes[0].text) if visual_nodes else document.get('visual_profile')
    # Saved pages retain the chart choices embedded before the current catalogue rules.
    document=style_document(document,selected,
                            preserve_archived_choices='visual_catalogue_version' not in document)
    if format=='html':return raw,document
    if format in ('pdf','docx'):
        from .report_designer import document_reference_blocks
        document=dict(document,blocks=document_reference_blocks(document['blocks'],tree.find('.//main')))
    if format=='pdf':
        if sys.platform=='darwin':
            from .html_pdf import render_saved_html
            from .publication_graphics import EvidenceChart, dense_time_bars
            specs = [block['spec'] for block in document['blocks'] if block['kind']=='chart']
            width, height = _pdf_body_size()
            plots = (EvidenceChart(spec, width=width, height=128) for spec in specs)
            # WebKit prints each embedded SVG as one figure. Use the
            # paginated renderer for a categorical plot that needs sections.
            requires_pages = any(plot.categories is not None and plot.height > height-60 for plot in plots)
            if not requires_pages:
                document['conversion_engine'] = 'webkit'
                return render_saved_html(path,expected_sha256=expected_sha256,resources=resources,
                                         refresh_chart_layout=any(map(dense_time_bars,specs))),document
        document['conversion_engine'] = 'reportlab'
        return _pdf(document),document
    if format=='docx':return _docx(document),document
    if format in ('xlsx','csv'):
        data=document['dataset'];return _tabular(dict(export_rows=data['rows'],export_calculations=data['calculations']),format),document
    raise ContractError('不支持的导出格式')



def _report_filename(title,version,profile,format):
    title=re.sub(r'[\x00-\x1f/\\:*?"<>|]','',title)[:80].strip(' .') or '研究报告'
    suffix=f'_v{version}_{PROFILES[profile]}.{format}'
    # A character cap alone can exceed a filesystem's UTF-8 byte limit.
    # Leave room for the JSON sidecar and native save-panel name suffixes.
    title=title.encode('utf-8')[:240-len(suffix.encode('utf-8'))].decode('utf-8',errors='ignore').rstrip(' .') or '研究报告'
    return title+suffix


def _report_receipt(document, file, source, format, source_hash, data):
    receipt = dict(
        schema='researchhelper.export-result', version=1, status='saved', output_type='report',
        template_id=document['template']['id'], profile=document['profile'], audience=document['audience'],
        format=format, path=str(file), filename=file.name, mime=FORMATS[format],
        sha256=hashlib.sha256(data).hexdigest(), source_html=str(source), source_html_sha256=source_hash,
        converted_from_saved_html=True, content_hash=document['content_hash'], bytes=len(data),
        visual_profile=document['visual_profile'], model_calls=0, data_requests=0,
    )
    if format == 'pdf':
        receipt['conversion_engine'] = document['conversion_engine']
    if format != 'html':
        revision = {'docx': _WORD_CONVERSION_REVISION, 'xlsx': _WORKBOOK_CONVERSION_REVISION}.get(
            format, _CONVERSION_REVISION)
        receipt.update(conversion_revision=revision, conversion_platform=sys.platform)
    return receipt


def _receipt_bytes(receipt):
    return json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _check_export_signature(format, data):
    if format in _FORMAT_SIGNATURES:
        prefix, error = _FORMAT_SIGNATURES[format]
        if not data.startswith(prefix):
            raise ContractError(error)


def _reusable_export(file, source, format, document, source_hash, inputs):
    """Read only a complete bundle produced for the same saved HTML."""
    target = file.parent
    receipt_file = target/'receipt.json'
    if not _matches_export_files(target, inputs):
        return None
    if any(path.is_symlink() or not path.is_file() for path in (file, receipt_file)):
        return None
    try:
        stored = receipt_file.read_bytes()
        data = inputs[source.name] if format == 'html' else file.read_bytes()
        _check_export_signature(format, data)
        if format == 'pdf':
            previous = json.loads(stored)
            engine = previous.get('conversion_engine') if isinstance(previous, dict) else None
            if engine != 'reportlab' and not (engine == 'webkit' and sys.platform == 'darwin'):
                return None
            document = dict(document, conversion_engine=engine)
        expected = _report_receipt(document, file, source, format, source_hash, data)
    except (OSError, ValueError):
        return None
    return expected if stored == _receipt_bytes(expected) else None


def export_report(content,resources,directory,*,profile='standard',format='html',theme=None,visual_profile=None):
    if not isinstance(format,str) or format not in FORMATS:
        raise ContractError('不支持的导出格式')
    before = canonical(content)
    publication = design(content,resources,profile=profile,theme=theme,visual_profile=visual_profile)
    if canonical(content) != before:
        raise ContractError('导出改变了冻结研究内容')
    content_bytes = before.encode('utf-8')
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    html_bytes = publication['html'].encode('utf-8')
    source_hash = hashlib.sha256(html_bytes).hexdigest()
    document = publication['document']
    if document.get('content_hash') != content_hash or document.get('profile') != profile:
        raise ContractError('导出正文与选中的报告版本或详略不一致')

    filename = _report_filename(document['title'],content['research_version'],profile,format)
    sidecar = Path(filename).with_suffix('.json').name
    html_export = format == 'html'
    source_name = filename if html_export else 'report.html'
    inputs = {source_name: html_bytes, sidecar: content_bytes}
    if html_export:
        stem = 'export-' + source_hash[:24]
    else:
        revision = _WORD_CONVERSION_REVISION if format == 'docx' else _CONVERSION_REVISION
        key = canonical([format, revision, sys.platform, source_hash])
        stem = 'export-' + format + '-' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]
    directory = Path(directory).resolve()

    def reuse(target):
        return _reusable_export(target/filename,target/source_name,format,document,source_hash,inputs)

    while True:
        target, receipt = _bundle_location(directory, stem, reuse)
        if receipt is not None:
            return receipt
        try:
            with _export_transaction(directory,target.name) as (stage,target):
                source = stage/source_name
                _atomic_write(source,html_bytes)
                data, verified = convert_saved_html(source,format,expected_sha256=source_hash,resources=resources)
                if canonical(content) != before:
                    raise ContractError('导出改变了冻结研究内容')
                if (verified.get('content_hash') != content_hash or verified.get('profile') != profile
                        or verified.get('title') != document['title']):
                    raise ContractError('导出正文与选中的报告版本或详略不一致')
                _check_export_signature(format, data)
                file = stage/filename
                if not html_export:
                    _atomic_write(file,data)
                if file.read_bytes() != data:
                    raise ContractError('导出文件读回校验失败')
                receipt = _report_receipt(verified,target/filename,target/source_name,format,source_hash,data)
                _atomic_write(stage/sidecar,content_bytes)
                _atomic_write(stage/'receipt.json',_receipt_bytes(receipt))
        except FileExistsError:
            if target.exists() or target.is_symlink():
                # A concurrent writer can publish the same bundle while this one stages.
                continue
            raise
        return receipt

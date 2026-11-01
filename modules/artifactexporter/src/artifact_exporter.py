"""ArtifactExporter: real, atomic offline files from one presentation document."""
from contextlib import contextmanager
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
import uuid
from .contracts import ContractError,canonical
from .report_designer import design,PROFILES,DEFAULT_TEMPLATE_ID

FORMATS={'html':'text/html','pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','csv':'text/csv'}


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
        else:
            yield block;index+=1


def _pdf(document):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,Table
    from .publication_graphics import FONT,EvidenceChart
    from .publication_math import MathFormula
    output=BytesIO();styles={
        'title':ParagraphStyle('rh-title',fontName=FONT,fontSize=13.5,leading=18,spaceAfter=7,textColor=colors.HexColor('#252525')),
        'heading':ParagraphStyle('rh-heading',fontName=FONT,fontSize=10.8,leading=14,spaceBefore=6,spaceAfter=4,keepWithNext=True),
        'paragraph':ParagraphStyle('rh-body',fontName=FONT,fontSize=9,leading=12,spaceAfter=4,wordWrap='CJK'),
        'caption':ParagraphStyle('rh-caption',fontName=FONT,fontSize=9,leading=12,spaceBefore=4,spaceAfter=3,keepWithNext=True),
        'cell':ParagraphStyle('rh-cell',fontName=FONT,fontSize=8.25,leading=10.5,wordWrap='CJK')}
    width=A4[0]-56
    def flow(blocks,available):
        result=[]
        for block in _layout_segments(blocks):
            kind=block['kind']
            if kind in styles:result.append(Paragraph(html.escape(block['text']),styles[kind]))
            elif kind=='formula':result.append(MathFormula(block['mathml']))
            elif kind=='chart':result.append(EvidenceChart(block['spec'],width=available,height=128 if available>300 else 96,palette=document['theme']))
            elif kind in ('panels','reading_columns'):
                panels=block['panels'];columns=min(block['columns'],len(panels));cell_width=available/columns
                cells=[flow(panel,cell_width-10) for panel in panels]
                table=Table([cells],colWidths=[cell_width]*columns,hAlign='LEFT')
                table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE' if kind=='reading_columns' else 'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),5)]));result.append(table)
            elif kind=='table':
                rows=block['rows'];columns=max(map(len,rows));batches=[list(range(columns))] if columns<=7 else [[0]+list(range(start,min(start+6,columns))) for start in range(1,columns,6)]
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


def _docx(document):
    from docx import Document
    from docx.shared import Mm,Pt,RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from .publication_graphics import chart_png
    output=BytesIO();doc=Document();section=doc.sections[0];section.page_width=Mm(210);section.page_height=Mm(297)
    section.left_margin=section.right_margin=Mm(12);section.top_margin=section.bottom_margin=Mm(12)
    for name in ('Normal','Title','Heading 1','Heading 2','Heading 3'):
        style=doc.styles[name];style.font.name='Arial';style.font.color.rgb=RGBColor.from_string('252525');style.font.size=Pt(9 if name=='Normal' else 13.5 if name=='Title' else 10.8)
        rpr=style.element.get_or_add_rPr();rfonts=rpr.find(qn('w:rFonts'))
        if rfonts is None:rfonts=OxmlElement('w:rFonts');rpr.append(rfonts)
        rfonts.set(qn('w:eastAsia'),'宋体');style.paragraph_format.space_after=Pt(4);style.paragraph_format.line_spacing=1.1
        for border in style.element.xpath('.//w:pBdr'):border.getparent().remove(border)
    def populate(container,blocks,available):
        for block in _layout_segments(blocks):
            kind=block['kind']
            if kind=='title':container.add_paragraph(block['text'],'Title')
            elif kind=='heading':container.add_paragraph(block['text'],'Heading '+str(min(3,max(1,block['level']-1))))
            elif kind in ('paragraph','caption'):container.add_paragraph(block['text'])
            elif kind=='formula':container.add_paragraph()._p.append(_omml(block['mathml']))
            elif kind=='chart':
                paragraph=container.add_paragraph();paragraph.add_run().add_picture(BytesIO(chart_png(block['spec'],width=available*2.835,height=128 if available>120 else 115,palette=document['theme'])),width=Mm(available))
            elif kind in ('panels','reading_columns'):
                columns=min(block['columns'],len(block['panels']))
                if container is doc:table=container.add_table(rows=1,cols=columns)
                else:table=container.add_table(rows=1,cols=columns)
                table.autofit=False
                for cell,panel in zip(table.rows[0].cells,block['panels']):
                    cell.width=Mm(available/columns);populate(cell,panel,available/columns-3)
                container.add_paragraph()
            elif kind=='table':
                rows=block['rows'];cols=max(map(len,rows))
                table=container.add_table(rows=0,cols=cols)
                table.style='Table Grid'
                for i,row in enumerate(rows):
                    cells=table.add_row().cells
                    for j,value in enumerate(row):cells[j].text=str(value)
                    if i==0:
                        for cell in cells:
                            shading=OxmlElement('w:shd');shading.set(qn('w:fill'),'F3F3F3');cell._tc.get_or_add_tcPr().append(shading)
                            for run in cell.paragraphs[0].runs:run.bold=True
                        repeat=OxmlElement('w:tblHeader');table.rows[-1]._tr.get_or_add_trPr().append(repeat)
                container.add_paragraph()
    populate(doc,document['blocks'],182);doc.save(output);return output.getvalue()


def _tabular(content,format):
    rows=[['对象','指标','期间','原值','单位','来源']]+(content['export_rows'] if 'export_rows' in content else data_rows(content))
    if format=='csv':
        stream=StringIO(newline='');writer=csv.writer(stream)
        for row in rows:writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in row])
        return ('\ufeff'+stream.getvalue()).encode('utf-8')
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    book=Workbook();sheet=book.active;sheet.title='研究数据'
    for row in rows:
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value,str):cell.data_type='s'
    calculations=content.get('export_calculations',content.get('draft',{}).get('calculations',content.get('calculations',[])))
    if calculations:
        methods=book.create_sheet('计算依据');methods.append(['指标','方法','结果','单位'])
        for calc in calculations:
            output=calc.get('output',{})
            methods.append([calc.get('label',calc.get('method','')),calc.get('method',''),calc.get('value',output.get('value')),calc.get('unit',output.get('unit',''))])
    for tab in book:
        for row in tab:
            for cell in row:
                if isinstance(cell.value,str):cell.data_type='s'
        tab.freeze_panes='A2';tab.auto_filter.ref=tab.dimensions
        for cell in tab[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='C8102E')
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
        os.replace(stage,target)


def save_frozen_html(content,html,directory,filename):
    """Keep the original bytes and JSON together; never replace an older export."""
    if not isinstance(filename,str) or Path(filename).name!=filename or not filename.endswith('.html'):
        raise ContractError('原冻结报告文件名无效')
    files={filename:html.encode('utf-8'),Path(filename).with_suffix('.json').name:canonical(content).encode('utf-8')}
    digest=hashlib.sha256(filename.encode()+b'\0'+b'\0'.join(files.values())).hexdigest()[:24]
    directory=Path(directory).resolve();name='frozen-'+digest
    def matches(target):
        if target.is_symlink() or not target.is_dir():return False
        try:
            return all(not (target/key).is_symlink() and (target/key).is_file() and (target/key).read_bytes()==value for key,value in files.items())
        except OSError:return False
    target=directory/name
    if matches(target):return target/filename
    # A changed or incomplete old export remains available for recovery.
    if target.exists() or target.is_symlink():name+='-'+uuid.uuid4().hex[:12]
    target=directory/name
    try:
        with _export_transaction(directory,name) as (stage,target):
            for key,value in files.items():_atomic_write(stage/key,value)
    except OSError:
        # Another host can finish the same immutable pair while we prepare it.
        if not matches(target):raise
    return target/filename


def convert_saved_html(path,format,*,expected_sha256,resources=None):
    """Convert the exact saved HTML; never regenerate research or its content."""
    from lxml import html as dom
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ContractError('HTML版本已变化，未转换')
    tree=dom.document_fromstring(raw);nodes=tree.xpath('//script[@id="rh-publication-document"]')
    if len(nodes)!=1:raise ContractError('HTML缺少同版转换资料')
    document=json.loads(nodes[0].text)
    if document.get('schema')!='researchhelper.presentation-document':raise ContractError('HTML转换资料无效')
    from .report_designer import style_document
    visual_nodes=tree.xpath('//script[@id="rh-visual-profile"]')
    if len(visual_nodes)>1:raise ContractError('HTML图表设置不唯一')
    selected=json.loads(visual_nodes[0].text) if visual_nodes else document.get('visual_profile')
    document=style_document(document,selected)
    if format=='html':return raw,document
    if format=='pdf':
        if sys.platform=='darwin':
            from .html_pdf import render_saved_html
            return render_saved_html(path,expected_sha256=expected_sha256,resources=resources),document
        return _pdf(document),document
    if format=='docx':return _docx(document),document
    if format in ('xlsx','csv'):
        data=document['dataset'];return _tabular(dict(export_rows=data['rows'],export_calculations=data['calculations']),format),document
    raise ContractError('不支持的导出格式')



def _report_filename(title,profile,format):
    title=re.sub(r'[\x00-\x1f/\\:*?"<>|]','',title)[:80].strip(' .') or '研究报告'
    suffix='_'+PROFILES[profile]+'.'+format
    # A character cap alone can exceed a filesystem's UTF-8 byte limit.
    # Leave room for the JSON sidecar and native save-panel name suffixes.
    title=title.encode('utf-8')[:240-len(suffix.encode('utf-8'))].decode('utf-8',errors='ignore').rstrip(' .') or '研究报告'
    return title+suffix


def export_report(content,resources,directory,*,profile='standard',format='html',theme='research',visual_profile=None):
    if not isinstance(format,str) or format not in FORMATS:raise ContractError('不支持的导出格式')
    before=canonical(content);publication=design(content,resources,profile=profile,theme=theme,visual_profile=visual_profile)
    with _export_transaction(directory,'export-'+uuid.uuid4().hex[:12]) as (stage,target):
        source=stage/'report.html';html_bytes=publication['html'].encode()
        # Conversion reads the saved HTML before the complete bundle is published.
        _atomic_write(source,html_bytes);source_hash=hashlib.sha256(html_bytes).hexdigest()
        data,document=convert_saved_html(source,format,expected_sha256=source_hash,resources=resources)
        if canonical(content)!=before:raise ContractError('导出改变了冻结研究内容')
        if format=='pdf' and not data.startswith(b'%PDF-'):raise ContractError('PDF文件无效')
        if format in ('docx','xlsx') and not data.startswith(b'PK'):raise ContractError('Office文件无效')
        filename=_report_filename(document['title'],profile,format);file=stage/filename;_atomic_write(file,data)
        receipt=dict(schema='researchhelper.export-result',version=1,status='saved',output_type='report',template_id=document['template']['id'],profile=profile,audience=document['audience'],format=format,
            path=str(target/filename),filename=filename,mime=FORMATS[format],sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            source_html=str(target/'report.html'),source_html_sha256=source_hash,converted_from_saved_html=True,content_hash=document['content_hash'],
            bytes=len(data),visual_profile=document['visual_profile'],model_calls=0,data_requests=0)
        if format=='pdf':receipt['conversion_engine']='webkit' if sys.platform=='darwin' else 'reportlab'
        _atomic_write(file.with_suffix('.json'),before.encode('utf-8'))
        _atomic_write(stage/'receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2).encode('utf-8'))
    return receipt

"""Offline vector charts from the shared frozen chart plan, without inference."""
from io import BytesIO
from math import isfinite,log10,floor,hypot
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Flowable
from .visual_catalog import theme

FONT='STSong-Light'
if FONT not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def number(value):
    if value is None:return '未返回'
    if not isinstance(value,(int,float)):return str(value)
    return format(value,',.2f').rstrip('0').rstrip('.') if abs(value)>=.01 or not value else format(value,'.3g')


def mechanism_layout(data, width, height):
    """Static-format geometry keeps the same complete labels and directed links."""
    nodes=data['nodes'];count=len(nodes);columns=count if count<=3 else (count+1)//2
    rows=(count+columns-1)//columns
    box=min(112,max(42,(width-24-14*(columns-1))/columns))
    def wrap(value):
        lines=[];line=''
        for character in value:
            if line and pdfmetrics.stringWidth(line+character,FONT,8.25)>box-12:
                lines.append(line);line=''
            line+=character
        return lines+[line] if line else lines
    labels=[wrap(node['name']) for node in nodes]
    node_height=max(40,max(len(lines) for lines in labels)*10.5+12)
    height=max(height,rows*node_height+(rows-1)*14+16)
    inset_x=box/2+8;inset_y=node_height/2+8
    positions=[]
    for index in range(count):
        row,column=divmod(index,columns)
        if row%2:column=columns-1-column
        positions.append((width/2 if columns==1 else inset_x+column*(width-2*inset_x)/(columns-1),
                          height/2 if rows==1 else height-inset_y-row*(height-2*inset_y)/(rows-1)))
    return dict(height=height,box=box,node_height=node_height,labels=labels,positions=positions)


def category_layout(labels, width, height, legend=None):
    """Measure complete row names and grow static figures when wrapping is needed."""
    size=8.25;leading=10
    def wrap(value, available):
        lines=[];line=''
        for character in str(value):
            if character=='\n':
                lines.append(line);line='';continue
            if line and pdfmetrics.stringWidth(line+character,FONT,size)>available:
                lines.append(line);line=''
            line+=character
        return lines+[line]
    measured=max((pdfmetrics.stringWidth(str(label),FONT,size) for label in labels),default=0)
    limit=min(width*.46,width-24-64)
    left=max(min(72 if width>300 else 59,limit),min(measured+12,limit))
    wrapped=[wrap(label,max(1,left-12)) for label in labels]
    legends=[wrap(name,max(1,(width-24)/2-10)) for name in (legend or [])]
    top_padding=32+leading*max(map(len,legends)) if legends else 27
    row_height=max(30,leading*max(map(len,wrapped),default=1)+8)
    height=max(height,top_padding+27+len(labels)*row_height)
    return dict(left=left,top=height-top_padding,height=height,labels=wrapped,
                legends=legends,leading=leading)


class EvidenceChart(Flowable):
    def __init__(self,spec,width=510,height=154,palette='research'):
        Flowable.__init__(self);self.spec=spec;self.width=width;self.height=height
        auto=spec.get('auto_visual',{});data=auto.get('data',spec.get('data',{}))
        self.mechanism=None;self.categories=None
        kind=auto.get('scheme',spec.get('scheme'))
        if kind in ('dumbbell','lollipop','ranked_bar','diverging_bar','benchmark_dot','grouped_bar','stacked_bar','return_bars'):
            legends=data.get('periods') if kind=='dumbbell' else None
            self.categories=category_layout(data['labels'],width,height,legends)
            self.height=self.categories['height']
        if kind=='evidence' and data.get('qualitative'):
            self.mechanism=mechanism_layout(data,width,height)
            self.height=self.mechanism['height']
        self.palette=theme(palette) or theme('research')
        if self.palette.get('dark'):self.palette=theme('research')

    def draw(self):
        c=self.canv;w,h=self.width,self.height;p=self.palette
        auto=self.spec.get('auto_visual',{});kind=auto.get('scheme',self.spec.get('scheme'))
        d=auto.get('data',self.spec.get('data',{}));colors=p['colors']
        def text(x,y,value,size=8,align='left',color=None):
            size=max(8.25,size);c.setFillColor(color or p['ink']);c.setFont(FONT,size)
            getattr(c,{'left':'drawString','right':'drawRightString','center':'drawCentredString'}[align])(x,y,str(value))
        def line(x1,y1,x2,y2,color=None,width=.6):
            c.setStrokeColor(color or p['grid']);c.setLineWidth(width);c.line(x1,y1,x2,y2)
        def dot(x,y,color,size=2.5):
            c.setFillColor(color);c.circle(x,y,size,stroke=0,fill=1)
        def finite(value):return type(value) in (int,float) and isfinite(value)
        def bounds(values,zero=False):
            vals=[value for value in values if finite(value)];lo=min(vals or [0]);hi=max(vals or [1])
            if zero:lo=min(0,lo);hi=max(0,hi)
            pad=(hi-lo or abs(hi) or 1)*.12
            return lo-pad if lo<0 or not zero else 0,hi+pad
        unit=d.get('unit','');left=72 if w>300 else 59;right=w-24;bottom=27;top=h-27
        if self.categories:left=self.categories['left'];top=self.categories['top']
        def category(index,y):
            lines=self.categories['labels'][index];leading=self.categories['leading']
            for row,value in enumerate(lines):text(left-6,y+(len(lines)-1)*leading/2-row*leading-3,value,align='right')
        def legends():
            for index,lines in enumerate(self.categories['legends']):
                for row,value in enumerate(lines):text(12 if index==0 else w-12,h-12-row*self.categories['leading'],value,color=series_color(index),align='left' if index==0 else 'right')
        observed=[]
        for row in d.get('series',[]):observed.extend(row['values'])
        for row in d.get('pairs',[]):observed.extend(row)
        for row in d.get('points',[]):observed.extend([row['x'],row['y']])
        observed.extend(v for v in d.get('values',[]) if finite(v))
        magnitude=max([abs(v) for v in observed if finite(v)] or [0])
        exponent=3*floor(log10(magnitude)/3) if unit and unit not in ('%','倍','个百分点') and magnitude>=1e6 else 0
        divisor=10**exponent
        digits=d.get('numberDigits',2)
        def shown(value):
            if not finite(value):return number(value)
            scaled=value/divisor
            if scaled and abs(scaled)<10**(-digits):return number(scaled)
            return format(scaled,',.'+str(digits)+'f').rstrip('0').rstrip('.') if digits else format(scaled,',.0f')
        label_unit='' if exponent else unit
        if exponent:
            text(right-10,top+11,unit+'×10',align='right',color=p['muted']);text(right-9,top+15,str(exponent),color=p['muted'])
        elif unit:text(right,top+11,unit,align='right',color=p['muted'])
        periods=d.get('comparison_periods') or d.get('periods')
        def series_color(index):
            return (p['accent'],p['primary'])[index%2] if periods and len(periods)==2 else colors[index%len(colors)]
        if kind in ('line','multi_line','area','indexed_line','drawdown','rolling','excess_return'):
            series=d.get('series') or [dict(name=d.get('name','观测值'),values=d.get('values',[]))]
            labels=d['labels'];lo,hi=bounds([v for row in series for v in row['values']]);x=lambda i:left+(right-left)*i/max(1,len(labels)-1);y=lambda v:bottom+(v-lo)/(hi-lo)*(top-bottom)
            for j in range(5):
                v=lo+(hi-lo)*j/4;line(left,y(v),right,y(v));text(left-6,y(v)-3,shown(v),align='right')
            for j,row in enumerate(series):
                color=colors[j%len(colors)];previous=None
                for i,value in enumerate(row['values']):
                    if not finite(value):previous=None;continue
                    point=(x(i),y(value))
                    if previous:line(*previous,*point,color,width=1.3)
                    dot(*point,color,2);previous=point
                text(left+j*(right-left)/max(1,len(series)),h-10,row.get('name','')[:26],size=7.5,color=color)
            stride=max(1,len(labels)//4)
            for i,label in enumerate(labels):
                if i%stride==0 or i==len(labels)-1:text(x(i),10,str(label)[5:] if d.get('compactDates') else str(label),size=7,align='center')
        elif kind=='scatter':
            points=d['points'];xlo,xhi=bounds([r['x'] for r in points]);ylo,yhi=bounds([r['y'] for r in points])
            x=lambda value:left+(value-xlo)/(xhi-xlo)*(right-left);y=lambda value:bottom+(value-ylo)/(yhi-ylo)*(top-bottom)
            for j in range(4):
                vx=xlo+(xhi-xlo)*j/3;vy=ylo+(yhi-ylo)*j/3
                line(x(vx),bottom,x(vx),top);line(left,y(vy),right,y(vy))
                text(x(vx),10,shown(vx),align='center');text(left-5,y(vy)-3,shown(vy),align='right')
            for i,row in enumerate(points):
                dot(x(row['x']),y(row['y']),series_color(i),3)
                text(x(row['x']),y(row['y'])+(9 if i%2 else -13),row.get('name',''),size=8.25,align='center')
        elif kind=='heatmap':
            matrix=d['matrix'];rows=d['rows'];labels=d['labels'];values=[v for row in matrix for v in row if finite(v)]
            lo,hi=bounds(values);cellw=(right-left)/max(1,len(labels));cellh=(top-bottom)/max(1,len(rows))
            for r,row in enumerate(matrix):
                text(left-7,top-(r+.5)*cellh-3,rows[r][:22],size=7.5,align='right')
                for i,v in enumerate(row):
                    if not finite(v):continue
                    ratio=(v-lo)/(hi-lo);base=colors[0] if v>=0 else p['accent']
                    from reportlab.lib.colors import HexColor,Color
                    rgb=HexColor(base);color=Color(1-(1-rgb.red)*(.18+.82*ratio),1-(1-rgb.green)*(.18+.82*ratio),1-(1-rgb.blue)*(.18+.82*ratio))
                    c.setFillColor(color);c.rect(left+i*cellw,top-(r+1)*cellh,cellw-1,cellh-1,stroke=0,fill=1)
                    text(left+(i+.5)*cellw,top-(r+.5)*cellh-3,shown(v),size=7.5,align='center',color='#FFFFFF' if ratio>.55 else '#252525')
            for i,label in enumerate(labels):text(left+(i+.5)*cellw,10,str(label)[2:10],size=6.8,align='center')
        elif kind=='dumbbell':
            pairs=d['pairs'];labels=d['labels'];lo,hi=bounds([v for pair in pairs for v in pair]);x=lambda v:left+(v-lo)/(hi-lo)*(right-left)
            for j in range(5):
                v=lo+(hi-lo)*j/4;line(x(v),bottom,x(v),top);text(x(v),10,shown(v),align='center')
            for i,(label,pair) in enumerate(zip(labels,pairs)):
                y=top-(i+.5)*(top-bottom)/len(pairs);category(i,y)
                line(x(pair[0]),y,x(pair[1]),y,p['muted'],1.5)
                for k,v in enumerate(pair):dot(x(v),y,series_color(k));text(x(v),y+(8 if k else -13),shown(v)+label_unit,size=8,align='center')
            legends()
        elif kind in ('lollipop','ranked_bar','diverging_bar','benchmark_dot','grouped_bar','stacked_bar','return_bars'):
            labels=d['labels'];series=d.get('series') or [dict(name=d.get('name',''),values=d['values'])]
            if len(series)>1:
                for j,row in enumerate(series):text(left+j*(right-left)/len(series),h-10,row.get('name',''),color=series_color(j))
            lo,hi=bounds([v for row in series for v in row['values']],True);x=lambda v:left+(v-lo)/(hi-lo)*(right-left)
            for j in range(5):
                v=lo+(hi-lo)*j/4;line(x(v),bottom,x(v),top);text(x(v),10,shown(v),align='center')
            for i,label in enumerate(labels):
                y=top-(i+.5)*(top-bottom)/len(labels);category(i,y)
                for j,row in enumerate(series):
                    v=row['values'][i]
                    if not finite(v):continue
                    yy=y+(j-(len(series)-1)/2)*8;color=(p['positive'] if v>=0 else p['negative']) if kind in ('diverging_bar','return_bars') else series_color(j)
                    if kind=='lollipop' and isinstance(d.get('focusIndex'),int):color=p['primary'] if i==d['focusIndex'] else p['muted']
                    if kind in ('lollipop','benchmark_dot'):line(x(0),yy,x(v),yy,color,1);dot(x(v),yy,color)
                    else:c.setFillColor(color);c.rect(min(x(0),x(v)),yy-2,abs(x(v)-x(0)),4,stroke=0,fill=1)
                    if kind in ('lollipop','benchmark_dot'):
                        # Above the point, never into the category column for
                        # negative values. Keep the whole label on the canvas.
                        label=shown(v)+label_unit
                        half=pdfmetrics.stringWidth(label,FONT,8.25)/2
                        label_x=max(left+half,min(w-half-3,x(v)))
                        text(label_x,yy+8,label,align='center',size=8.25,color=color)
                    else:text(x(v)+(5 if v>=0 else -5),yy-3,shown(v)+label_unit,align='left' if v>=0 else 'right',size=7.5,color=color)
        elif kind=='candlestick':
            rows=d['values'];labels=d['labels'];lo,hi=bounds([v for row in rows for v in row]);y=lambda v:bottom+(v-lo)/(hi-lo)*(top-bottom);step=(right-left)/len(rows)
            for j in range(5):
                v=lo+(hi-lo)*j/4;line(left,y(v),right,y(v));text(left-6,y(v)-3,shown(v),align='right')
            for i,(op,cl,low,high) in enumerate(rows):
                x=left+(i+.5)*step;color=p['positive'] if cl>=op else p['negative'];line(x,y(low),x,y(high),color,1)
                c.setFillColor(color);c.rect(x-min(5,step*.3),min(y(op),y(cl)),min(10,step*.6),max(1,abs(y(op)-y(cl))),stroke=0,fill=1)
                if i%max(1,len(rows)//4)==0:text(x,10,str(labels[i]),size=7,align='center')
        elif kind=='evidence' and d.get('qualitative'):
            layout=self.mechanism;box=layout['box'];node_height=layout['node_height']
            positions=dict(zip((node['id'] for node in d['nodes']),layout['positions']))
            c.setDash(3,2)
            for edge in d['links']:
                a,b=positions[edge['source']],positions[edge['target']]
                dx,dy=b[0]-a[0],b[1]-a[1];length=hypot(dx,dy)
                if not length:continue
                ux,uy=dx/length,dy/length
                boundary=min(box/2/abs(ux) if ux else float('inf'),node_height/2/abs(uy) if uy else float('inf'))+2
                x1,y1=a[0]+ux*boundary,a[1]+uy*boundary
                x2,y2=b[0]-ux*boundary,b[1]-uy*boundary
                bx,by=x2-ux*5,y2-uy*5
                line(x1,y1,bx,by,p['muted'],.8)
                arrow=c.beginPath();arrow.moveTo(x2,y2);arrow.lineTo(bx-uy*2,by+ux*2);arrow.lineTo(bx+uy*2,by-ux*2);arrow.close()
                c.setFillColor(p['muted']);c.drawPath(arrow,stroke=0,fill=1)
            c.setDash()
            for index,node in enumerate(d['nodes']):
                cx,cy=layout['positions'][index];lines=layout['labels'][index]
                c.setFillColor(p['paper']);c.setStrokeColor(p['primary'] if node.get('kind')=='condition' else p['accent']);c.setLineWidth(.8)
                c.roundRect(cx-box/2,cy-node_height/2,box,node_height,4,stroke=1,fill=1)
                for row,label in enumerate(lines):text(cx,cy+(len(lines)-1)*5.25-row*10.5-3,label,align='center',size=8.25)
        elif kind=='range':
            rows=d['ranges'];lo,hi=bounds([r[k] for r in rows for k in ('min','max')]);x=lambda v:left+(v-lo)/(hi-lo)*(right-left)
            for i,row in enumerate(rows):
                y=top-(i+.5)*(top-bottom)/len(rows);text(left-6,y-3,row['name'],align='right');line(x(row['min']),y,x(row['max']),y,colors[0],3)
                dot(x(row['min']),y,colors[0]);dot(x(row['max']),y,colors[0]);text((x(row['min'])+x(row['max']))/2,y+10,number(row['min'])+'至'+number(row['max'])+unit,align='center')
        else:
            # An unsupported static grammar keeps its actual evidence table.
            text(left,top-15,'原值见下表',size=9)


def chart_pdf(spec,width=510,height=154,palette='research'):
    stream=BytesIO();item=EvidenceChart(spec,width,height,palette);canvas=Canvas(stream,pagesize=(width,item.height));item.drawOn(canvas,0,0);canvas.showPage();canvas.save();return stream.getvalue()


def chart_png(spec,width=510,height=154,palette='research'):
    import pypdfium2
    document=pypdfium2.PdfDocument(chart_pdf(spec,width,height,palette))
    try:
        bitmap=document[0].render(scale=2);image=bitmap.to_pil();stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()
    finally:document.close()

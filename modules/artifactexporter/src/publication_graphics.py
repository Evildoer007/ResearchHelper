"""Offline vector charts from the shared frozen chart plan, without inference."""
from collections import Counter
from copy import copy
from datetime import date
from io import BytesIO
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from math import ceil,floor,hypot,isfinite,log10
from reportlab.lib.colors import Color, HexColor
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Flowable
from .visual_catalog import theme

FONT='STSong-Light'
LABEL_SIZE = 8.25
LINE_CHARTS = ('line', 'multi_line', 'area', 'indexed_line', 'drawdown', 'rolling', 'excess_return')
BAR_CHARTS = ('lollipop', 'ranked_bar', 'diverging_bar', 'benchmark_dot', 'grouped_bar', 'stacked_bar', 'return_bars')
TIME_BAR_CHARTS = ('lollipop', 'ranked_bar', 'diverging_bar', 'grouped_bar', 'return_bars')
if FONT not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def reading_number(value, digits=2):
    """Match the browser's reading precision, half-up rounding and nonzero rule."""
    if value is None:return '未返回'
    if type(value) not in (int,float) or type(value) is float and not isfinite(value):return str(value)
    if value == 0:return '0'
    if type(digits) is float and isfinite(digits) and digits.is_integer():digits=int(digits)
    digits=max(0,min(12,digits)) if type(digits) is int else 2
    original=Decimal(str(value))
    with localcontext() as context:
        context.prec=max(50,original.adjusted()+digits+4)
        rounded=original.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
        if not rounded:
            if abs(original)>=Decimal('0.000001'):
                digits=min(12,max(digits,2-original.adjusted()))
                rounded=original.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
            else:
                rounded=original.quantize(Decimal(1).scaleb(original.adjusted()-2),rounding=ROUND_HALF_UP)
                exponent=rounded.adjusted()
                mantissa=format(rounded.scaleb(-exponent),'f').rstrip('0').rstrip('.')
                return mantissa+'e'+str(exponent)
        shown=format(rounded,',f')
        return shown.rstrip('0').rstrip('.') if digits else shown


def _isolated_marker(canvas, values, index, point, color):
    if (index == 0 or values[index-1] is None) and (index+1 == len(values) or values[index+1] is None):
        canvas.setFillColor(color)
        canvas.rect(point[0]-1.6, point[1]-1.6, 3.2, 3.2, fill=1, stroke=0)


def _draw_small_multiples(chart, data, layer):
    """Static equivalent of the report's shared-scale ECharts facets."""
    text, line, category_axis = layer
    width, height, palette = chart.width, chart.height, chart.palette
    series = data['series']
    left, right = 46, width - 58
    all_values = [value for row in series for value in row['values'] if value is not None]
    low, high = min(all_values), max(all_values)
    margin = (high-low or abs(high) or 1)*.14
    low, high = low-margin, high+margin
    row_height = (height-17)/len(series)
    positions = [left + i*(right-left)/(len(data['labels'])-1) for i in range(len(data['labels']))]
    colors = palette['colors']
    for index, row in enumerate(series):
        top = height - index*row_height - 16
        bottom = top - row_height + 22
        y = lambda number: bottom+(number-low)/(high-low)*(top-bottom)
        text(left, top+6, row['name'])
        line(left, bottom, left, top, palette['muted'], .65)
        for tick, label in axis_ticks(low, high, 1, 2):
            line(left-3, y(tick), left, y(tick), palette['muted'], .5)
            text(left-5, y(tick)-3, label, align='right')
        color = colors[index % len(colors)]
        previous = None
        for offset, (x, value) in enumerate(zip(positions, row['values'], strict=True)):
            if value is None:
                previous = None
                continue
            point = x, y(value)
            if previous:
                line(*previous, *point, color, 1.35)
            else:
                _isolated_marker(chart.canv, row['values'], offset, point, color)
            previous = point
        if row['values'][-1] is not None:
            text(right+5, y(row['values'][-1])-3, number(row['values'][-1])+data['unit'])
    category_axis(data['labels'], positions, axis_y=bottom, compact=data.get('compactDates', False))


def _draw_window_changes(chart, data, layer):
    text, line, category, horizontal_ticks, series_legend, shown = layer
    palette, canvas = chart.palette, chart.canv
    layout = chart.categories
    left, right = layout['left'], chart.width-27
    top, bottom = chart.height-chart.top_inset, chart.bottom_inset
    series = data['series']
    colors = palette['colors']
    bound = max(abs(value) for row in series for value in row['values']) or 1
    bound *= 1.5
    scale = lambda number: left+(number+bound)/(2*bound)*(right-left)
    center = scale(0)
    horizontal_ticks(-bound, bound, scale, right-left)
    line(left, bottom, left, top, palette['muted'], .65)
    line(center, bottom, center, top, palette['muted'])
    series_legend(lambda index: colors[index])
    row_height = (top-bottom)/len(chart.category_rows)
    step = min(14, row_height/(len(series)+1))
    for offset, index in enumerate(chart.category_rows):
        middle = top-(offset+.5)*row_height
        category(index, middle)
        for order, row in enumerate(series):
            value = row['values'][index]
            x = scale(value)
            y = middle+((len(series)-1)/2-order)*step
            canvas.setFillColor(colors[order])
            canvas.rect(min(x, center), y-3, abs(x-center), 6, stroke=0, fill=1)
            text(x+(4 if value>=0 else -4), y-3, ('+' if value>0 else '')+shown(value),
                 align='left' if value>=0 else 'right')


def _draw_sample_range(chart, data, layer):
    text, line, horizontal_ticks = layer
    canvas, palette = chart.canv, chart.palette
    minimum, maximum = data['minimum'], data['maximum']
    left, right = 30, chart.width-34
    middle = chart.height*.53
    margin = (maximum-minimum)*.23
    low, high = minimum-margin, maximum+margin
    x = lambda value: left+(value-low)/(high-low)*(right-left)
    from_x, to_x = x(minimum), x(maximum)
    horizontal_ticks(low, high, x, right-left, axis_y=22)
    line(from_x, middle, to_x, middle, palette['ramp'][2], 4)
    line(from_x, middle-5, from_x, middle+5, palette['primary'], 1.4)
    line(to_x, middle-5, to_x, middle+5, palette['primary'], 1.4)
    canvas.setFillColor(palette['paper'])
    canvas.setStrokeColor(palette['primary'])
    canvas.setLineWidth(1.25)
    for sample in data['samples']:
        canvas.circle(x(sample['value']), middle, 2.4, stroke=1, fill=1)
    center = x(data['latest'])
    diamond = canvas.beginPath()
    diamond.moveTo(center, middle+4.4)
    diamond.lineTo(center+4.4, middle)
    diamond.lineTo(center, middle-4.4)
    diamond.lineTo(center-4.4, middle)
    diamond.close()
    canvas.setFillColor(palette['accent'])
    canvas.drawPath(diamond, stroke=0, fill=1)
    unit = data['unit']
    text(from_x, middle+12, number(minimum)+unit, align='center')
    text(to_x, middle+12, number(maximum)+unit, align='center')
    text(center, middle-19, '期末 '+number(data['latest'])+unit, align='center')


def number(value):
    return reading_number(value)


def axis_ticks(lo, hi, divisor, intervals=4):
    """Choose readable axis increments in displayed units."""
    low, high = Decimal(str(lo)) / divisor, Decimal(str(hi)) / divisor
    target = (high - low) / intervals
    scale = Decimal(1).scaleb(target.adjusted())
    step = next(scale * factor for factor in (1, 2, Decimal('2.5'), 5, 10)
                if scale * factor >= target)
    first = int((low / step).to_integral_value(rounding=ROUND_CEILING))
    last = int((high / step).to_integral_value(rounding=ROUND_FLOOR))
    for index in range(first, last + 1):
        value = (step * index).normalize()
        label = format(value, 'e') if value and value.adjusted() < -6 else format(value, ',f')
        yield float(value * divisor), label


def wrapped_label(value, available):
    lines = []
    current = ''
    for character in str(value):
        if character == '\n':
            lines.append(current)
            current = ''
            continue
        if current and label_width(current + character) > available:
            lines.append(current)
            current = ''
        current += character
    return lines + [current]


def label_width(label):
    return pdfmetrics.stringWidth(str(label), FONT, LABEL_SIZE)


def display_label(data, value, *, compact=False):
    raw = str(value)
    mapped = (data.get('dateLabels') or {}).get(raw)
    label = mapped if isinstance(mapped, str) else raw
    if (compact and data.get('compactDates') and len(label) == 10
            and label[4] == label[7] == '-' and label.replace('-', '').isdigit()):
        return label[5:]
    return label


def dense_time_bars(spec):
    """Use a date axis for dense bar plots without thinning the observations."""
    auto = spec.get('auto_visual', {})
    data = auto.get('data', spec.get('data', {}))
    labels = data.get('labels', [])
    if auto.get('scheme', spec.get('scheme')) not in TIME_BAR_CHARTS or len(labels) <= 12:
        return False
    previous = None
    for raw in labels:
        label = display_label(data, raw)
        if len(label) == 7 and label[4] == '-':
            label += '-01'
        if len(label) != 10 or label[4] != '-' or label[7] != '-':
            return False
        try:
            day = date.fromisoformat(label)
        except ValueError:
            return False
        if previous is not None and day <= previous:
            return False
        previous = day
    return True


def series_legend_layout(names, width):
    if not names:
        return [], 0
    columns = min(2, len(names))
    slot = (width - 24) / columns
    labels = [wrapped_label(name, slot - 17) for name in names]
    entries = []
    depth = 12
    for offset in range(0, len(labels), columns):
        group = labels[offset:offset + columns]
        entries.extend((12 + index * slot, depth, lines) for index, lines in enumerate(group))
        depth += 10 * max(map(len, group)) + 4
    return entries, depth - 18


def gradient_color(stops, ratio):
    position = max(0, min(1, ratio)) * (len(stops) - 1)
    index = min(floor(position), len(stops) - 2)
    fraction = position - index
    a, b = stops[index:index + 2]
    return Color(*(floor((start + (end - start) * fraction) * 255 + .5) / 255
                   for start, end in zip(a.rgb(), b.rgb())))


def contrasting_ink(color):
    channels = (v / 12.92 if v <= .04045 else ((v + .055) / 1.055)**2.4 for v in color.rgb())
    light = sum(value * weight for value, weight in zip(channels, (.2126, .7152, .0722)))
    return '#000000' if light > .179 else '#FFFFFF'


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


def category_layout(labels, width, height, legend=None, series_count=1):
    """Measure complete row names and grow static figures when wrapping is needed."""
    leading=10
    measured=max(map(label_width, labels),default=0)
    limit=min(width*.46,width-24-64)
    left=max(min(72 if width>300 else 59,limit),min(measured+12,limit))
    wrapped=[wrapped_label(label,max(1,left-12)) for label in labels]
    legends=[wrapped_label(name,max(1,(width-24)/2-10)) for name in (legend or [])]
    top_padding=32+leading*max(map(len,legends)) if legends else 27
    row_height=max(30,leading*max(map(len,wrapped),default=1)+8,10*series_count+8)
    height=max(height,top_padding+27+len(labels)*row_height)
    return dict(left=left,top=height-top_padding,height=height,labels=wrapped,
                legends=legends,leading=leading,row_height=row_height)


class EvidenceChart(Flowable):
    def __init__(self,spec,width=510,height=154,palette='research'):
        Flowable.__init__(self);self.spec=spec;self.width=width;self.height=height
        auto=spec.get('auto_visual',{});data=auto.get('data',spec.get('data',{}))
        self.mechanism=None;self.categories=None
        self.category_rows = range(0)
        self.time_bars = dense_time_bars(spec)
        self.top_inset = self.bottom_inset = 27
        self.scatter_titles = ([], [])
        self.heatmap_extent = None
        kind=auto.get('scheme',spec.get('scheme'))
        if kind in BAR_CHARTS + ('dumbbell', 'heatmap', 'window_change', 'interval_dot') and not self.time_bars:
            labels = data['rows'] if kind == 'heatmap' else data['labels']
            dates = [display_label(data, period) for period in data.get('periods', [])] if kind == 'dumbbell' else None
            series_count = len(data.get('series', [])) if kind in BAR_CHARTS or kind == 'window_change' else 1
            self.categories = category_layout([display_label(data, label) for label in labels],
                                              width, height, dates, series_count)
            self.category_rows = range(len(labels))
            self.height = self.categories['height']
            self.top_inset = self.height - self.categories['top']
        if kind == 'small_multiples':
            self.height = max(self.height, 24+59*len(data['series']))
        if kind == 'heatmap':
            values = [value for row in data['matrix'] for value in row
                      if type(value) in (int, float) and isfinite(value)]
            minimum, maximum = min(values, default=0), max(values, default=0)
            self.heatmap_extent = (minimum, maximum)
            if minimum != maximum:
                self.bottom_inset += 22
                self.height += 22
        if kind=='evidence' and data.get('qualitative'):
            self.mechanism=mechanism_layout(data,width,height)
            self.height=self.mechanism['height']
        names = []
        if kind == 'scenario_profile':
            names = data['rows']
        elif kind == 'slope':
            names = data['labels']
        elif kind in ('scatter', 'scatter_rug'):
            plot_width = width - (72 if width > 300 else 59) - 24
            x_title = wrapped_label(data['xName'], plot_width) if data.get('xName') else []
            y_title = wrapped_label(data['yName'], plot_width * .55) if data.get('yName') else []
            self.scatter_titles = x_title, y_title
            extra_x = 12 * len(x_title)
            extra_y = 10 * max(0, len(y_title) - 1)
            self.bottom_inset += extra_x
            self.top_inset += extra_y
            self.height += extra_x + extra_y
            if data.get('comparison_periods'):
                names = [point.get('name', '') for point in data['points']]
        elif kind in LINE_CHARTS + BAR_CHARTS + ('window_change',) and len(data.get('series', [])) > 1:
            names = [series.get('name', '') for series in data['series']]
        elif kind in LINE_CHARTS:
            series = data.get('series') or [dict(name=data.get('name', '观测值'))]
            name = display_label(data, series[0].get('name', ''))
            if label_width(name) > width - (72 if width > 300 else 59) - 24:
                names = [name]
        self.series_legend, legend_padding = series_legend_layout([display_label(data, name) for name in names], width)
        self.top_inset += legend_padding
        self.height += legend_padding
        self.palette=theme(palette) or theme('research')
        if self.palette.get('dark'):self.palette=theme('research')

    def pages(self, height_limit):
        """Keep the original dataset and axis scale for each category section."""
        if self.height <= height_limit or self.categories is None:
            yield self
            return
        padding = self.top_inset + self.bottom_inset
        row_height = self.categories['row_height']
        rows_per_page = floor((height_limit-padding)/row_height)
        if rows_per_page < 1:
            return
        for start in range(0, len(self.category_rows), rows_per_page):
            page = copy(self)
            page.category_rows = range(start, min(start+rows_per_page, len(self.category_rows)))
            page.height = padding + row_height*len(page.category_rows)
            yield page

    def draw(self):
        c=self.canv;w,h=self.width,self.height;p=self.palette
        auto=self.spec.get('auto_visual',{});kind=auto.get('scheme',self.spec.get('scheme'))
        d=auto.get('data',self.spec.get('data',{}));colors=p['colors']
        def text(x,y,value,size=8,align='left',color=None):
            size=max(LABEL_SIZE,size);c.setFillColor(color or p['ink']);c.setFont(FONT,size)
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
        unit = d.get('unit', '')
        left = self.categories['left'] if self.categories else 72 if w > 300 else 59
        right, bottom, top = w - 24, self.bottom_inset, h - self.top_inset
        def category(index,y):
            lines=self.categories['labels'][index];leading=self.categories['leading']
            for row,value in enumerate(lines):text(left-6,y+(len(lines)-1)*leading/2-row*leading-3,value,align='right')
        def period_legend():
            for index,lines in enumerate(self.categories['legends']):
                for row,value in enumerate(lines):text(12 if index==0 else w-12,h-12-row*self.categories['leading'],value,color=series_color(index),align='left' if index==0 else 'right')
        observed=[]
        for row in d.get('series',[]):observed.extend(row['values'])
        for row in d.get('pairs',[]):observed.extend(row)
        for row in d.get('points',[]):observed.extend([row['x'],row['y']])
        observed.extend(v for v in d.get('values',[]) if finite(v))
        if kind == 'ecdf':
            observed.extend(row['value'] if isinstance(row, dict) else row for row in d['samples'])
        if kind == 'scenario_profile':
            observed.extend(value for row in d['matrix'] for value in row)
        if kind == 'candlestick':
            observed.extend(value for row in d['values'] for value in row)
        magnitude=max([abs(v) for v in observed if finite(v)] or [0])
        exponent=3*floor(log10(magnitude)/3) if unit and unit not in ('%','倍','个百分点') and magnitude>=1e6 else 0
        divisor=10**exponent
        def shown(value):
            return reading_number(value/divisor,d.get('numberDigits',2)) if finite(value) else number(value)
        def horizontal_ticks(low, high, x, plot_width, label_y=10, *, axis_y=None, axis_divisor=None):
            intervals = max(2, min(4, int(plot_width / 34)))
            previous_end = 2
            axis = bottom if axis_y is None else axis_y
            line(x(low), axis, x(high), axis, p['muted'], .65)
            for value, label in axis_ticks(low, high, divisor if axis_divisor is None else axis_divisor, intervals):
                position = x(value)
                half = label_width(label) / 2
                if position - half < previous_end + 3 or position + half > w - 3:
                    continue
                line(position, axis-3, position, axis,p['muted'],.5)
                text(position, label_y, label, align='center')
                previous_end = position + half
        def vertical_ticks(low, high, y, intervals=4, *, axis_divisor=None):
            line(left, y(low), left, y(high), p['muted'], .65)
            for value, label in axis_ticks(low, high, divisor if axis_divisor is None else axis_divisor, intervals):
                line(left-3, y(value), left, y(value),p['muted'],.5)
                text(left - 6, y(value) - 3, label, align='right')
        def category_axis(labels, positions, label_y=10, *, axis_y=None, compact=False):
            axis = bottom if axis_y is None else axis_y
            line(left, axis, right, axis, p['muted'], .65)
            ticks = []
            max_ticks = 6 if w > 430 else 4
            stride = max(1, ceil((len(labels) - 1) / (max_ticks - 1)))
            for index, (raw, position) in enumerate(zip(labels, positions)):
                if index % stride and index != len(labels) - 1:
                    continue
                label = display_label(d, raw, compact=compact)
                half = label_width(label) / 2
                position = max(half + 3, min(w - half - 3, position))
                ticks.append((position, label, half))
            if not ticks:
                return
            last_x, last_label, last_half = ticks[-1]
            previous_end = 0
            for position, label, half in ticks[:-1]:
                if position - half >= previous_end + 3 and position + half + 3 <= last_x - last_half:
                    line(position, axis-3, position, axis, p['muted'], .5)
                    text(position, label_y, label, align='center')
                    previous_end = position + half
            line(last_x, axis-3, last_x, axis, p['muted'], .5)
            text(last_x, label_y, last_label, align='center')
        label_unit='' if exponent else unit
        if exponent:
            text(right-10,top+11,unit+'×10',align='right',color=p['muted']);text(right-9,top+15,str(exponent),color=p['muted'])
        elif unit:text(right,top+11,unit,align='right',color=p['muted'])
        periods=d.get('comparison_periods') or d.get('periods')
        def series_color(index):
            return (p['accent'],p['primary'])[index%2] if periods and len(periods)==2 else colors[index%len(colors)]
        def series_legend(color_for, *, points=False):
            for j, (key_x, depth, lines) in enumerate(self.series_legend):
                swatch_y = h - depth + 3
                color = color_for(j)
                if points:
                    dot(key_x + 5, swatch_y, color)
                else:
                    line(key_x, swatch_y, key_x + 10, swatch_y, color, 1.6)
                for index, label in enumerate(lines):
                    text(key_x + 14, h - depth - 10 * index, label)
        if kind == 'small_multiples':
            _draw_small_multiples(self, d, (text, line, category_axis))
        elif kind == 'window_change':
            _draw_window_changes(self, d, (text, line, category, horizontal_ticks, series_legend, shown))
        elif kind == 'sample_range':
            _draw_sample_range(self, d, (text, line, horizontal_ticks))
        elif kind == 'ecdf':
            samples = Counter(row['value'] if isinstance(row, dict) else row for row in d['samples'])
            sample_size = sum(samples.values())
            low, high = bounds(samples)
            x = lambda value: left + (value-low)/(high-low)*(right-left)
            y = lambda value: bottom + value/100*(top-bottom)
            horizontal_ticks(low, high, x, right-left)
            vertical_ticks(0, 100, y, axis_divisor=1)
            text(left, top+8, '累计占比%')
            text(right, top+8, f'{sample_size}个样本', align='right')
            previous, total = x(low), 0
            for value, count in sorted(samples.items()):
                position, previous_share = x(value), 100*total/sample_size
                total += count
                share = 100*total/sample_size
                line(previous, y(previous_share), position, y(previous_share), p['primary'], 1.3)
                line(position, y(previous_share), position, y(share), p['primary'], 1.3)
                dot(position, y(share), p['primary'], 1.9)
                if value == d.get('latest'):
                    dot(position, y(share), p['accent'], 3.5)
                    end = position > (left+right)/2
                    text(position+(-6 if end else 6), y(share)-3, '期末 '+shown(value)+label_unit,
                         align='right' if end else 'left', color=p['accent'])
                previous = position
            line(previous, y(100), right, y(100), p['primary'], 1.3)
            if label_unit: text(w/2, 0, label_unit, align='center')
        elif kind == 'scenario_profile':
            low, high = bounds(d['xValues'])
            y_low, y_high = bounds(value for row in d['matrix'] for value in row)
            x = lambda value: left + (value-low)/(high-low)*(right-left)
            y = lambda value: bottom + (value-y_low)/(y_high-y_low)*(top-bottom)
            horizontal_ticks(low, high, x, right-left, 13, axis_divisor=1)
            vertical_ticks(y_low, y_high, y)
            text(w/2, 1, d['xName']+d.get('xUnit', ''), align='center')
            text(left, top+8, unit)
            series_legend(lambda i: colors[i])
            for index, values in enumerate(d['matrix']):
                previous = None
                for setting, value in zip(d['xValues'], values, strict=True):
                    if value is None:
                        previous = None
                        continue
                    point = x(setting), y(value)
                    if previous is not None: line(*previous, *point, colors[index], 1.3)
                    dot(*point, colors[index], 2.2)
                    previous = point
        elif kind in LINE_CHARTS + ('slope',):
            if kind == 'slope':
                series = [dict(name=name, values=pair) for name, pair in zip(d['labels'], d['pairs'])]
                labels = d['periods']
                inset = (right - left) / 5
            else:
                series = d.get('series') or [dict(name=d.get('name','观测值'),values=d.get('values',[]))]
                labels = d['labels']
                inset = 0
            lo, hi = bounds([v for row in series for v in row['values']])
            x = lambda i: left + inset + (right - left - 2 * inset) * i / max(1, len(labels) - 1)
            y = lambda v: bottom + (v - lo) / (hi - lo) * (top - bottom)
            vertical_ticks(lo, hi, y)
            for j,row in enumerate(series):
                color=colors[j%len(colors)];previous=None
                for i,value in enumerate(row['values']):
                    if not finite(value):previous=None;continue
                    point=(x(i),y(value))
                    if previous:line(*previous,*point,color,width=1.3)
                    if kind == 'slope':
                        dot(*point, color, 2)
                    elif not previous:
                        _isolated_marker(c, row['values'], i, point, color)
                    previous=point
            if self.series_legend:
                series_legend(lambda index: colors[index % len(colors)])
            else:
                name = display_label(d, series[0].get('name', ''))
                text(left, h-11, name, color=colors[0])
            category_axis(labels, (x(i) for i in range(len(labels))), compact=kind!='slope')
        elif kind in ('scatter', 'scatter_rug'):
            points=d['points'];xlo,xhi=bounds([r['x'] for r in points]);ylo,yhi=bounds([r['y'] for r in points])
            x=lambda value:left+(value-xlo)/(xhi-xlo)*(right-left);y=lambda value:bottom+(value-ylo)/(yhi-ylo)*(top-bottom)
            x_title, y_title = self.scatter_titles
            horizontal_ticks(xlo, xhi, x, right - left, 10 + self.bottom_inset - 27)
            vertical_ticks(ylo, yhi, y, intervals=3)
            for index, label in enumerate(x_title):
                text((left + right)/2, 4 + 10*(len(x_title)-1-index), label, align='center')
            for index, label in enumerate(y_title):
                text(left, top + 11 + 10*(len(y_title)-1-index), label)
            positions = [(x(row['x']), y(row['y'])) for row in points]
            if kind == 'scatter_rug':
                for px, py in positions:
                    line(px, top-6, px, top, p['accent'], .7)
                    line(right-6, py, right, py, p['accent'], .7)
            coincident_pair = len(positions) == 2 and positions[0] == positions[1]
            for i, (px, py) in enumerate(positions):
                dot(px, py, series_color(i), 5 if coincident_pair and i == 0 else 3)
            series_legend(series_color, points=True)
        elif kind=='heatmap':
            matrix, labels = d['matrix'], d['labels']
            minimum, maximum = self.heatmap_extent
            signed = minimum < 0 < maximum
            bound = max(abs(minimum), maximum)
            low = -bound if signed else minimum
            high = bound if signed else maximum
            colorscale = (p['negative'], p['paper'], p['positive']) if signed and p['id']=='research' else p['ramp']
            stops = [HexColor(color) for color in colorscale]
            cellw = (right-left)/max(1,len(labels))
            cellh = (top-bottom)/max(1,len(self.category_rows))
            for offset, r in enumerate(self.category_rows):
                row = matrix[r]
                cy = top - (offset+.5)*cellh
                category(r, cy)
                for i, value in enumerate(row):
                    cell_x, cell_y = left + i*cellw, cy - cellh/2
                    observed_value = finite(value)
                    ratio = (value-low)/(high-low) if observed_value and high>low else 0
                    fill = gradient_color(stops, ratio) if observed_value else HexColor(p['paper'])
                    c.setFillColor(fill)
                    c.setStrokeColor(p['grid'])
                    c.setLineWidth(.4)
                    c.rect(cell_x+.3, cell_y+.3, max(.1,cellw-.6), cellh-.6,
                           stroke=0 if observed_value else 1, fill=1)
                    label = shown(value) if observed_value else '-'
                    if label_width(label) <= cellw - 1.5:
                        text(cell_x+cellw/2, cy-3, label, align='center',
                             color=contrasting_ink(fill) if observed_value else p['muted'])
            category_axis(labels, (left + (i+.5)*cellw for i in range(len(labels))), 10 + bottom - 27)
            if high > low:
                width = min(128, right-left)
                start = (right + left - width)/2
                for index in range(32):
                    c.setFillColor(gradient_color(stops, index/31))
                    c.rect(start+index*width/32, 15, width/32, 5, stroke=0, fill=1)
                text(start, 4, reading_number(low/divisor, 12))
                text(start+width, 4, reading_number(high/divisor, 12), align='right')
                if signed:
                    text(start+width/2, 4, '0', align='center')
        elif kind=='dumbbell':
            pairs = d['pairs']
            endpoints = [value for pair in pairs for value in pair]
            lo, hi = bounds(endpoints)
            if min(endpoints) >= 0:
                lo = max(0, lo)
            if max(endpoints) <= 0 and min(endpoints) < 0:
                hi = min(0, hi)
            x = lambda value: left + (value-lo)/(hi-lo)*(right-left)
            horizontal_ticks(lo, hi, x, right - left)
            line(left, bottom, left, top, p['muted'], .65)
            for offset, i in enumerate(self.category_rows):
                pair=pairs[i]
                y=top-(offset+.5)*(top-bottom)/len(self.category_rows);category(i,y)
                line(x(pair[0]),y,x(pair[1]),y,p['muted'],1.5)
                for k,v in enumerate(pair):dot(x(v),y,series_color(k));text(x(v),y+(8 if k else -13),shown(v)+label_unit,size=8,align='center')
            period_legend()
        elif kind in BAR_CHARTS:
            labels=d['labels'];series=d.get('series') or [dict(name=d.get('name',''),values=d['values'])]
            series_legend(series_color)
            values = [v for row in series for v in row['values'] if finite(v)]
            benchmark = d['reference'] if kind == 'benchmark_dot' else None
            lo, hi = bounds(values if benchmark is None else [*values, benchmark], benchmark is None)
            points_only = kind in ('lollipop', 'benchmark_dot')
            focus = d.get('focusIndex')
            def bar_color(index, series_index, value):
                if kind == 'lollipop' and type(focus) is int and 0 <= focus < len(labels):
                    return p['primary'] if index == focus else p['muted']
                if kind in ('diverging_bar', 'return_bars'):
                    return p['positive'] if value >= 0 else p['negative']
                return series_color(series_index)
            if self.time_bars:
                step = (right-left)/len(labels)
                y = lambda value: bottom+(value-lo)/(hi-lo)*(top-bottom)
                vertical_ticks(lo, hi, y)
                baseline = y(0)
                line(left, baseline, right, baseline, p['muted'], .65)
                bar_width = min(8, step*.76/len(series))
                for j, row in enumerate(series):
                    for i, value in enumerate(row['values']):
                        if not finite(value):
                            continue
                        x = left+(i+.5)*step+(j-(len(series)-1)/2)*bar_width
                        end = y(value)
                        color = bar_color(i, j, value)
                        if points_only:
                            line(x, baseline, x, end, p['ramp'][1], min(1, bar_width))
                            dot(x, end, color, min(2.2, max(.35, bar_width/2)))
                        else:
                            c.setFillColor(color)
                            c.rect(x-bar_width/2,min(baseline,end),bar_width,abs(end-baseline),stroke=0,fill=1)
                category_axis(labels, (left+(i+.5)*step for i in range(len(labels))))
                return
            plot_left, plot_right = left, right
            if not points_only:
                # The unit is already marked above the chart. Reserve room for
                # values alongside the bars, while retaining a readable plot.
                value_labels = {value: ('+' if kind == 'diverging_bar' and value > 0 else '') + shown(value)
                                for value in values}
                widths = {value: label_width(label)
                          for value, label in value_labels.items()}
                left_padding = max((width + 5 for value, width in widths.items() if value < 0), default=0)
                right_padding = max((width + 9 - (w - right) for value, width in widths.items() if value >= 0), default=0)
                right_padding = max(0, right_padding)
                padding = min(1, max(0, right - left - 64) / max(1, left_padding + right_padding))
                plot_left += left_padding * padding
                plot_right -= right_padding * padding
            x = lambda value: plot_left + (value - lo) / (hi - lo) * (plot_right - plot_left)
            horizontal_ticks(lo, hi, x, plot_right - plot_left)
            line(plot_left, bottom, plot_left, top, p['muted'], .65)
            if benchmark is not None:
                guide = x(benchmark)
                c.setDash(3,2)
                line(guide,bottom,guide,top,p['accent'],.8)
                c.setDash()
                text(guide,top+12,d['referenceName'],align='center',color=p['ink'])
            for offset, i in enumerate(self.category_rows):
                y=top-(offset+.5)*(top-bottom)/len(self.category_rows);category(i,y)
                for j,row in enumerate(series):
                    v=row['values'][i]
                    if not finite(v):continue
                    yy=y+(j-(len(series)-1)/2)*10;color=bar_color(i,j,v)
                    if points_only:
                        if benchmark is None:line(x(0),yy,x(v),yy,color,1)
                        dot(x(v),yy,color)
                    else:c.setFillColor(color);c.rect(min(x(0),x(v)),yy-2,abs(x(v)-x(0)),4,stroke=0,fill=1)
                    if points_only:
                        # Above the point, never into the category column for
                        # negative values. Keep the whole label on the canvas.
                        label=shown(v)+label_unit
                        half=label_width(label)/2
                        label_x=max(left+half,min(w-half-3,x(v)))
                        text(label_x,yy+8,label,align='center',size=8.25,color=color)
                    else:
                        value_width = widths[v]
                        label_x = x(v) + 5 if v >= 0 else x(v) - 5 - value_width
                        label_x = max(left, min(w - value_width - 4, label_x))
                        text(label_x, yy - 3, value_labels[v], color=color)
        elif kind=='candlestick':
            rows=d['values'];labels=d['labels'];lo,hi=bounds([v for row in rows for v in row]);y=lambda v:bottom+(v-lo)/(hi-lo)*(top-bottom);step=(right-left)/len(rows)
            vertical_ticks(lo, hi, y)
            for i,(op,cl,low,high) in enumerate(rows):
                x=left+(i+.5)*step;color=p['positive'] if cl>=op else p['negative'];line(x,y(low),x,y(high),color,1)
                c.setFillColor(color);c.rect(x-min(5,step*.3),min(y(op),y(cl)),min(10,step*.6),max(1,abs(y(op)-y(cl))),stroke=0,fill=1)
            category_axis(labels, (left+(i+.5)*step for i in range(len(labels))), compact=True)
        elif kind=='event_timeline':
            dates = [date.fromisoformat(row['date']) for row in d['events']]
            lo, hi = min(dates), max(dates)
            span = max(1, (hi-lo).days)
            center=(bottom+top)/2
            line(left,center,right,center,p['muted'],.8)
            for index,(day,row) in enumerate(zip(dates,d['events'],strict=True)):
                x=left+(day-lo).days*(right-left)/span
                above=index%2==0
                direction=1 if above else -1
                dot(x,center,p['primary'],2.6)
                line(x,center,x,center+direction*12,p['muted'],.5)
                ws=min(96,(right-left)/max(2,len(dates)/2))
                caption=wrapped_label(row['name'],ws)
                label_x=max(ws/2+2,min(w-ws/2-2,x))
                text(label_x,center+direction*18,day.isoformat(),align='center',color=p['muted'])
                for offset,label in enumerate(caption[:2]):
                    text(label_x,center+direction*(29+offset*10),label,align='center',size=8)
        elif kind=='evidence' and d.get('qualitative'):
            layout=self.mechanism;box=layout['box'];node_height=layout['node_height']
            positions=dict(zip((node['id'] for node in d['nodes']),layout['positions']))
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
            for index,node in enumerate(d['nodes']):
                cx,cy=layout['positions'][index];lines=layout['labels'][index]
                c.setFillColor(p['paper']);c.setStrokeColor(p['primary'] if node.get('kind')=='condition' else p['accent']);c.setLineWidth(.8)
                c.roundRect(cx-box/2,cy-node_height/2,box,node_height,4,stroke=1,fill=1)
                for row,label in enumerate(lines):text(cx,cy+(len(lines)-1)*5.25-row*10.5-3,label,align='center',size=8.25)
        elif kind in ('range', 'interval_dot'):
            rows = d['ranges'] if kind == 'range' else [dict(name=name, min=low, max=high, value=center)
                for name, low, high, center in zip(d['labels'], d['lower'], d['upper'], d['center'], strict=True)]
            if kind == 'interval_dot':
                caption = (reading_number(d['level']*100)+'%置信区间' if d['intervalType'] == 'confidence'
                           else '情景区间' if d['intervalType'] == 'scenario' else '观测区间')
                text(left, top+8, caption)
            lo,hi=bounds([r[k] for r in rows for k in ('min','max')]);x=lambda v:left+(v-lo)/(hi-lo)*(right-left)
            horizontal_ticks(lo, hi, x, right - left)
            line(left, bottom, left, top, p['muted'], .65)
            for i,row in enumerate(rows):
                y=top-(i+.5)*(top-bottom)/len(rows)
                if self.categories:
                    category(i,y)
                else:
                    text(left-6,y-3,row['name'],align='right')
                line(x(row['min']),y,x(row['max']),y,colors[0],3)
                dot(x(row['min']),y,colors[0]);dot(x(row['max']),y,colors[0])
                text((x(row['min'])+x(row['max']))/2,y+11,shown(row['min'])+'至'+shown(row['max'])+label_unit,align='center')
                if row.get('value') is not None:
                    position = x(row['value'])
                    dot(position,y,p['accent'],4)
                    text(position,y-16,('中心值 ' if kind=='interval_dot' else '观测值 ')+shown(row['value'])+label_unit,align='center')
        else:
            # An unsupported static grammar keeps its actual evidence table.
            text(left,top-15,'原值见下表',size=9)


def chart_pdf(spec,width=510,height=154,palette='research'):
    item=spec if isinstance(spec,EvidenceChart) else EvidenceChart(spec,width,height,palette)
    stream=BytesIO();canvas=Canvas(stream,pagesize=(item.width,item.height));item.drawOn(canvas,0,0);canvas.showPage();canvas.save();return stream.getvalue()


def chart_png(spec,width=510,height=154,palette='research'):
    import pypdfium2
    document=pypdfium2.PdfDocument(chart_pdf(spec,width,height,palette))
    try:
        bitmap=document[0].render(scale=2);image=bitmap.to_pil();stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()
    finally:document.close()

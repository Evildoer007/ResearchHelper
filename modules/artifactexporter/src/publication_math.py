"""MathML layout shared by PDF, without converting equations to code text."""
from lxml import etree
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Flowable
from .publication_graphics import FONT


def _layout(node,size):
    tag=etree.QName(node).localname;children=list(node)
    if not children:
        value=node.text or '';width=pdfmetrics.stringWidth(value,FONT,size)
        return width,size+3,3,lambda c,x,y:c.drawString(x,y+3,value)
    if tag=='mfrac' and len(children)==2:
        a=_layout(children[0],size*.9);b=_layout(children[1],size*.9);width=max(a[0],b[0])+6
        def draw(c,x,y):
            c.setFont(FONT,size*.9);a[3](c,x+(width-a[0])/2,y+b[1]+6);b[3](c,x+(width-b[0])/2,y)
            c.setLineWidth(.5);c.line(x,y+b[1]+3,x+width,y+b[1]+3)
        return width,a[1]+b[1]+6,b[1]+3,draw
    if tag in ('msup','msub') and len(children)==2:
        a=_layout(children[0],size);b=_layout(children[1],size*.7);offset=b[1]*.6 if tag=='msub' else 0
        def draw(c,x,y):
            c.setFont(FONT,size);a[3](c,x,y+offset);c.setFont(FONT,size*.7)
            b[3](c,x+a[0]+1,y if tag=='msub' else y+a[2]+size*.6)
        return a[0]+b[0]+1,max(a[1]+offset,b[1]+a[2]+size*.6 if tag=='msup' else a[1]+offset),a[2]+offset,draw
    boxes=[_layout(child,size) for child in children];base=max(b[2] for b in boxes);height=base+max(b[1]-b[2] for b in boxes)
    def draw(c,x,y):
        for box in boxes:
            c.setFont(FONT,size);box[3](c,x,y+base-box[2]);x+=box[0]+1
    return sum(b[0]+1 for b in boxes),height,base,draw


class MathFormula(Flowable):
    def __init__(self,xml,size=10):
        Flowable.__init__(self);self.size=size;self.box=_layout(etree.fromstring(xml.encode()),size)
        self.width=self.box[0];self.height=self.box[1]+8
    def draw(self):
        self.canv.setFont(FONT,self.size);self.canv.setFillColor('#252525');self.canv.setStrokeColor('#252525');self.box[3](self.canv,0,4)

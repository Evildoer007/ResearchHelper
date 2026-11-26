/* Offline print-only table groups. Original rows and reading DOM stay intact. */
(function(global){
 'use strict';
 const prepared=[];
 function restore(){for(const {table,parts,className} of prepared){if(className===null)table.removeAttribute('class');else table.setAttribute('class',className);parts?.remove()}prepared.length=0}
 function prepare(){
  if(prepared.length)return;
  const ruler=document.createElement('div');
  ruler.style.cssText='position:absolute;visibility:hidden;width:196mm;height:283mm;pointer-events:none';
  document.body.append(ruler);const page=ruler.getBoundingClientRect();ruler.remove();
  for(const table of document.querySelectorAll('main table')){
   const head=table.tHead,rows=[...table.tBodies].flatMap(body=>[...body.rows]);
   if(!head||!rows.length||table.tFoot||table.querySelector('table'))continue;
   const header=[...head.rows[head.rows.length-1].cells],cells=[...table.rows].flatMap(row=>[...row.cells]);
   if(!header.length||cells.some(cell=>cell.rowSpan!==1||cell.colSpan!==1)||rows.some(row=>row.cells.length!==header.length))continue;
   const measure=table.cloneNode(true);
   // Measure the condensed observation ledger before the print media rules apply.
   measure.classList.add('rh-print-table-measure');
   measure.style.cssText+=';position:absolute;visibility:hidden;pointer-events:none';
   measure.style.setProperty('width',Math.min(page.width,table.getBoundingClientRect().width)+'px','important');measure.style.setProperty('max-width','none','important');
   table.after(measure);
   const measuredHeader=[...measure.tHead.rows[measure.tHead.rows.length-1].cells];
   const widths=measuredHeader.map(cell=>cell.getBoundingClientRect().width),total=widths.reduce((a,b)=>a+b,0);
   const captionHeight=measure.caption?.getBoundingClientRect().height||0,headHeight=measure.tHead.getBoundingClientRect().height;
   const heights=[...measure.tBodies].flatMap(body=>[...body.rows]).map(row=>row.getBoundingClientRect().height);
   const limit=page.height-headHeight-4,firstLimit=limit-captionHeight;
   const top=table.getBoundingClientRect().top-table.closest('main').getBoundingClientRect().top;
   const remaining=page.height-((top%page.height+page.height)%page.height);
   let capacity=Math.min(firstLimit,remaining-headHeight-captionHeight-4);
   if(capacity<heights.slice(0,Math.min(2,rows.length)).reduce((a,b)=>a+b,0))capacity=firstLimit;
   measure.remove();
   if(firstLimit<=0||heights[0]>firstLimit||!total||heights.some(height=>height<=0||height>limit))continue;
   // Native page breaks can move earlier figures and calculation groups.
   // A table that fits on one page needs no guessed first-page remainder.
   // Decide from its measured height rather than a fixed row-count limit.
   if(heights.reduce((sum,height)=>sum+height,0)<=firstLimit){
    const className=table.getAttribute('class');
    table.classList.add('publication-table--short');
    prepared.push({table,parts:null,className});continue;
   }
   const freshPage=table.classList.contains('report-series-table');
   if(freshPage)capacity=firstLimit;
   const groups=[];let group=[],height=0;
   rows.forEach((row,index)=>{
    if(group.length&&height+heights[index]>capacity){groups.push(group);group=[];height=0;capacity=freshPage?firstLimit:limit}
    group.push(row);height+=heights[index];
   });if(group.length)groups.push(group);
   if(groups.length<2)continue;
   const parts=document.createElement('div');parts.className='rh-print-table-parts'+(freshPage?' rh-print-table-new-page':'');
   groups.forEach((items,index)=>{
    const copy=table.cloneNode(false);copy.classList.add('rh-print-table-copy');copy.style.tableLayout='fixed';
    if(!freshPage&&index===0&&table.caption)copy.append(table.caption.cloneNode(true));
    const columns=document.createElement('colgroup');
    for(const width of widths){const col=document.createElement('col');col.style.width=width/total*100+'%';columns.append(col)}
    const repeatedHead=head.cloneNode(true);
    if(freshPage&&table.caption){
     // WebKit may clip a native caption after a page break. Keep the series
     // identity in the repeating header so it remains visible on every page.
     const title=document.createElement('tr'),cell=document.createElement('th');
     cell.className='rh-print-series-caption';cell.colSpan=header.length;cell.textContent=table.caption.textContent;
     title.append(cell);repeatedHead.prepend(title);
    }
    copy.append(columns,repeatedHead);const body=document.createElement('tbody');
    for(const row of items)body.append(row.cloneNode(true));copy.append(body);
    copy.removeAttribute('id');for(const node of copy.querySelectorAll('[id]'))node.removeAttribute('id');
    parts.append(copy);
   });
   const className=table.getAttribute('class');
   table.after(parts);table.classList.add('rh-print-table-original');prepared.push({table,parts,className});
  }
 }
 global.RHTablePrint={prepare,restore};
})(window);

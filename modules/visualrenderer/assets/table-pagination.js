/* Offline print-only table groups. Original rows and reading DOM stay intact. */
(function(global){
 'use strict';
 const prepared=[];
 function restore(){for(const {table,parts,className} of prepared){if(className===null)table.removeAttribute('class');else table.setAttribute('class',className);parts.remove()}prepared.length=0}
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
   measure.style.cssText+=';position:absolute;visibility:hidden;pointer-events:none';
   measure.style.setProperty('width',page.width+'px','important');measure.style.setProperty('max-width','none','important');
   table.after(measure);
   const measuredHeader=[...measure.tHead.rows[measure.tHead.rows.length-1].cells];
   const widths=measuredHeader.map(cell=>cell.getBoundingClientRect().width),total=widths.reduce((a,b)=>a+b,0);
   const captionHeight=measure.caption?.getBoundingClientRect().height||0,headHeight=measure.tHead.getBoundingClientRect().height;
   const heights=[...measure.tBodies].flatMap(body=>[...body.rows]).map(row=>row.getBoundingClientRect().height),limit=page.height-headHeight-captionHeight-4;
   const top=table.getBoundingClientRect().top-table.closest('main').getBoundingClientRect().top;
   const remaining=page.height-((top%page.height+page.height)%page.height);
   let capacity=Math.min(limit,remaining-headHeight-captionHeight-4);
   if(capacity<heights.slice(0,Math.min(2,rows.length)).reduce((a,b)=>a+b,0))capacity=limit;
   measure.remove();
   if(limit<=0||!total||heights.some(height=>height<=0||height>limit))continue;
   // Short tables already move as a whole print unit. Do not split them
   // against the old page remainder and duplicate headers on the next page.
   if(table.classList.contains('publication-table--short')&&heights.reduce((sum,height)=>sum+height,0)<=limit)continue;
   const groups=[];let group=[],height=0;
   rows.forEach((row,index)=>{
    if(group.length&&height+heights[index]>capacity){groups.push(group);group=[];height=0;capacity=limit}
    group.push(row);height+=heights[index];
   });if(group.length)groups.push(group);
   if(groups.length<2)continue;
   const parts=document.createElement('div');parts.className='rh-print-table-parts';
   groups.forEach((items,index)=>{
    const copy=table.cloneNode(false);copy.classList.add('rh-print-table-copy');copy.style.tableLayout='fixed';
    if(index===0&&table.caption)copy.append(table.caption.cloneNode(true));
    const columns=document.createElement('colgroup');
    for(const width of widths){const col=document.createElement('col');col.style.width=width/total*100+'%';columns.append(col)}
    copy.append(columns,head.cloneNode(true));const body=document.createElement('tbody');
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

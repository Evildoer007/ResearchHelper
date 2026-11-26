/* Extract one real declaration without executing adjacent browser bootstrap. */
const {Script}=require('node:vm');

function functionSource(source,name){
  const match=new RegExp('(?:async\\s+)?function\\s+'+name+'\\s*\\(').exec(source);
  if(!match)throw Error('Missing function: '+name);
  // Let the JavaScript parser handle template strings, comments and nested
  // blocks. The first complete function expression has no adjacent statements.
  for(let end=source.indexOf('}',match.index);end>=0;end=source.indexOf('}',end+1)){
    const declaration=source.slice(match.index,end+1);
    try{new Script('('+declaration+')');return declaration}catch(error){
      if(!(error instanceof SyntaxError))throw error;
    }
  }
  throw Error('Incomplete function: '+name);
}
module.exports={functionSource};

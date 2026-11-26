// Copy the approved user-supplied icon assets. Do not redraw or recompose them.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
const root=process.cwd();
const output=path.resolve(process.env.RESEARCHHELPER_RELEASE_OUTPUT||path.join(root,'result/build-staging/current'));
const relative=path.relative(path.join(root,'result/build-staging'),output);
if(!relative||relative.startsWith('..')||path.isAbsolute(relative))throw new Error('Icon output must remain inside ResearchHelper build staging');
const manifest=JSON.parse(await fs.readFile(path.join(root,'assets/icons/icon-manifest.json'),'utf8'));
const copies=[
  ['researchhelper-app-icon-tile-light.icns','ResearchHelper.icns'],
  ['researchhelper-app-icon-tile-dark.icns','ResearchHelper-dark.icns'],
  ['researchhelper-app-icon-tile-light.png','ResearchHelperDock.png'],
  ['researchhelper-app-icon-tile-dark.png','ResearchHelperDockDark.png'],
  ['researchhelper-app-icon-tile-light.ico','ResearchHelper.ico'],
  ['researchhelper-app-icon-tile-dark.ico','ResearchHelper-dark.ico'],
];
const verified=[];
for(const [source,destination] of copies){
  const name='assets/icons/'+source, bytes=await fs.readFile(path.join(root,name));
  if(crypto.createHash('sha256').update(bytes).digest('hex')!==manifest.source_hashes[name])throw new Error('Unapproved icon asset: '+source);
  verified.push([destination,bytes]);
}
await fs.mkdir(output,{recursive:true});
for(const [name,bytes] of verified)await fs.writeFile(path.join(output,name),bytes);

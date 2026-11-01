// Render the exact existing SVG, then form an Apple icon set. No re-drawn mark.
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.RESEARCHHELPER_PLAYWRIGHT||'/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=process.cwd(),out=path.resolve(process.env.RESEARCHHELPER_RELEASE_OUTPUT||path.join(root,'result/build-staging/current'));
const releaseRelative=path.relative(path.join(root,'result/build-staging'),out);
if(!releaseRelative||releaseRelative.startsWith('..')||path.isAbsolute(releaseRelative))throw new Error('Icon output must remain inside ResearchHelper build staging');
const iconset=path.join(out,'ResearchHelper.iconset');await fs.mkdir(iconset,{recursive:true});
const markset=path.join(out,'mark-renders');await fs.mkdir(markset,{recursive:true});
const svg=await fs.readFile(path.join(root,'products/app/frontend/app-icon.svg'),'utf8');
const brand=await fs.readFile(path.join(root,'products/app/frontend/brand.svg'),'utf8');
// The tile and in-App brand must contain exactly the same source center mark.
const inner=svg.slice(svg.indexOf('viewBox="0 0 40 40">')+'viewBox="0 0 40 40">'.length,svg.indexOf('</svg>'));
const expected=brand.slice(brand.indexOf('viewBox="0 0 40 40">')+'viewBox="0 0 40 40">'.length,brand.indexOf('</svg>'));
if(inner!==expected)throw new Error('ResearchHelper tile center differs from the approved demo mark');
// Software rasterization and one color profile keep gradient/filter pixels
// stable across render pages and eliminate GPU dithering from shipped assets.
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu','--force-color-profile=srgb']});
try{for(const size of [16,32,64,128,256,512,1024]){
// A fresh rendering surface prevents compositor tiles from a previous size
// leaking into a smaller icon. Size only the outer SVG, never its nested mark.
const supersample=size<=64?8:size<=256?4:2,workSize=size*supersample;
const page=await browser.newPage({viewport:{width:workSize,height:workSize},deviceScaleFactor:1});
try{await page.setContent('<html><style>html,body{margin:0;background:transparent}body>svg{display:block;width:100vw;height:100vh}</style>'+svg+'</html>');await page.evaluate(()=>{const root=document.querySelector('body>svg');for(const child of Array.from(root.children))if(child.localName!=='svg')child.remove();});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await page.screenshot({path:path.join(markset,'mark-'+size+'.png'),omitBackground:true});}finally{await page.close()}
}}finally{await browser.close()}
// Copy OH's native tile finish, including its size-specific antialiasing;
// compose only ResearchHelper's original SVG mark, never re-draw its artwork.
const python=process.env.RESEARCHHELPER_BUILD_PYTHON;
if(!python)throw new Error('Build launcher must supply its existing Python interpreter');
execFileSync(python,['-B','-c','import sys;sys.path.insert(0,"packaging/app/macos");from icon_material import render_icon_assets;render_icon_assets()'],{cwd:root});
execFileSync('iconutil',['-c','icns',iconset,'-o',path.join(out,'ResearchHelper.icns')]);

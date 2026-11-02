const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../host/host.jsx'),'utf8');
function setup(){
 const sequences=[];Object.defineProperty(sequences,'numSequences',{get(){return this.length;}});
 const app={project:{path:'C:\\project\\edit.prproj',name:'Edit',sequences,rootItem:{},save(){this.saved=true;},openSequence(id){this.opened=id;},importFiles(){sequences.push({name:'Minecut',sequenceID:'new',videoTracks:{numTracks:1,0:{clips:{numItems:3}}}});return true;}},getCurrentProjectViewSelection(){return [{name:'Clip',getMediaPath(){return 'C:\\path\\quote" & café.mp4';},isSequence(){return false;}}];}};
 const box={app,File:function(p){this.exists=p!=='missing';},JSON:undefined};
 vm.createContext(box);vm.runInContext(source,box);return box;
}
let s=setup();assert.equal(JSON.parse(s.Minecut.projectInfo()).ok,true);
assert.equal(JSON.parse(s.Minecut.selectedSource()).mediaPath,'C:\\path\\quote" & café.mp4');
assert.equal(JSON.parse(s.Minecut.importEdit({projectPath:'different',path:'good',clips:3})).ok,false);
assert.equal(s.app.project.sequences.length,0);
assert.equal(JSON.parse(s.Minecut.importEdit({projectPath:s.app.project.path,path:'missing',clips:3})).ok,false);
assert.equal(JSON.parse(s.Minecut.importEdit({projectPath:s.app.project.path,path:'good',clips:3,openAfter:true})).ok,true);
assert.equal(s.app.project.opened,'new');assert(s.app.project.saved);
s=setup();assert.equal(JSON.parse(s.Minecut.importEdit({projectPath:s.app.project.path,path:'good',clips:4})).ok,false);
assert(!s.app.project.saved);
console.log('Host contract tests passed: no JSON global, escaped paths, project guard, missing file, imported clip count and save.');

s=setup();s.app.getCurrentProjectViewSelection=()=>[{isSequence:()=>true,getMediaPath:()=>'',name:'Stale sequence'}];
const clip={mediaType:'Video',projectItem:{name:'Day 1.mp4',getMediaPath:()=> 'C:\\Day 1.mp4',isSequence:()=>false},inPoint:{seconds:370},outPoint:{seconds:775},getSpeed:()=>1};
s.app.project.activeSequence={getSelection:()=>[clip,{mediaType:'Audio'}]};
let chosen=JSON.parse(s.Minecut.selectedSource());assert(chosen.ok);assert.deepStrictEqual(chosen.sourceRange,{start_seconds:370,end_seconds:775});
s.app.project.activeSequence.getSelection=()=>[clip,clip];assert(!JSON.parse(s.Minecut.selectedSource()).ok);
s.app.project.activeSequence.getSelection=()=>[clip];clip.getSpeed=()=>2;assert(!JSON.parse(s.Minecut.selectedSource()).ok);
console.log('Timeline priority, linked audio, exact trim range, ambiguous selection and speed guards passed.');

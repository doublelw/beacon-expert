"""把 mech.Assembly 导出的 assembly.json 做成单文件交互网页（双击即开，不联网）。

    python viewer.py 输出目录/assembly.json [--out 爆炸图.html] [--theme light|dark] [--subtitle 文字]

网页功能：爆炸滑杆、按真实传动比运转、剖切、边线、零件标签、BOM 悬停高亮、视角预设。
render_media.py 通过 window.CAD 驱动它出静帧与视频。
"""
import argparse, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parents[1] / "assets" / "viewer" / "three-cad-bundle.min.js"

HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg1:#f6f4ef;--bg2:#d9d4ca;--ink:#1d1f22;--mute:#6b6a66;--panel:rgba(255,255,255,.72);--line:rgba(0,0,0,.08);--acc:#d4572f}
body.dark{--bg1:#2a2d33;--bg2:#0d0e10;--ink:#eceae4;--mute:#9a9892;--panel:rgba(24,26,30,.72);--line:rgba(255,255,255,.08)}
*{box-sizing:border-box}html,body{margin:0;height:100%;overflow:hidden;font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;color:var(--ink)}
body{background:radial-gradient(120% 90% at 50% 38%,var(--bg1) 0%,var(--bg2) 100%)}
canvas{position:fixed;inset:0;width:100%;height:100%;display:block}
#hud{position:fixed;left:32px;top:28px;max-width:46vw;pointer-events:none}
#hud h1{margin:0;font-size:30px;font-weight:700;letter-spacing:.02em}
#hud .sub{margin-top:8px;font-size:14px;color:var(--mute);line-height:1.6}
#hud .chips{margin-top:12px;display:flex;flex-wrap:wrap;gap:8px}
.chip{font-size:12.5px;padding:5px 10px;border-radius:999px;background:var(--panel);border:1px solid var(--line);backdrop-filter:blur(8px)}
.chip b{font-weight:650}.ok{color:#2f8f5b}.bad{color:#c43d2b}
#bom{position:fixed;right:24px;top:24px;width:264px;max-height:calc(100% - 140px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:10px;backdrop-filter:blur(10px)}
#bom h2{font-size:13px;margin:4px 6px 8px;color:var(--mute);font-weight:600;letter-spacing:.06em}
.row{display:grid;grid-template-columns:24px 1fr auto;gap:8px;align-items:center;padding:7px 6px;border-radius:8px;font-size:13px;cursor:default}
.row:hover,.row.on{background:rgba(212,87,47,.10)}
.no{width:22px;height:22px;border-radius:50%;background:var(--ink);color:var(--bg1);font-size:11px;display:grid;place-items:center;font-weight:700}
.row small{display:block;color:var(--mute);font-size:11.5px;margin-top:1px}.row .m{color:var(--mute);font-size:12px;font-variant-numeric:tabular-nums}
#bar{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);display:flex;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:8px 14px;backdrop-filter:blur(10px)}
#bar label{font-size:13px;color:var(--mute)}#bar input[type=range]{width:200px;accent-color:var(--acc)}
#bar button{font:inherit;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer}
#bar button.on{background:var(--ink);color:var(--bg1)}
#labels{position:fixed;inset:0;pointer-events:none}
#labels svg{position:absolute;inset:0;width:100%;height:100%}
.lab{position:absolute;transform:translate(-50%,-50%);font-size:12.5px;padding:4px 9px 4px 4px;border-radius:999px;background:var(--panel);border:1px solid var(--line);white-space:nowrap;display:flex;gap:6px;align-items:center;backdrop-filter:blur(6px)}
.lab .no{width:18px;height:18px;font-size:10px}
body.clean #hud,body.clean #bom,body.clean #bar{display:none}
@media (max-width:760px){#bom{display:none}#hud{max-width:90vw}#bar input[type=range]{width:110px}}
</style></head><body class="__THEME__">
<div id="labels"><svg></svg></div>
<div id="hud"><h1>__TITLE__</h1><div class="sub" id="sub"></div><div class="chips" id="chips"></div></div>
<div id="bom"><h2>零件清单</h2><div id="rows"></div></div>
<div id="bar"><label>爆炸</label><input id="ex" type="range" min="0" max="1" step="0.001" value="0">
<button id="run">运转</button><button id="sec">剖切</button><button id="edg" class="on">边线</button><button id="lbl">标签</button><button id="cam">视角</button></div>
<script>__BUNDLE__</script>
<script>
const A=__DATA__, SUB=__SUBTITLE__;
const MAT={steel:[0xb9bec4,1,.32],stainless:[0xcfd2d6,1,.24],aluminum:[0xd6dadf,1,.36],aluminum_black:[0x2c2f34,.7,.42],
aluminum_blue:[0x2f5d8a,.8,.36],titanium:[0x9d9c97,1,.4],brass:[0xc8a14b,1,.3],copper:[0xb4693e,1,.34],magnet:[0x70757d,.9,.34],
electrical_steel:[0x7f8791,.9,.5],rubber:[0x1e1e20,0,.85],plastic:[0xe9e5dd,0,.5],pcb:[0x1f6b46,0,.4]};
const b64=(s,T)=>{const bin=atob(s),u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return new T(u.buffer)};
const W=()=>innerWidth,H=()=>innerHeight;
const R=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
R.setPixelRatio(Math.min(devicePixelRatio,2));R.setSize(W(),H());R.toneMapping=THREE.ACESFilmicToneMapping;R.toneMappingExposure=1.05;
R.outputColorSpace=THREE.SRGBColorSpace;R.shadowMap.enabled=true;R.shadowMap.type=THREE.PCFSoftShadowMap;R.localClippingEnabled=true;
document.body.prepend(R.domElement);
const S=new THREE.Scene();const pm=new THREE.PMREMGenerator(R);S.environment=pm.fromScene(new RoomEnvironment(),0.04).texture;
const bmin=new THREE.Vector3(...A.bbox.min),bmax=new THREE.Vector3(...A.bbox.max),C=bmin.clone().add(bmax).multiplyScalar(.5);
let ext=new THREE.Box3(bmin.clone(),bmax.clone());A.parts.forEach(p=>{const e=new THREE.Vector3(...p.explode);ext.expandByPoint(bmin.clone().add(e));ext.expandByPoint(bmax.clone().add(e));});
const RAD=ext.getSize(new THREE.Vector3()).length()/2, RAD0=bmax.distanceTo(bmin)/2, CE=ext.getCenter(new THREE.Vector3());
const tgt=()=>C.clone().lerp(CE,ease(st.explode));
const cam=new THREE.PerspectiveCamera(30,W()/H(),RAD/100,RAD*60);cam.up.set(0,0,1);
const ctl=new OrbitControls(cam,R.domElement);ctl.enableDamping=true;ctl.target.copy(C);
const key=new THREE.DirectionalLight(0xffffff,2.2);key.castShadow=true;key.shadow.mapSize.set(2048,2048);key.shadow.bias=-0.0004;
const sc=key.shadow.camera;sc.left=sc.bottom=-RAD*1.6;sc.right=sc.top=RAD*1.6;sc.near=RAD*.1;sc.far=RAD*12;
key.position.copy(C).add(new THREE.Vector3(-RAD*.5,-RAD*.8,RAD*4.5));key.target.position.copy(C);S.add(key,key.target);
const rim=new THREE.DirectionalLight(0xfff1e0,1.1);rim.position.copy(C).add(new THREE.Vector3(RAD*3,RAD*2,RAD*1.2));S.add(rim);
S.add(new THREE.HemisphereLight(0xffffff,0x8a8378,.35));
const ground=new THREE.Mesh(new THREE.PlaneGeometry(RAD*20,RAD*20),new THREE.ShadowMaterial({opacity:.16}));
ground.receiveShadow=true;S.add(ground);
const clip=new THREE.Plane(new THREE.Vector3(0,1,0),-C.y);const clips=[];
const ACC=new THREE.Color(0xd4572f);
const parts=[];const rows=document.getElementById('rows');
A.parts.forEach((p,k)=>{let g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(b64(p.mesh.v,Float32Array),3));
 g.setIndex(new THREE.BufferAttribute(b64(p.mesh.i,Uint32Array),1));g=toCreasedNormals(g,Math.PI/7);
 const mm=MAT[p.material]||MAT.steel;const col=p.color?new THREE.Color(p.color):new THREE.Color(mm[0]);
 const mat=new THREE.MeshPhysicalMaterial({color:col,metalness:mm[1],roughness:mm[2],clearcoat:p.material.startsWith('aluminum_')?.35:0,clippingPlanes:clips,envMapIntensity:1.1});
 const grp=new THREE.Group();grp.matrixAutoUpdate=false;const mesh=new THREE.Mesh(g,mat);mesh.castShadow=mesh.receiveShadow=true;
 const capMat=new THREE.MeshBasicMaterial({color:col.clone().lerp(ACC,.55).offsetHSL(0,0,(k%3-1)*.06),side:THREE.BackSide,clippingPlanes:clips});const cap=new THREE.Mesh(g,capMat);const edges=new THREE.LineSegments(new THREE.EdgesGeometry(g,28),new THREE.LineBasicMaterial({color:0x121314,transparent:true,opacity:.22,clippingPlanes:clips}));
 grp.add(mesh,cap,edges);S.add(grp);
 const it={p,grp,mesh,mat,edges,cap,c0:new THREE.Vector3(...p.spin_center),axis:new THREE.Vector3(...p.spin_axis).normalize(),oc:new THREE.Vector3(...p.motion.orbit_center),ex:new THREE.Vector3(...p.explode),base:col.clone()};
 parts.push(it);
 const r=document.createElement('div');r.className='row';r.innerHTML=`<span class="no">${k+1}</span><span>${p.name}${p.qty>1?' ×'+p.qty:''}<small>${p.material_name}${p.gear?` · m${p.gear.module} z${p.gear.teeth}`:''}</small></span><span class="m">${p.mass_g>=1000?(p.mass_g/1000).toFixed(2)+' kg':p.mass_g.toFixed(1)+' g'}</span>`;
 r.onmouseenter=()=>hi(k);r.onmouseleave=()=>hi(-1);rows.appendChild(r);it.row=r;});
function hi(k){parts.forEach((it,j)=>{it.mat.emissive.set(j===k?0x5a2010:0x000000);it.mat.transparent=k>=0&&j!==k;it.mat.opacity=k>=0&&j!==k?.25:1;it.row.classList.toggle('on',j===k)});}
// HUD
const pl=A.planetary;const nInter=0;
document.getElementById('sub').textContent=SUB||`${A.parts.length} 个零件 · 总质量 ${A.mass_total_g>=1000?(A.mass_total_g/1000).toFixed(2)+' kg':A.mass_total_g+' g'}`;
const chips=[];if(pl&&pl.ratio)chips.push(`传动比 <b>${+pl.ratio.toFixed(3)}:1</b>`);
chips.push(`<span class="${A.checks_ok?'ok':'bad'}">${A.checks_ok?'✓ 装配验证通过':'✗ 装配验证未通过'}</span>`);
chips.push(`干涉检查 · 啮合中心距 · STEP 读回`);
document.getElementById('chips').innerHTML=chips.map(c=>`<span class="chip">${c}</span>`).join('');
// state
const st={explode:0,t:0,section:false,edges:true,labels:'auto',run:false};
const M4=new THREE.Matrix4(),R1=new THREE.Matrix4(),T1=new THREE.Matrix4(),T2=new THREE.Matrix4(),V=new THREE.Vector3();
const ease=x=>x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2;
function pose(){const e=ease(st.explode);let zmin=Infinity;
 parts.forEach(it=>{const m=it.p.motion,TAU=Math.PI*2;
  const ao=m.orbit*st.t*TAU,as=m.spin*st.t*TAU;
  V.copy(it.c0).sub(it.oc).applyAxisAngle(it.axis,ao).add(it.oc).addScaledVector(it.ex,e);
  T1.makeTranslation(V.x,V.y,V.z);R1.makeRotationAxis(it.axis,as);T2.makeTranslation(-it.c0.x,-it.c0.y,-it.c0.z);
  it.grp.matrix.copy(T1).multiply(R1).multiply(T2);it.grp.matrixWorldNeedsUpdate=true;
  zmin=Math.min(zmin,bmin.z+it.ex.z*e);});
 ground.position.set(C.x,C.y,zmin-RAD0*.02);
 clips.length=0;if(st.section)clips.push(clip);parts.forEach(it=>{it.edges.visible=st.edges;it.mesh.material.needsUpdate=true;it.cap.material.needsUpdate=true;});}
let az=-38,el=24,dist=RAD*3.1,autoD=true;
const autoDist=()=>THREE.MathUtils.lerp(RAD0*3.3,RAD*2.9,ease(st.explode));
function setCam(a,e,d){az=a;el=e;autoD=!d;dist=d||autoDist();const ra=az*Math.PI/180,re=el*Math.PI/180;
 cam.position.set(C.x+dist*Math.cos(re)*Math.cos(ra),C.y+dist*Math.cos(re)*Math.sin(ra),C.z+dist*Math.sin(re));ctl.target.copy(C);cam.lookAt(C);ctl.update();}
setCam(az,el);
ctl.addEventListener('start',()=>{ctl.__user=true;});
const labs=document.getElementById('labels'),svg=labs.querySelector('svg');const labEls=parts.map((it,k)=>{const d=document.createElement('div');d.className='lab';d.innerHTML=`<span class="no">${k+1}</span>${it.p.name}`;labs.appendChild(d);return d;});
function drawLabels(){const show=st.labels===true||(st.labels==='auto'&&st.explode>.35);svg.innerHTML='';const w=W(),h=H();const cs=new THREE.Vector3(...C.toArray()).project(cam);
 parts.forEach((it,k)=>{const d=labEls[k];if(!show){d.style.display='none';return}
  const p=it.c0.clone().applyMatrix4(it.grp.matrix).project(cam);const x=(p.x*.5+.5)*w,y=(-p.y*.5+.5)*h;
  let dx=x-(cs.x*.5+.5)*w,dy=y-(-cs.y*.5+.5)*h;const L=Math.hypot(dx,dy)||1;const off=Math.min(w,h)*.11;
  const lx=x+dx/L*off,ly=y+dy/L*off*.7;d.style.display='flex';d.style.left=lx+'px';d.style.top=ly+'px';
  svg.insertAdjacentHTML('beforeend',`<line x1="${x}" y1="${y}" x2="${lx}" y2="${ly}" stroke="currentColor" stroke-opacity=".45" stroke-width="1"/><circle cx="${x}" cy="${y}" r="2.6" fill="currentColor" fill-opacity=".7"/>`);});}
function render(){if(autoD&&!ctl.__user){const ra=az*Math.PI/180,re=el*Math.PI/180,d=autoDist(),T=tgt();cam.position.set(T.x+d*Math.cos(re)*Math.cos(ra),T.y+d*Math.cos(re)*Math.sin(ra),T.z+d*Math.sin(re));cam.lookAt(T);ctl.target.copy(T);}pose();R.render(S,cam);drawLabels();}
// UI
const ex=document.getElementById('ex');ex.oninput=()=>{st.explode=+ex.value;};
const tg=(id,k)=>{const b=document.getElementById(id);b.onclick=()=>{st[k]=!st[k];b.classList.toggle('on',st[k]);}};
tg('run','run');tg('sec','section');tg('edg','edges');document.getElementById('lbl').onclick=()=>{st.labels=st.labels===true?false:true;document.getElementById('lbl').classList.toggle('on',st.labels===true);};
const views=[[-38,24],[-90,0],[-90,89.5],[30,35]];let vi=0;document.getElementById('cam').onclick=()=>{vi=(vi+1)%views.length;ctl.__user=false;setCam(...views[vi]);};
addEventListener('resize',()=>{cam.aspect=W()/H();cam.updateProjectionMatrix();R.setSize(W(),H());});
let last=performance.now();const auto=!/[?&]static/.test(location.search);
function loop(now){const dt=Math.min(.05,(now-last)/1000);last=now;if(st.run)st.t+=dt*.5;ctl.update();render();if(auto)requestAnimationFrame(loop);}
if(auto)requestAnimationFrame(loop);
// 无头驱动接口
window.CAD={set(o){Object.assign(st,o);if(o.cam)setCam(...o.cam);if('clean' in o)document.body.classList.toggle('clean',o.clean);if('dark' in o)document.body.classList.toggle('dark',o.dark);
 ex.value=st.explode;['run','sec','edg','lbl'].forEach((id,i)=>document.getElementById(id).classList.toggle('on',st[['run','section','edges','labels'][i]]===true));render();return true;},
 render(){render();return true;},radius:RAD,ready:true};
render();
</script></body></html>"""


def build(asm_json, out=None, theme="light", subtitle=""):
    data = Path(asm_json).read_text()
    title = json.loads(data)["title"]
    html = (HTML.replace("__BUNDLE__", BUNDLE.read_text())
            .replace("__DATA__", data).replace("__TITLE__", title)
            .replace("__THEME__", "dark" if theme == "dark" else "")
            .replace("__SUBTITLE__", json.dumps(subtitle, ensure_ascii=False)))
    out = Path(out) if out else Path(asm_json).with_name(f"{title}-交互爆炸图.html")
    out.write_text(html)
    print(out, f"{out.stat().st_size / 1e6:.1f} MB")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("assembly_json")
    ap.add_argument("--out")
    ap.add_argument("--theme", default="light")
    ap.add_argument("--subtitle", default="")
    a = ap.parse_args()
    build(a.assembly_json, a.out, a.theme, a.subtitle)

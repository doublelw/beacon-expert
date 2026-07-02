"""Beacon专家 - 前端SPA (对标企业知识库wiki风格).
顶栏(工作台选择) | 左栏(目录) | 中栏(内容) | 右栏(Beacon AI+转换)."""

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Beacon专家</title>
<style>
:root{
  --bg:#ffffff;--bg2:#f8f9fb;--bg3:#f0f1f3;
  --surface:#ffffff;--surface2:#f8f9fb;
  --border:#e5e7eb;--border2:#d0d5dd;
  --text:#111827;--text2:#4b5563;--text3:#9ca3af;
  --accent:#6366f1;--accent-light:#eef2ff;--accent-bg:#e0e7ff;
  --wiki:#7c3aed;--wiki-light:#f5f3ff;
  --green:#059669;--green-bg:#dcfce7;
  --amber:#d97706;--amber-bg:#fef3c7;
  --red:#dc2626;
  --radius:8px;--radius-sm:6px;--radius-lg:12px;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 6px -1px rgba(0,0,0,.07),0 2px 4px -2px rgba(0,0,0,.05);
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:"SF Mono",SFMono-Regular,Menlo,monospace;
  --sidebar-w:240px;--right-w:380px;--topbar-h:48px;
  --transition:all .2s cubic-bezier(.4,0,.2,1);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font);background:var(--bg);color:var(--text);display:flex;flex-direction:column;overflow:hidden;-webkit-font-smoothing:antialiased}

/* === 登录 === */
#login-view{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--bg2)}
.login-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:40px;width:360px;box-shadow:var(--shadow-md)}
.login-card h1{font-size:20px;text-align:center;margin-bottom:4px;color:var(--text)}
.login-card .sub{text-align:center;color:var(--text3);margin-bottom:28px;font-size:13px}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;color:var(--text3);margin-bottom:6px}
.field input{width:100%;padding:9px 12px;border:1px solid var(--border2);border-radius:var(--radius-sm);font-size:14px;background:var(--bg2)}
.field input:focus{border-color:var(--accent);outline:none;box-shadow:0 0 0 3px var(--accent-light)}
.btn{padding:9px 16px;border-radius:var(--radius-sm);font-size:14px;cursor:pointer;border:none;transition:var(--transition)}
.btn-primary{background:var(--accent);color:#fff;width:100%}
.btn-primary:hover{background:#5558e0}
.err{color:var(--red);font-size:13px;text-align:center;margin-top:8px;min-height:18px}

/* === 顶栏 === */
#topbar{height:var(--topbar-h);background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 16px;flex-shrink:0;z-index:10}
.topbar-left{display:flex;align-items:center;gap:12px}
.topbar-logo{font-size:16px;font-weight:700;color:var(--text)}
.workspace-select{padding:4px 8px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;color:var(--text2);background:var(--bg2);cursor:pointer}
.topbar-right{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text2)}
.role-tag{font-size:11px;padding:1px 6px;border-radius:3px;background:var(--accent-light);color:var(--accent)}
.logout-link{color:var(--text3);cursor:pointer}
.logout-link:hover{color:var(--text)}

/* === 三栏 === */
#main{display:flex;flex:1;overflow:hidden}

/* 左栏 - 目录 */
#left{width:var(--sidebar-w);min-width:160px;max-width:400px;border-right:1px solid var(--border);background:var(--bg);overflow-y:auto;flex-shrink:0}
.sidebar-section{padding:12px 16px 4px;font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.tree-item{padding:6px 16px;cursor:pointer;font-size:14px;color:var(--text2);transition:var(--transition);display:flex;align-items:center;gap:6px}
.tree-item:hover{background:var(--bg2);color:var(--text)}
.tree-item.active{background:var(--accent-light);color:var(--accent);font-weight:500}
.tree-icon{font-size:14px;width:16px;text-align:center;opacity:.7}
.tree-children{margin-left:8px}
.tree-child{padding:5px 16px 5px 28px;cursor:pointer;font-size:13px;color:var(--text3);transition:var(--transition)}
.tree-child:hover{color:var(--text);background:var(--bg2)}
.tree-child.active{color:var(--accent)}

/* 中栏 - 内容 */
#content{flex:1;overflow-y:auto;padding:24px 32px;background:var(--bg)}
.content-header{font-size:22px;font-weight:700;margin-bottom:4px}
.content-meta{color:var(--text3);font-size:13px;margin-bottom:20px}
.content-body{font-size:15px;line-height:1.7;color:var(--text)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:12px}
.card-title{font-size:15px;font-weight:600;margin-bottom:8px}
.card-body{font-size:14px;color:var(--text2);line-height:1.6}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:3px;margin-right:4px}
.tag-blue{background:var(--accent-light);color:var(--accent)}
.tag-green{background:var(--green-bg);color:var(--green)}
.tag-amber{background:var(--amber-bg);color:var(--amber)}

/* 右栏 - Beacon AI */
#right{width:var(--right-w);border-left:1px solid var(--border);background:var(--surface);display:flex;flex-direction:column;flex-shrink:0}
.ai-header{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.ai-title{font-size:15px;font-weight:600;display:flex;align-items:center;gap:6px}
.ai-status{font-size:11px;padding:2px 8px;border-radius:4px;background:var(--green-bg);color:var(--green)}
/* 阶段进度 */
.stage-bar{display:flex;padding:6px 10px;gap:2px;border-bottom:1px solid var(--border)}
.stage-node{flex:1;text-align:center;font-size:10px;padding:3px 1px;color:var(--text3);border-bottom:2px solid transparent;transition:var(--transition)}
.stage-node.done{color:var(--green);border-color:var(--green)}
.stage-node.active{color:var(--accent);border-color:var(--accent);font-weight:600}
/* 上传区 */
.ai-upload{padding:16px;border-bottom:1px solid var(--border)}
.ai-dropzone{padding:20px;border:1.5px dashed var(--border2);border-radius:var(--radius);text-align:center;cursor:pointer;transition:var(--transition)}
.ai-dropzone:hover,.ai-dropzone.dragover{border-color:var(--accent);background:var(--accent-light)}
.ai-dropzone .dz-icon{font-size:28px;margin-bottom:4px}
.ai-dropzone .dz-text{font-size:13px;color:var(--text2)}
.ai-dropzone .dz-hint{font-size:11px;color:var(--text3);margin-top:4px}
.file-input{display:none}
/* 对话消息 */
.ai-messages{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
.msg{max-width:92%;padding:8px 12px;border-radius:8px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg.ai{background:var(--bg2);align-self:flex-start;border-bottom-left-radius:2px;color:var(--text)}
.msg.user{background:var(--accent);color:#fff;align-self:flex-end;border-bottom-right-radius:2px}
.msg.sys{background:var(--amber-bg);align-self:center;font-size:11px;color:#856404;border-radius:4px;padding:3px 10px}
.msg-actions{margin-top:6px;display:flex;gap:6px}
.act-btn{padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid var(--border2);background:var(--surface);color:var(--text2);transition:var(--transition)}
.act-btn.green{color:var(--green);border-color:var(--green)}
.act-btn.amber{color:var(--amber);border-color:var(--amber)}
.act-btn.blue{color:var(--accent);border-color:var(--accent);text-decoration:none;display:inline-block}
/* 输入栏 */
.ai-input{padding:8px 12px;border-top:1px solid var(--border);display:flex;gap:8px}
.ai-input textarea{flex:1;padding:7px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:13px;resize:none;max-height:70px;font-family:var(--font)}
.ai-input textarea:focus{border-color:var(--accent);outline:none}
.ai-input .send-btn{padding:7px 12px;background:var(--accent);color:#fff;border:none;border-radius:var(--radius-sm);cursor:pointer;font-size:13px;transition:var(--transition)}
.ai-input .send-btn:hover{background:#5558e0}
/* === 拖拽分隔条 === */
.resizer{width:5px;cursor:col-resize;background:transparent;flex-shrink:0;position:relative;z-index:5;transition:background .15s}
.resizer:hover,.resizer.dragging{background:var(--accent);opacity:.4}
</style>
</head>
<body>

<!-- === 登录 === -->
<div id="login-view">
<div class="login-card">
<h1>Beacon专家</h1>
<div class="sub">AI驱动 3D→2D 工程图转换平台</div>
<div class="field"><label>用户名</label><input id="email" value="admin" onkeydown="if(event.key==='Enter')doAuth()"></div>
<div class="field"><label>密码</label><input id="password" type="password" value="123456" onkeydown="if(event.key==='Enter')doAuth()"></div>
<button class="btn btn-primary" onclick="doAuth()">登录</button>
<div class="err" id="auth-err"></div>
</div>
</div>

<!-- === 主应用 === -->
<header id="topbar">
<div class="topbar-left">
<span class="topbar-logo">📐 Beacon专家</span>
<select class="workspace-select" onchange="switchWorkspace(this.value)">
<option value="personal">个人空间</option>
<option value="department">部门空间</option>
<option value="enterprise">企业空间</option>
</select>
</div>
<div class="topbar-right">
<span id="user-name"></span>
<span class="role-tag" id="user-role"></span>
<span class="logout-link" onclick="logout()">退出</span>
</div>
</header>

<div id="main">
<!-- 左栏: 目录 -->
<aside id="left">
<div class="sidebar-section">知识目录</div>
<div class="tree-item active" onclick="showPage('overview','零件总览')"><span class="tree-icon">📋</span> 零件总览</div>
<div class="tree-item" onclick="showPage('knowledge','知识库')"><span class="tree-icon">📚</span> 知识库</div>
<div class="tree-item" onclick="showPage('drawings','图纸库')"><span class="tree-icon">📊</span> 图纸库</div>
<div class="tree-item" onclick="showPage('components','零部件库')"><span class="tree-icon">⚙</span> 零部件库</div>
<div class="sidebar-section">系统</div>
<div class="tree-item" onclick="showPage('settings','模型配置')"><span class="tree-icon">🔧</span> 模型配置</div>
<div class="tree-item" onclick="showPage('memory','AI记忆')"><span class="tree-icon">🧠</span> AI记忆</div>
</aside>
<div class="resizer" id="resizer-left"></div>

<!-- 中栏: 内容 -->
<main id="content">
<div class="content-header">零件总览</div>
<div class="content-meta">转换历史 · 知识库 · 图纸管理</div>
<div class="content-body" id="content-body">
<div class="card">
<div class="card-title">📊 转换统计</div>
<div class="card-body" id="stats-body">加载中...</div>
</div>
<div class="card">
<div class="card-title">💡 使用说明</div>
<div class="card-body">
1. 在<span style="color:var(--accent)">右侧Beacon AI面板</span>上传STP文件<br>
2. AI自动判断工艺类型，您可以确认或纠正<br>
3. AI理解零件特征，生成标注方案<br>
4. 确认后自动转换，AI审查加工就绪度<br>
5. 下载DXF工程图
</div>
</div>
</div>
</main>
<div class="resizer" id="resizer-right"></div>

<!-- 右栏: Beacon AI + 转换 -->
<aside id="right">
<div class="ai-header">
<span class="ai-title">🤖 Beacon AI</span>
<span class="ai-status" id="ai-status">就绪</span>
</div>
<div class="stage-bar" id="stage-bar"></div>
<div class="ai-upload" id="ai-upload">
<div class="ai-dropzone" id="ai-dropzone" onclick="document.getElementById('ai-file').click()">
<div class="dz-icon">📁</div>
<div class="dz-text">上传STP文件开启AI对话</div>
<div class="dz-hint">拖拽或点击 · .stp/.step · 最大50MB</div>
<input type="file" id="ai-file" class="file-input" accept=".stp,.step">
</div>
</div>
<div class="ai-messages" id="ai-messages"></div>
<div class="ai-input">
<textarea id="ai-input" rows="1" placeholder="输入消息 (Enter发送)" onkeydown="handleKey(event)" oninput="autoGrow(this)"></textarea>
<button class="send-btn" onclick="sendMsg()">发送</button>
</div>
</aside>
</div>

<script>
const API='';
let TOKEN=localStorage.getItem('beacon_token')||'';
let USER=JSON.parse(localStorage.getItem('beacon_user')||'null');
let convId=null,stage='init',poll=null;
const STAGES=['init','classify','understand','plan','convert','audit','done'];
const SLABEL={init:'上传',classify:'分类',understand:'理解',plan:'规划',convert:'转换',audit:'审计',done:'完成'};

async function api(p,o={}){
  const h={...(o.headers||{})};
  if(TOKEN)h['Authorization']='Bearer '+TOKEN;
  if(o.body&&!(o.body instanceof FormData))h['Content-Type']='application/json';
  const r=await fetch(API+p,{...o,headers:h});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){const det=Array.isArray(d.detail)?d.detail.map(x=>x.msg||JSON.stringify(x)).join('; '):(d.detail||'HTTP '+r.status);throw new Error(det);}
  return d;
}

// === 登录 ===
async function doAuth(){
  const e=document.getElementById('email').value.trim(),p=document.getElementById('password').value;
  const err=document.getElementById('auth-err');err.textContent='';
  if(!e||!p){err.textContent='请填写';return}
  try{
    const d=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email:e,password:p})});
    TOKEN=d.token;USER={id:d.user_id,role:d.role,username:d.username||e};
    localStorage.setItem('beacon_token',TOKEN);
    localStorage.setItem('beacon_user',JSON.stringify(USER));
    enterApp();
  }catch(ex){err.textContent=ex.message}
}
function logout(){localStorage.removeItem('beacon_token');localStorage.removeItem('beacon_user');location.reload()}
function enterApp(){
  document.getElementById('login-view').style.display='none';
  document.querySelector('header').style.display='flex';
  document.getElementById('main').style.display='flex';
  document.getElementById('user-name').textContent=USER.username||USER.email;
  document.getElementById('user-role').textContent=USER.role;
  renderStageBar();
  loadStats();
  setupUpload();
}
function switchWorkspace(ws){loadStats()}

// === 左栏导航 ===
function showPage(view,title){
  document.querySelectorAll('.tree-item').forEach(n=>n.classList.toggle('active',n.onclick&&n.onclick.toString().includes(view)));
  document.querySelector('.content-header').textContent=title;
  const body=document.getElementById('content-body');
  if(view==='overview')loadStats();
  else if(view==='knowledge')loadKn(body);
  else if(view==='drawings')loadDr(body);
  else if(view==='components')loadCp(body);
  else if(view==='settings')loadSet(body);
  else if(view==='memory')loadMem(body);
}

async function loadStats(){
  const body=document.getElementById('content-body');
  body.innerHTML='<div class="card"><div class="card-title">📊 转换统计</div><div class="card-body">加载中...</div></div>';
  try{
    const h=await api('/api/health');
    body.innerHTML=`
    <div class="card"><div class="card-title">📊 系统状态</div><div class="card-body">
    FreeCAD: <span class="tag tag-green">${h.freecadcmd.available?'✓可用':'✗不可用'}</span>
    磁盘: ${h.disk_free_mb}MB空闲</div></div>
    <div class="card"><div class="card-title">💡 使用说明</div><div class="card-body">
    1. 在<span style="color:var(--accent)">右侧Beacon AI面板</span>上传STP文件<br>
    2. AI自动判断工艺类型，可确认或纠正<br>
    3. 确认后自动转换+审查<br>
    4. 下载DXF工程图</div></div>`;
  }catch(ex){body.innerHTML='<div class="card">'+ex.message+'</div>'}
}
async function loadKn(b){
  b.innerHTML='加载中...';
  try{const d=await api('/api/knowledge/list');
    b.innerHTML=(d.items||[]).map(k=>`<div class="card"><div class="card-title">${k.title}</div><div class="card-body"><span class="tag tag-${k.scope==='enterprise'?'green':'blue'}">${k.scope}</span></div></div>`).join('')||'<div class="card-body">暂无知识条目</div>'
  }catch(ex){b.innerHTML='<div class="card">'+ex.message+'</div>'}
}
async function loadDr(b){
  b.innerHTML='加载中...';
  try{const d=await api('/api/drawings/');
    b.innerHTML=`<div class="card"><div class="card-title">图纸库 (${d.total||0}件)</div></div>`+((d.items||[]).map(dr=>`<div class="card"><div class="card-title">${dr.name}</div><div class="card-body"><span class="tag tag-blue">${dr.process||'-'}</span></div></div>`).join(''))||'<div class="card-body">暂无图纸</div>'
  }catch(ex){b.innerHTML='<div class="card">'+ex.message+'</div>'}
}
async function loadCp(b){b.innerHTML='<div class="card"><div class="card-title">零部件库</div><div class="card-body">标准件(压铆BSO/沉头M4/过孔) + 自定义件</div></div>'}
async function loadSet(b){
  b.innerHTML='加载中...';
  try{const d=await api('/api/settings/llm');
    b.innerHTML=`<div class="card"><div class="card-title">模型配置</div>
    <div class="card-body">Provider: <strong>${d.provider||'-'}</strong><br>Model: <strong>${d.model||'-'}</strong><br>Base URL: ${d.base_url||'-'}<br>API Key: ${d.has_api_key?'✓已设置':'✗未设置'}</div></div>`
  }catch(ex){b.innerHTML='<div class="card">'+ex.message+'</div>'}
}
async function loadMem(b){
  b.innerHTML='加载中...';
  try{const d=await api('/api/memory/');
    b.innerHTML=`<div class="card"><div class="card-title">AI记忆 (${(d.items||d.total||0)}条)</div></div>`+((d.items||[]).map(m=>`<div class="card"><div class="card-title">${m.mem_type}</div><div class="card-body">${JSON.stringify(m.content).slice(0,100)}</div></div>`).join(''))||'<div class="card-body">暂无记忆</div>'
  }catch(ex){b.innerHTML='<div class="card">'+ex.message+'</div>'}
}

// === 右栏: AI对话 ===
function setupUpload(){
  const dz=document.getElementById('ai-dropzone');
  const fi=document.getElementById('ai-file');
  ['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('dragover')}));
  ['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('dragover')}));
  dz.addEventListener('drop',e=>{if(e.dataTransfer.files.length)startChat(e.dataTransfer.files[0])});
  fi.addEventListener('change',e=>{if(e.target.files.length)startChat(e.target.files[0])});
}
async function startChat(file){
  addMsg('ai','📁 接收: '+file.name+'\n分析中...');
  document.getElementById('ai-upload').style.display='none';
  const fd=new FormData();fd.append('file',file);
  try{
    const d=await api('/api/chat/start',{method:'POST',body:fd});
    convId=d.conversation_id;stage='classify';
    addMsg('ai',d.message);renderStageBar();
  }catch(ex){addMsg('ai','❌ '+ex.message);document.getElementById('ai-upload').style.display='block'}
}
function addMsg(role,content){
  const msgs=document.getElementById('ai-messages');
  const div=document.createElement('div');div.className='msg '+role;div.textContent=content;
  if(role==='ai'){
    const text=content.toLowerCase();
    if(stage==='classify'&&content.includes('工艺')){
      const a=document.createElement('div');a.className='msg-actions';
      const ok=document.createElement('button');ok.className='act-btn green';ok.textContent='确认';ok.onclick=()=>confirmStage();
      const no=document.createElement('button');no.className='act-btn amber';no.textContent='纠正';no.onclick=()=>{const p=prompt('正确工艺:');if(p)correctStage(p)};
      a.append(ok,no);div.appendChild(a);
    }
    if((stage==='plan'||stage==='audit')&&content.includes('确认')){
      const a=document.createElement('div');a.className='msg-actions';
      const ok=document.createElement('button');ok.className='act-btn green';ok.textContent=stage==='plan'?'执行转换':'确认接受';ok.onclick=()=>confirmStage();
      a.append(ok);div.appendChild(a);
    }
    if(content.includes('DXF')&&content.includes('下载')){
      const a=document.createElement('div');a.className='msg-actions';
      const dl=document.createElement('a');dl.className='act-btn blue';dl.textContent='⬇ 下载DXF';
      dl.href=API+'/api/convert/download/'+convId+'?token='+TOKEN;dl.target='_blank';
      a.append(dl);div.appendChild(a);
    }
  }
  msgs.append(div);msgs.scrollTop=msgs.scrollHeight;
}
async function sendMsg(){
  const inp=document.getElementById('ai-input');const txt=inp.value.trim();
  if(!txt||!convId)return;inp.value='';
  addMsg('user',txt);
  try{const d=await api('/api/chat/'+convId+'/message',{method:'POST',body:JSON.stringify({text:txt})});
    stage=d.stage;if(d.message)addMsg('ai',d.message);renderStageBar();
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
async function confirmStage(){
  addMsg('user','确认');
  try{const d=await api('/api/chat/'+convId+'/confirm',{method:'POST',body:JSON.stringify({})});
    stage=d.stage;if(d.message)addMsg('ai',d.message);renderStageBar();
    if(stage==='convert')pollConvert();
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
async function correctStage(p){
  addMsg('user','纠正: '+p);
  try{const d=await api('/api/chat/'+convId+'/correct',{method:'POST',body:JSON.stringify({process:p})});
    addMsg('ai',d.message);
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
function pollConvert(){
  if(poll)clearInterval(poll);
  poll=setInterval(async()=>{
    try{
      const d=await api('/api/convert/status/'+convId);
      if(d.status==='done'&&d.dxf_ready){
        clearInterval(poll);poll=null;
        stage='done';renderStageBar();
        addMsg('ai','✅ 转换完成！点击下载DXF。');
      }
    }catch(ex){}
  },3000);
}
function handleKey(e){if(e.key==='Enter'&&!e.shiftY){e.preventDefault();sendMsg()}}
function autoGrow(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,70)+'px'}
function renderStageBar(){
  document.getElementById('stage-bar').innerHTML=STAGES.map(s=>
    `<div class="stage-node ${s===stage?'active':''} ${STAGES.indexOf(s)<STAGES.indexOf(stage)?'done':''}">${SLABEL[s]}</div>`
  ).join('');
}

// === 启动 ===
async function init(){
  if(TOKEN){try{await api('/api/auth/me');enterApp()}catch(e){localStorage.clear();TOKEN='';USER=null}}
  initResizers();
}
init();

// === 三栏拖拽调宽 + 记忆 ===
function initResizers(){
  const left=document.getElementById('left');
  const right=document.getElementById('right');
  const main=document.getElementById('main');
  // 恢复上次宽度
  const sw=localStorage.getItem('beacon_col_widths');
  if(sw){const w=JSON.parse(sw);if(w.left)left.style.width=w.left+'px';if(w.right)right.style.width=w.right+'px'}
  function makeResizer(resizer,target,side){
    let startX=0,startW=0;
    resizer.addEventListener('mousedown',e=>{
      e.preventDefault();startX=e.clientX;startW=target.offsetWidth;
      resizer.classList.add('dragging');document.body.style.cursor='col-resize';
      const onMove=ev=>{
        const dx=ev.clientX-startX;
        let newW=startW+(side==='left'?dx:-dx);
        newW=Math.max(140,Math.min(newW,Math.floor(window.innerWidth*0.5)));
        target.style.width=newW+'px';
      };
      const onUp=()=>{
        resizer.classList.remove('dragging');document.body.style.cursor='';
        document.removeEventListener('mousemove',onMove);
        document.removeEventListener('mouseup',onUUP);
        // 保存
        localStorage.setItem('beacon_col_widths',JSON.stringify({
          left:left.offsetWidth,right:right.offsetWidth
        }));
      };
      document.addEventListener('mousemove',onMove);
      document.addEventListener('mouseup',onUp);
    });
  }
  const rl=document.getElementById('resizer-left');
  const rr=document.getElementById('resizer-right');
  if(rl)makeResizer(rl,left,'left');
  if(rr)makeResizer(rr,right,'right');
}
</script>
</body></html>"""

"""Beacon专家 - 前端SPA (wiki式三栏 + 右栏AI对话常驻).
参考企业知识库serve.py风格: 左导航 | 中内容 | 右AI对话."""

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Beacon专家</title>
<style>
:root{
  --bg:#ffffff;--bg2:#f8f9fb;--panel:#ffffff;--panel2:#f0f2f5;
  --text:#1a1a2e;--muted:#6b7280;--border:#e5e7eb;
  --accent:#6366f1;--accent-hover:#5558e0;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;
  --radius:8px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg2);color:var(--text);font-size:14px}

/* === 登录 === */
#login-view{display:flex;align-items:center;justify-content:center;min-height:100vh;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)}
.login-card{background:#fff;border-radius:16px;padding:40px;width:380px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.login-card h1{font-size:24px;text-align:center;margin-bottom:4px;background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.login-card .sub{text-align:center;color:var(--muted);margin-bottom:28px}
.field{margin-bottom:16px}
.field label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
.field input{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px}
.field input:focus{border-color:var(--accent);outline:none;box-shadow:0 0 0 3px rgba(99,102,241,.1)}
.btn{padding:10px 16px;border-radius:8px;font-size:14px;cursor:pointer;border:none;transition:.15s}
.btn-primary{background:var(--accent);color:#fff;width:100%}
.btn-primary:hover{background:var(--accent-hover)}
.err{color:var(--red);font-size:13px;text-align:center;margin-top:8px;min-height:18px}

/* === 主布局(wiki式三栏) === */
#app-view{display:none;height:100vh;flex-direction:column}
.app-header{height:52px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);display:flex;align-items:center;justify-content:space-between;padding:0 20px;color:#fff}
.app-header .logo{font-size:18px;font-weight:700;display:flex;align-items:center;gap:8px}
.app-header .logo span.icon{font-size:22px}
.app-header .user-area{display:flex;align-items:center;gap:12px;font-size:13px}
.app-header .user-area .role{background:rgba(255,255,255,.2);padding:2px 8px;border-radius:4px;font-size:11px}
.app-header .user-area .logout{cursor:pointer;opacity:.8}
.app-header .user-area .logout:hover{opacity:1}

.app-body{flex:1;display:flex;overflow:hidden}

/* === 左栏(导航) === */
.sidebar{width:200px;background:var(--panel);border-right:1px solid var(--border);display:flex;flex-direction:column;padding:12px 0}
.sidebar .nav-section{padding:8px 16px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:8px}
.sidebar .nav-item{padding:10px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:14px;color:var(--text);transition:.1s;border-left:3px solid transparent}
.sidebar .nav-item:hover{background:var(--bg2)}
.sidebar .nav-item.active{background:rgba(99,102,241,.08);border-left-color:var(--accent);color:var(--accent);font-weight:600}
.sidebar .nav-item .nav-icon{font-size:16px;width:20px;text-align:center}

/* === 中栏(内容) === */
.main-content{flex:1;padding:24px;overflow-y:auto;background:var(--bg2)}
.section-title{font-size:20px;font-weight:600;margin-bottom:16px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:16px}
.card h3{font-size:16px;margin-bottom:12px}

/* === 右栏(AI对话常驻) === */
.chat-panel{width:360px;border-left:1px solid var(--border);background:var(--panel);display:flex;flex-direction:column}
.chat-header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:linear-gradient(135deg,rgba(99,102,241,.05),rgba(118,75,162,.05))}
.chat-header .ch-title{font-size:15px;font-weight:600;display:flex;align-items:center;gap:6px}
.chat-header .ch-status{font-size:11px;padding:2px 8px;border-radius:4px;background:var(--green);color:#fff}

/* 阶段进度条 */
.stage-bar{display:flex;padding:8px 12px;gap:2px;border-bottom:1px solid var(--border)}
.stage-node{flex:1;text-align:center;font-size:10px;padding:4px 2px;color:var(--muted);border-bottom:2px solid transparent}
.stage-node.done{color:var(--green);border-color:var(--green)}
.stage-node.active{color:var(--accent);border-color:var(--accent);font-weight:600}

/* 对话消息 */
.chat-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:90%;padding:8px 12px;border-radius:10px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg.ai{background:var(--bg2);align-self:flex-start;border-bottom-left-radius:2px}
.msg.user{background:var(--accent);color:#fff;align-self:flex-end;border-bottom-right-radius:2px}
.msg.system{background:#fff3cd;align-self:center;font-size:12px;color:#856404;border-radius:6px;padding:4px 10px}
.msg .msg-actions{margin-top:6px;display:flex;gap:6px}
.msg .act-btn{padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid var(--border);background:#fff}
.msg .act-btn.confirm{color:var(--green);border-color:var(--green)}
.msg .act-btn.correct{color:var(--yellow);border-color:var(--yellow)}
.msg .act-btn.download{color:var(--accent);border-color:var(--accent);text-decoration:none;display:inline-block}

/* 对话输入 */
.chat-input-bar{padding:8px 12px;border-top:1px solid var(--border);display:flex;gap:8px}
.chat-input-bar textarea{flex:1;padding:8px;border:1px solid var(--border);border-radius:8px;font-size:13px;resize:none;max-height:80px;font-family:inherit}
.chat-input-bar textarea:focus{border-color:var(--accent);outline:none}
.chat-input-bar .send-btn{padding:8px 14px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px}
.chat-input-bar .send-btn:hover{background:var(--accent-hover)}

/* 上传区 */
.chat-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;padding:20px;text-align:center;color:var(--muted)}
.chat-dropzone{margin-top:12px;padding:24px;border:2px dashed var(--border);border-radius:12px;cursor:pointer;transition:.15s;width:100%}
.chat-dropzone:hover,.chat-dropzone.dragover{border-color:var(--accent);background:rgba(99,102,241,.03)}
.file-input{display:none}

/* 转换面板 */
.dropzone{padding:40px;border:2px dashed var(--border);border-radius:12px;text-align:center;cursor:pointer;transition:.15s}
.dropzone:hover,.dropzone.dragover{border-color:var(--accent);background:rgba(99,102,241,.03)}
.dz-icon{font-size:40px;margin-bottom:8px}
.progress-steps{display:flex;gap:4px;margin:12px 0}
.progress-steps .step{flex:1;text-align:center;font-size:11px;padding:4px;color:var(--muted);border-bottom:2px solid var(--border)}
.progress-steps .step.done{color:var(--green);border-color:var(--green)}
.progress-steps .step.active{color:var(--accent);border-color:var(--accent)}
.progress-bar{height:4px;background:var(--bg2);border-radius:2px;overflow:hidden;margin:12px 0}
.progress-fill{height:100%;background:var(--accent);transition:width .5s;width:0%}
.download-btn{display:inline-block;margin-top:12px;padding:8px 20px;background:var(--green);color:#fff;border-radius:8px;text-decoration:none;font-size:14px}

/* 知识库 */
.kn-list{list-style:none}
.kn-item{padding:12px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.kn-item .kn-title{font-weight:500}
.kn-item .kn-scope{font-size:11px;padding:2px 6px;border-radius:4px}
.kn-scope.personal{background:#dbeafe;color:#1e40af}
.kn-scope.enterprise{background:#dcfce7;color:#166534}

/* 设置 */
.setting-row{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--border)}
.setting-row label{font-size:14px}
.setting-row select,.setting-row input{padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px}
.save-btn{margin-top:12px;padding:8px 20px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer}
</style>
</head>
<body>

<!-- === 登录 === -->
<div id="login-view">
<div class="login-card">
<h1>Beacon专家</h1>
<div class="sub">AI驱动 3D→2D 工程图转换</div>
<div class="field">
<label>用户名 / 邮箱</label>
<input id="email" value="admin" onkeydown="if(event.key==='Enter')doAuth()">
</div>
<div class="field">
<label>密码</label>
<input id="password" type="password" value="123456" onkeydown="if(event.key==='Enter')doAuth()">
</div>
<button class="btn btn-primary" onclick="doAuth()">登录</button>
<div class="err" id="auth-err"></div>
</div>
</div>

<!-- === 主应用(wiki式三栏) === -->
<div id="app-view">
<div class="app-header">
<div class="logo"><span class="icon">📐</span> Beacon专家</div>
<div class="user-area">
<span id="user-email"></span><span class="role" id="user-role"></span>
<span class="logout" onclick="logout()">退出</span>
</div>
</div>
<div class="app-body">
<!-- 左栏 -->
<div class="sidebar">
<div class="nav-section">工作台</div>
<div class="nav-item active" data-view="convert" onclick="switchView('convert')"><span class="nav-icon">⚙</span> 转换</div>
<div class="nav-item" data-view="knowledge" onclick="switchView('knowledge')"><span class="nav-icon">📚</span> 知识库</div>
<div class="nav-item" data-view="drawings" onclick="switchView('drawings')"><span class="nav-icon">📊</span> 图纸库</div>
<div class="nav-section">设置</div>
<div class="nav-item" data-view="settings" onclick="switchView('settings')"><span class="nav-icon">🔧</span> 模型配置</div>
</div>
<!-- 中栏 -->
<div class="main-content" id="main-content"></div>
<!-- 右栏:AI对话常驻 -->
<div class="chat-panel">
<div class="chat-header">
<span class="ch-title">🤖 Beacon AI</span>
<span class="ch-status" id="chat-status">就绪</span>
</div>
<div class="stage-bar" id="stage-bar"></div>
<div class="chat-messages" id="chat-messages">
<div class="chat-empty">
<div style="font-size:36px;margin-bottom:8px">📐</div>
<div style="font-size:14px;margin-bottom:4px">上传STP开启AI对话</div>
<div class="chat-dropzone" id="chat-dropzone" onclick="document.getElementById('chat-file-input').click()">
<div style="font-size:24px;margin-bottom:4px">📁</div>
<div style="font-size:13px">拖拽或点击上传</div>
<div style="font-size:11px;margin-top:4px;opacity:.6">.stp / .step</div>
<input type="file" id="chat-file-input" class="file-input" accept=".stp,.step">
</div>
</div>
</div>
<div class="chat-input-bar">
<textarea id="chat-input" rows="1" placeholder="输入消息 (Enter发送)" onkeydown="handleChatKey(event)" oninput="autoGrow(this)"></textarea>
<button class="send-btn" onclick="sendMessage()">发送</button>
</div>
</div>
</div><!-- app-body -->
</div><!-- app-view -->

<script>
const API='';
let TOKEN=localStorage.getItem('beacon_token')||'';
let USER=JSON.parse(localStorage.getItem('beacon_user')||'null');
let chatSessionId=null,chatStage='init',pollTimer=null;
const CHAT_STAGES=['init','classify','understand','plan','convert','audit','done'];
const STAGE_LABEL={init:'上传',classify:'分类',understand:'理解',plan:'规划',convert:'转换',audit:'审计',done:'完成'};

async function api(path,opts={}){
  const h={...(opts.headers||{})};
  if(TOKEN)h['Authorization']='Bearer '+TOKEN;
  if(opts.body&&!(opts.body instanceof FormData))h['Content-Type']='application/json';
  const r=await fetch(API+path,{...opts,headers:h});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){const det=Array.isArray(d.detail)?d.detail.map(x=>x.msg||JSON.stringify(x)).join('; '):(d.detail||'HTTP '+r.status);throw new Error(det);}
  return d;
}

// === 登录 ===
async function doAuth(){
  const e=document.getElementById('email').value.trim();
  const p=document.getElementById('password').value;
  const err=document.getElementById('auth-err');err.textContent='';
  if(!e||!p){err.textContent='请填写';return}
  try{
    const d=await api('/api/auth/login',{method:'POST',body:JSON.stringify({email:e,password:p})});
    TOKEN=d.token;USER={id:d.user_id,role:d.role,email:e,username:d.username||e};
    localStorage.setItem('beacon_token',TOKEN);
    localStorage.setItem('beacon_user',JSON.stringify(USER));
    enterApp();
  }catch(ex){err.textContent=ex.message}
}
function logout(){localStorage.removeItem('beacon_token');localStorage.removeItem('beacon_user');location.reload()}
function enterApp(){
  document.getElementById('login-view').style.display='none';
  document.getElementById('app-view').style.display='flex';
  document.getElementById('user-email').textContent=USER.username||USER.email;
  document.getElementById('user-role').textContent=USER.role;
  updateStageBar();
  switchView('convert');
  setupChatDropzone();
}

function switchView(view){
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.view===view));
  if(pollTimer){clearInterval(pollTimer);pollTimer=null}
  const el=document.getElementById('main-content');
  if(view==='convert')el.innerHTML=`
    <div class="section-title">STP → DXF 转换</div>
    <div class="card">
    <div class="dropzone" id="conv-dz" onclick="document.getElementById('conv-file').click()">
    <div class="dz-icon">📁</div><div>拖拽STP/STEP文件</div><div style="font-size:12px;color:var(--muted);margin-top:4px">或点击选择 · 最大50MB</div>
    <input type="file" id="conv-file" class="file-input" accept=".stp,.step"></div>
    <div id="conv-result"></div></div>`
  else if(view==='knowledge')loadKnowledge(el)
  else if(view==='drawings')loadDrawings(el)
  else if(view==='settings')loadSettings(el)
  if(view==='convert')setupConvDropzone()
}
function setupConvDropzone(){
  const dz=document.getElementById('conv-dz');if(!dz)return;
  const fi=document.getElementById('conv-file');
  ['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.style.borderColor='var(--accent)'}));
  ['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.style.borderColor=''}));
  dz.addEventListener('drop',e=>{if(e.dataTransfer.files.length)handleFile(e.dataTransfer.files[0])});
  fi.addEventListener('change',e=>{if(e.target.files.length)handleFile(e.target.files[0])});
}
async function handleFile(file){
  const fd=new FormData();fd.append('file',file);
  document.getElementById('conv-result').innerHTML='<div>上传中...</div>';
  try{
    const d=await api('/api/convert/upload',{method:'POST',body:fd});
    currentTaskId=d.task_id;
    document.getElementById('conv-result').innerHTML=`<div>任务: ${d.task_id.slice(0,8)}... 状态: ${d.status}</div>`;
    pollTask();
  }catch(ex){document.getElementById('conv-result').innerHTML=`<div style="color:red">${ex.message}</div>`}
}
function pollTask(){
  if(pollTimer)clearInterval(pollTimer);
  pollTimer=setInterval(async()=>{
    try{
      const d=await api('/api/convert/status/'+currentTaskId);
      const r=document.getElementById('conv-result');
      if(d.status==='done'&&d.dxf_ready){
        r.innerHTML=`<div style="color:var(--green);font-size:16px;margin-bottom:8px">✅ 转换完成</div>
        <a class="download-btn" href="${API}/api/convert/download/${currentTaskId}?token=${TOKEN}" target="_blank">下载DXF</a>`;
        clearInterval(pollTimer);pollTimer=null;
      }else if(d.status==='failed'){
        r.innerHTML=`<div style="color:red">失败: ${d.error||'未知错误'}</div>`;
        clearInterval(pollTimer);pollTimer=null;
      }else{
        r.innerHTML=`<div>状态: ${d.status}...</div>`;
      }
    }catch(ex){}
  },2000);
}

async function loadKnowledge(el){
  el.innerHTML='<div class="section-title">知识库</div><div class="card">加载中...</div>';
  try{
    const d=await api('/api/knowledge/list');
    el.innerHTML='<div class="section-title">知识库</div>'+
      d.items.map(k=>`<div class="kn-item"><span class="kn-title">${k.title}</span><span class="kn-scope ${k.scope}">${k.scope}</span></div>`).join('')||'<div class="card">暂无知识</div>';
  }catch(ex){el.innerHTML='<div class="section-title">知识库</div><div class="card">'+ex.message+'</div>'}
}
async function loadDrawings(el){
  el.innerHTML='<div class="section-title">图纸库</div><div class="card">加载中...</div>';
  try{
    const d=await api('/api/drawings/');
    el.innerHTML='<div class="section-title">图纸库 ('+d.total+'件)</div>'+
      (d.items||[]).map(dr=>`<div class="kn-item"><span class="kn-title">${dr.name}</span><span style="font-size:11px;color:var(--muted)">${dr.process||'-'}</span></div>`).join('')||'<div class="card">暂无图纸</div>';
  }catch(ex){el.innerHTML='<div class="section-title">图纸库</div><div class="card">'+ex.message+'</div>'}
}
async function loadSettings(el){
  el.innerHTML='<div class="section-title">模型配置</div><div class="card">加载中...</div>';
  try{
    const d=await api('/api/settings/llm');
    el.innerHTML=`<div class="section-title">模型配置</div>
    <div class="card">
    <div class="setting-row"><label>Provider</label><select id="set-provider"><option value="zhipu">智谱GLM</option><option value="anthropic">Claude</option><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="ollama">Ollama(本地)</option></select></div>
    <div class="setting-row"><label>模型名</label><input id="set-model" value="${d.model||'glm-4.5-air'}"></div>
    <div class="setting-row"><label>API Key</label><input id="set-key" type="password" placeholder="${d.has_api_key?'(已设置)':'输入Key'}"></div>
    <div class="setting-row"><label>Base URL</label><input id="set-url" value="${d.base_url||''}" style="width:200px"></div>
    <button class="save-btn" onclick="saveSettings()">保存</button>
    </div>`;
    if(d.provider)document.getElementById('set-provider').value=d.provider;
  }catch(ex){el.innerHTML='<div class="section-title">模型配置</div><div class="card">'+ex.message+'</div>'}
}
async function saveSettings(){
  try{
    const body={provider:document.getElementById('set-provider').value,model:document.getElementById('set-model').value,base_url:document.getElementById('set-url').value};
    const key=document.getElementById('set-key').value;if(key)body.api_key=key;
    await api('/api/settings/llm',{method:'POST',body:JSON.stringify(body)});
    alert('保存成功');
  }catch(ex){alert(ex.message)}
}

// === AI对话(右栏常驻) ===
function setupChatDropzone(){
  const dz=document.getElementById('chat-dropzone');if(!dz)return;
  const fi=document.getElementById('chat-file-input');
  ['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('dragover')}));
  ['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('dragover')}));
  dz.addEventListener('drop',e=>{if(e.dataTransfer.files.length)startChat(e.dataTransfer.files[0])});
  fi.addEventListener('change',e=>{if(e.target.files.length)startChat(e.target.files[0])});
}
async function startChat(file){
  addMsg('ai','📁 接收文件: '+file.name+'\n正在分析3D模型...');
  document.getElementById('chat-messages').querySelector('.chat-empty')?.remove();
  const fd=new FormData();fd.append('file',file);
  try{
    const d=await api('/api/chat/start',{method:'POST',body:fd});
    chatSessionId=d.conversation_id;chatStage='classify';
    addMsg('ai',d.message);
    updateStageBar();
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
function addMsg(role,content){
  const msgs=document.getElementById('chat-messages');
  const div=document.createElement('div');div.className='msg '+role;div.textContent=content;
  // classify阶段加确认/纠正按钮
  if(role==='ai'&&chatStage==='classify'&&content.includes('工艺')){
    const actions=document.createElement('div');actions.className='msg-actions';
    const ok=document.createElement('button');ok.className='act-btn confirm';ok.textContent='确认';ok.onclick=()=>confirmStage();
    const no=document.createElement('button');no.className='act-btn correct';no.textContent='纠正';no.onclick=()=>{const p=prompt('请输入正确工艺(sheet_metal/injection_molding/machining/stamping/casting/welding/additive):');if(p)correctStage(p)};
    actions.append(ok,no);div.appendChild(actions);
  }
  // plan阶段加确认
  if(role==='ai'&&chatStage==='plan'&&content.includes('确认')){
    const actions=document.createElement('div');actions.className='msg-actions';
    const ok=document.createElement('button');ok.className='act-btn confirm';ok.textContent='执行转换';ok.onclick=()=>confirmStage();
    actions.append(ok);div.appendChild(actions);
  }
  msgs.append(div);msgs.scrollTop=msgs.scrollHeight;
}
async function sendMessage(){
  const inp=document.getElementById('chat-input');const txt=inp.value.trim();if(!txt||!chatSessionId)return;
  inp.value='';addMsg('user',txt);
  try{
    const d=await api('/api/chat/'+chatSessionId+'/message',{method:'POST',body:JSON.stringify({text:txt})});
    chatStage=d.stage;addMsg('ai',d.message);updateStageBar();
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
async function confirmStage(){
  addMsg('user','确认');try{
    const d=await api('/api/chat/'+chatSessionId+'/confirm',{method:'POST',body:JSON.stringify({})});
    chatStage=d.stage;if(d.message)addMsg('ai',d.message);updateStageBar();
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
async function correctStage(process){
  addMsg('user','纠正: '+process);try{
    const d=await api('/api/chat/'+chatSessionId+'/correct',{method:'POST',body:JSON.stringify({process:process})});
    addMsg('ai',d.message);
  }catch(ex){addMsg('ai','❌ '+ex.message)}
}
function handleChatKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}}
function autoGrow(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,80)+'px'}
function updateStageBar(){
  const bar=document.getElementById('stage-bar');if(!bar)return;
  bar.innerHTML=CHAT_STAGES.map(s=>`<div class="stage-node ${s===chatStage?'active':''} ${(CHAT_STAGES.indexOf(s)<CHAT_STAGES.indexOf(chatStage))?'done':''}">${STAGE_LABEL[s]}</div>`).join('');
}

// === 启动 ===
async function checkTokenAndEnter(){
  if(TOKEN){try{await api('/api/auth/me');enterApp()}catch(e){localStorage.removeItem('beacon_token');localStorage.removeItem('beacon_user');TOKEN='';USER=null}}
}
checkTokenAndEnter();
</script>
</body></html>"""

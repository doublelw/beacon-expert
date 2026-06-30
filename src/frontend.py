"""Beacon专家 - 前端SPA(纯HTML+JS+CSS, 无框架).

路由: GET /app
功能: 登录 → 主页(导航+内容+转换面板) → 设置(LLM配置)
"""
from fastapi.responses import HTMLResponse

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Beacon专家 — 3D→2D工程图自动转换</title>
<style>
  :root {
    --bg: #0f1419; --panel: #1a1f2e; --panel-2: #242b3d;
    --border: #2d3548; --text: #e4e7eb; --muted: #8b94a7;
    --accent: #4f8cff; --accent-hover: #6a9eff;
    --success: #34d399; --warn: #fbbf24; --danger: #f87171;
    --radius: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  button { cursor: pointer; font-family: inherit; border: none; outline: none; }
  input { font-family: inherit; outline: none; }

  /* === 登录页 === */
  #login-view {
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 20px;
  }
  .login-card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 40px; width: 100%; max-width: 400px;
  }
  .login-card h1 { font-size: 24px; margin-bottom: 8px; }
  .login-card .sub { color: var(--muted); margin-bottom: 28px; font-size: 14px; }
  .field { margin-bottom: 16px; }
  .field label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  .field input {
    width: 100%; padding: 10px 12px; background: var(--panel-2);
    border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 14px;
  }
  .field input:focus { border-color: var(--accent); }
  .btn {
    padding: 10px 16px; border-radius: 6px; font-size: 14px; font-weight: 500;
    transition: background 0.15s;
  }
  .btn-primary { background: var(--accent); color: #fff; width: 100%; }
  .btn-primary:hover { background: var(--accent-hover); }
  .btn-ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); }
  .btn-ghost:hover { color: var(--text); border-color: var(--accent); }
  .err-msg { color: var(--danger); font-size: 13px; margin-top: 12px; min-height: 18px; }
  .login-tabs { display: flex; gap: 8px; margin-bottom: 24px; }
  .login-tabs button {
    flex: 1; padding: 8px; background: transparent; color: var(--muted);
    border-bottom: 2px solid transparent; font-size: 14px;
  }
  .login-tabs button.active { color: var(--text); border-bottom-color: var(--accent); }

  /* === 主应用 === */
  #app-view { display: none; min-height: 100vh; }
  .app-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 24px; height: 56px; background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  .app-header .logo { font-weight: 600; font-size: 16px; }
  .app-header .user-area { display: flex; align-items: center; gap: 12px; font-size: 13px; }
  .app-header .user-area .role-tag {
    padding: 2px 8px; background: var(--panel-2); border-radius: 4px;
    color: var(--muted); font-size: 12px;
  }
  .logout-btn { color: var(--muted); background: transparent; font-size: 13px; }
  .logout-btn:hover { color: var(--danger); }

  .app-body { display: flex; height: calc(100vh - 56px); }
  .sidebar {
    width: 200px; background: var(--panel); border-right: 1px solid var(--border);
    padding: 16px 0; flex-shrink: 0;
  }
  .nav-item {
    display: flex; align-items: center; gap: 10px; padding: 10px 20px;
    color: var(--muted); font-size: 14px; width: 100%; text-align: left;
    background: transparent; transition: all 0.15s;
  }
  .nav-item:hover { color: var(--text); background: var(--panel-2); }
  .nav-item.active { color: var(--accent); background: var(--panel-2); border-right: 3px solid var(--accent); }
  .nav-icon { width: 18px; text-align: center; }

  .main-content { flex: 1; padding: 24px; overflow-y: auto; }
  .main-content h2 { font-size: 18px; margin-bottom: 16px; }
  .convert-panel {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px;
  }

  /* === 转换面板 === */
  .dropzone {
    border: 2px dashed var(--border); border-radius: var(--radius);
    padding: 48px 24px; text-align: center; transition: all 0.2s;
    cursor: pointer;
  }
  .dropzone:hover, .dropzone.dragover {
    border-color: var(--accent); background: rgba(79, 140, 255, 0.05);
  }
  .dropzone .dz-icon { font-size: 36px; margin-bottom: 12px; }
  .dropzone .dz-text { font-size: 15px; margin-bottom: 4px; }
  .dropzone .dz-hint { font-size: 12px; color: var(--muted); }
  .file-input { display: none; }

  .progress-area { margin-top: 24px; display: none; }
  .progress-area.active { display: block; }
  .progress-steps { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .step {
    flex: 1; min-width: 100px; padding: 8px 12px; background: var(--panel-2);
    border-radius: 6px; font-size: 12px; color: var(--muted); text-align: center;
    border: 1px solid var(--border);
  }
  .step.done { color: var(--success); border-color: var(--success); }
  .step.active { color: var(--accent); border-color: var(--accent); }
  .step.error { color: var(--danger); border-color: var(--danger); }
  .progress-bar {
    height: 6px; background: var(--panel-2); border-radius: 3px; overflow: hidden; margin-bottom: 12px;
  }
  .progress-fill {
    height: 100%; background: var(--accent); width: 0%;
    transition: width 0.4s ease;
  }
  .task-status { font-size: 13px; color: var(--muted); margin-bottom: 16px; }

  .result-area { display: none; }
  .result-area.active { display: block; }
  .result-area .dxf-preview {
    background: #fff; border-radius: 6px; height: 300px;
    display: flex; align-items: center; justify-content: center;
    color: #666; margin-bottom: 12px; overflow: hidden;
  }
  .result-area .download-btn {
    display: inline-block; padding: 10px 20px; background: var(--success);
    color: #fff; border-radius: 6px; text-decoration: none; font-size: 14px;
  }
  .error-box {
    background: rgba(248, 113, 113, 0.1); border: 1px solid var(--danger);
    color: var(--danger); padding: 12px; border-radius: 6px; font-size: 13px;
    margin-top: 12px; display: none;
  }
  .error-box.active { display: block; }

  /* === 知识库 === */
  .kb-list { display: flex; flex-direction: column; gap: 8px; }
  .kb-item {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 14px 16px;
  }
  .kb-item .kb-title { font-size: 14px; margin-bottom: 4px; }
  .kb-item .kb-meta { font-size: 12px; color: var(--muted); }
  .empty-state { color: var(--muted); font-size: 14px; text-align: center; padding: 40px; }

  /* === 设置页 === */
  .settings-form { max-width: 480px; }
  .settings-form .field select {
    width: 100%; padding: 10px 12px; background: var(--panel-2);
    border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 14px;
  }
  .settings-form .save-btn {
    margin-top: 8px; padding: 10px 24px; background: var(--accent);
    color: #fff; border-radius: 6px; font-size: 14px;
  }
  .settings-form .save-btn:hover { background: var(--accent-hover); }
  .settings-msg { font-size: 13px; margin-top: 12px; min-height: 18px; }
  .settings-msg.ok { color: var(--success); }
  .settings-msg.err { color: var(--danger); }
  .key-hint { font-size: 12px; color: var(--muted); margin-top: 4px; }

  /* === AI对话面板 === */
  .chat-wrap {
    display: flex; flex-direction: column; height: 100%;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); overflow: hidden;
  }
  .chat-header {
    padding: 12px 20px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    background: var(--panel-2);
  }
  .chat-header .ch-title { font-size: 15px; font-weight: 600; }
  .chat-header .ch-status { font-size: 12px; color: var(--muted); }

  /* 阶段进度条 */
  .stage-bar {
    display: flex; gap: 4px; padding: 10px 16px;
    background: var(--panel-2); border-bottom: 1px solid var(--border);
  }
  .stage-node {
    flex: 1; padding: 6px 4px; text-align: center; font-size: 11px;
    color: var(--muted); background: var(--panel); border-radius: 4px;
    border: 1px solid var(--border); position: relative;
  }
  .stage-node.done { color: var(--success); border-color: var(--success); background: rgba(52,211,153,0.08); }
  .stage-node.active { color: var(--accent); border-color: var(--accent); background: rgba(79,140,255,0.10); font-weight: 600; }
  .stage-node + .stage-node::before {
    content: ''; position: absolute; left: -5px; top: 50%;
    width: 8px; height: 1px; background: var(--border);
  }

  /* 对话消息区 */
  .chat-messages {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 14px;
    background: var(--bg);
  }
  .msg { max-width: 78%; padding: 12px 16px; border-radius: 12px;
    font-size: 14px; line-height: 1.6; word-wrap: break-word; }
  .msg.ai {
    align-self: flex-start; background: var(--panel-2);
    border: 1px solid var(--border); border-bottom-left-radius: 4px;
  }
  .msg.user {
    align-self: flex-end; background: var(--accent); color: #fff;
    border-bottom-right-radius: 4px;
  }
  .msg.system {
    align-self: center; background: transparent; color: var(--muted);
    font-size: 12px; border: 1px dashed var(--border); max-width: 60%;
  }
  .msg.ai strong, .msg.user strong { font-weight: 700; }
  .msg.ai ul, .msg.ai ol { margin: 6px 0 6px 20px; }
  .msg.ai li { margin: 2px 0; }
  .msg.ai code {
    background: var(--bg); padding: 1px 5px; border-radius: 3px;
    font-family: ui-monospace, "SF Mono", monospace; font-size: 12px;
  }
  .msg-actions {
    margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border);
    display: flex; gap: 8px;
  }
  .msg-actions .act-btn {
    padding: 4px 12px; font-size: 12px; border-radius: 4px;
    background: transparent; border: 1px solid var(--border); color: var(--muted);
  }
  .msg-actions .act-btn.confirm { color: var(--success); border-color: var(--success); }
  .msg-actions .act-btn.correct { color: var(--warn); border-color: var(--warn); }
  .msg-actions .act-btn:hover:not(:disabled) { background: var(--panel); }
  .msg-actions .act-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* 上传区 */
  .chat-empty {
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: 12px; color: var(--muted); padding: 40px;
  }
  .chat-empty .ce-icon { font-size: 48px; opacity: 0.6; }
  .chat-dropzone {
    border: 2px dashed var(--border); border-radius: var(--radius);
    padding: 32px 48px; text-align: center; cursor: pointer;
    transition: all 0.2s; width: 100%; max-width: 420px;
  }
  .chat-dropzone:hover, .chat-dropzone.dragover {
    border-color: var(--accent); background: rgba(79,140,255,0.05);
  }

  /* 输入栏 */
  .chat-input-bar {
    padding: 12px 16px; border-top: 1px solid var(--border);
    background: var(--panel); display: flex; gap: 8px; align-items: flex-end;
  }
  .chat-input-bar textarea {
    flex: 1; resize: none; max-height: 120px; min-height: 40px;
    padding: 10px 12px; background: var(--panel-2); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); font-size: 14px; font-family: inherit;
  }
  .chat-input-bar textarea:focus { border-color: var(--accent); outline: none; }
  .chat-input-bar .send-btn {
    padding: 10px 18px; background: var(--accent); color: #fff;
    border-radius: 8px; font-size: 14px; font-weight: 500;
  }
  .chat-input-bar .send-btn:hover:not(:disabled) { background: var(--accent-hover); }
  .chat-input-bar .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .chat-input-bar .download-btn {
    padding: 10px 18px; background: var(--success); color: #fff;
    border-radius: 8px; font-size: 14px; text-decoration: none;
    display: inline-block;
  }

  /* 适配非chat视图: chat容器需占满高度 */
  .main-content.chat-mode { padding: 16px; display: flex; }
  .main-content.chat-mode > h2 { display: none; }
</style>
</head>
<body>

<!-- === 登录页 === -->
<div id="login-view">
  <div class="login-card">
    <h1>Beacon专家</h1>
    <div class="sub">3D→2D工程图自动转换平台</div>
    <div class="login-tabs">
      <button id="tab-login" class="active" onclick="switchAuthTab('login')">登录</button>
      <button id="tab-register" onclick="switchAuthTab('register')">注册</button>
    </div>
    <div id="username-field" class="field" style="display:none;">
      <label>用户名</label>
      <input type="text" id="reg-username" autocomplete="username">
    </div>
    <div class="field">
      <label>邮箱</label>
      <input type="email" id="email" autocomplete="email">
    </div>
    <div class="field">
      <label>密码</label>
      <input type="password" id="password" autocomplete="current-password">
    </div>
    <button class="btn btn-primary" id="auth-btn" onclick="doAuth()">登录</button>
    <div class="err-msg" id="auth-err"></div>
  </div>
</div>

<!-- === 主应用 === -->
<div id="app-view">
  <div class="app-header">
    <div class="logo">Beacon专家</div>
    <div class="user-area">
      <span id="user-email"></span>
      <span class="role-tag" id="user-role"></span>
      <button class="logout-btn" onclick="logout()">退出</button>
    </div>
  </div>
  <div class="app-body">
    <div class="sidebar">
      <button class="nav-item active" data-view="convert" onclick="switchView('convert')">
        <span class="nav-icon">⚙</span> 转换
      </button>
      <button class="nav-item" data-view="knowledge" onclick="switchView('knowledge')">
        <span class="nav-icon">📚</span> 知识库
      </button>
      <button class="nav-item" data-view="settings" onclick="switchView('settings')">
        <span class="nav-icon">🔧</span> 设置
      </button>
      <button class="nav-item" data-view="chat" onclick="switchView('chat')">
        <span class="nav-icon">🤖</span> Beacon
      </button>
    </div>
    <div class="main-content" id="main-content"></div>
  </div>
</div>

<script>
const API = '';
let TOKEN = localStorage.getItem('beacon_token') || '';
let USER = JSON.parse(localStorage.getItem('beacon_user') || 'null');
let authMode = 'login';
let pollTimer = null;
let currentTaskId = null;

// === 工具 ===
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN;
  if (opts.body && !(opts.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(API + path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
  return data;
}

// === 登录/注册 ===
function switchAuthTab(mode) {
  authMode = mode;
  document.getElementById('tab-login').classList.toggle('active', mode === 'login');
  document.getElementById('tab-register').classList.toggle('active', mode === 'register');
  document.getElementById('username-field').style.display = mode === 'register' ? 'block' : 'none';
  document.getElementById('auth-btn').textContent = mode === 'login' ? '登录' : '注册';
  document.getElementById('auth-err').textContent = '';
}

async function doAuth() {
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const errEl = document.getElementById('auth-err');
  errEl.textContent = '';
  if (!email || !password) { errEl.textContent = '请填写邮箱和密码'; return; }
  const endpoint = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
  const payload = { email, password };
  if (authMode === 'register') {
    const username = document.getElementById('reg-username').value.trim();
    if (!username) { errEl.textContent = '请填写用户名'; return; }
    payload.username = username;
  }
  try {
    const data = await api(endpoint, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    TOKEN = data.token;
    USER = { id: data.user_id, role: data.role, email };
    localStorage.setItem('beacon_token', TOKEN);
    localStorage.setItem('beacon_user', JSON.stringify(USER));
    enterApp();
  } catch (e) {
    errEl.textContent = e.message;
  }
}

function logout() {
  TOKEN = '';
  USER = null;
  localStorage.removeItem('beacon_token');
  localStorage.removeItem('beacon_user');
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  document.getElementById('app-view').style.display = 'none';
  document.getElementById('login-view').style.display = 'flex';
}

function enterApp() {
  document.getElementById('login-view').style.display = 'none';
  document.getElementById('app-view').style.display = 'block';
  document.getElementById('user-email').textContent = USER.email;
  document.getElementById('user-role').textContent = USER.role;
  switchView('convert');
}

// === 视图切换 ===
function switchView(view) {
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.view === view);
  });
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  const main = document.getElementById('main-content');
  main.classList.toggle('chat-mode', view === 'chat');
  if (view === 'convert') renderConvert();
  else if (view === 'knowledge') renderKnowledge();
  else if (view === 'settings') renderSettings();
  else if (view === 'chat') renderChat();
}

function renderConvert() {
  document.getElementById('main-content').innerHTML = `
    <h2>STP → DXF 转换</h2>
    <div class="convert-panel">
      <div class="dropzone" id="dropzone" onclick="document.getElementById('file-input').click()">
        <div class="dz-icon">📁</div>
        <div class="dz-text">拖拽STP/STEP文件到此处，或点击选择</div>
        <div class="dz-hint">支持 .stp / .step，最大 50MB</div>
        <input type="file" id="file-input" class="file-input" accept=".stp,.step">
      </div>
      <div class="progress-area" id="progress-area">
        <div class="progress-steps">
          <div class="step" data-step="queued">排队</div>
          <div class="step" data-step="projecting">几何校验</div>
          <div class="step" data-step="classifying">投影</div>
          <div class="step" data-step="rendering">渲染</div>
          <div class="step" data-step="done">完成</div>
        </div>
        <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
        <div class="task-status" id="task-status">准备中...</div>
      </div>
      <div class="error-box" id="error-box"></div>
      <div class="result-area" id="result-area">
        <div class="dxf-preview">DXF预览（待集成）</div>
        <a class="download-btn" id="download-link" href="#">下载 DXF</a>
      </div>
    </div>
  `;
  setupDropzone();
}

function setupDropzone() {
  const dz = document.getElementById('dropzone');
  const input = document.getElementById('file-input');
  ['dragenter', 'dragover'].forEach(ev => {
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(ev => {
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('dragover'); });
  });
  dz.addEventListener('drop', e => {
    const files = e.dataTransfer.files;
    if (files.length) handleFile(files[0]);
  });
  input.addEventListener('change', e => {
    if (e.target.files.length) handleFile(e.target.files[0]);
  });
}

async function handleFile(file) {
  const errBox = document.getElementById('error-box');
  errBox.classList.remove('active');
  const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!['.stp', '.step'].includes(suffix)) {
    errBox.textContent = '仅支持 .stp / .step 文件';
    errBox.classList.add('active');
    return;
  }
  const formData = new FormData();
  formData.append('file', file);
  try {
    document.getElementById('task-status').textContent = '上传中...';
    document.getElementById('progress-area').classList.add('active');
    const data = await api('/api/convert/upload', { method: 'POST', body: formData });
    currentTaskId = data.task_id;
    pollStatus(data.task_id);
  } catch (e) {
    errBox.textContent = '上传失败: ' + e.message;
    errBox.classList.add('active');
  }
}

const STEP_ORDER = ['queued', 'projecting', 'classifying', 'rendering', 'done'];
const STEP_LABEL = {
  queued: '排队', projecting: '几何校验', classifying: '投影',
  rendering: '渲染', done: '完成', failed: '失败',
};

function pollStatus(taskId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const s = await api('/api/convert/status/' + taskId);
      updateProgress(s.status);
      document.getElementById('task-status').textContent = '状态: ' + (STEP_LABEL[s.status] || s.status);
      if (s.status === 'done' && s.dxf_ready) {
        clearInterval(pollTimer); pollTimer = null;
        showResult(taskId);
      } else if (s.status === 'failed') {
        clearInterval(pollTimer); pollTimer = null;
        const errBox = document.getElementById('error-box');
        errBox.textContent = '转换失败: ' + (s.error || '未知错误');
        errBox.classList.add('active');
      }
    } catch (e) {
      clearInterval(pollTimer); pollTimer = null;
    }
  }, 2000);
}

function updateProgress(status) {
  const idx = STEP_ORDER.indexOf(status);
  const pct = status === 'done' ? 100 : (idx >= 0 ? (idx / STEP_ORDER.length) * 100 : 0);
  document.getElementById('progress-fill').style.width = pct + '%';
  document.querySelectorAll('.step').forEach(el => {
    const stepIdx = STEP_ORDER.indexOf(el.dataset.step);
    el.classList.remove('done', 'active', 'error');
    if (status === 'failed') {
      el.classList.add('error');
    } else if (stepIdx < idx) {
      el.classList.add('done');
    } else if (stepIdx === idx) {
      el.classList.add('active');
    }
  });
}

function showResult(taskId) {
  const area = document.getElementById('result-area');
  area.classList.add('active');
  document.getElementById('download-link').href = API + '/api/convert/download/' + taskId + '?t=' + TOKEN;
  // 注: download端点用Authorization header, 这里用token作query param需要后端支持
  // 临时方案: 用fetch+blob
  document.getElementById('download-link').onclick = async (e) => {
    e.preventDefault();
    try {
      const res = await fetch(API + '/api/convert/download/' + taskId, {
        headers: { 'Authorization': 'Bearer ' + TOKEN },
      });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = taskId + '.dxf';
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('下载失败: ' + err.message);
    }
  };
}

// === 知识库 ===
async function renderKnowledge() {
  const el = document.getElementById('main-content');
  el.innerHTML = '<h2>知识库</h2><div id="kb-container">加载中...</div>';
  try {
    const data = await api('/api/knowledge/list');
    const list = data.items || data.list || [];
    const container = document.getElementById('kb-container');
    if (!list.length) {
      container.innerHTML = '<div class="empty-state">暂无知识条目</div>';
      return;
    }
    container.innerHTML = '<div class="kb-list">' + list.map(k => `
      <div class="kb-item">
        <div class="kb-title">${escapeHtml(k.title)}</div>
        <div class="kb-meta">${escapeHtml(k.category || 'general')} · ${escapeHtml((k.scope || 'personal'))} · ${k.updated_at || ''}</div>
      </div>
    `).join('') + '</div>';
  } catch (e) {
    document.getElementById('kb-container').innerHTML =
      '<div class="empty-state">加载失败: ' + escapeHtml(e.message) + '</div>';
  }
}

// === 设置页 ===
async function renderSettings() {
  const el = document.getElementById('main-content');
  el.innerHTML = `
    <h2>LLM 配置</h2>
    <div class="settings-form">
      <div class="field">
        <label>Provider</label>
        <select id="llm-provider"></select>
      </div>
      <div class="field">
        <label>Model</label>
        <input type="text" id="llm-model" placeholder="例如 glm-4.5-air">
      </div>
      <div class="field">
        <label>API Key</label>
        <input type="password" id="llm-key" placeholder="留空则保留已存Key">
        <div class="key-hint" id="key-hint"></div>
      </div>
      <div class="field">
        <label>Base URL (可选)</label>
        <input type="text" id="llm-baseurl" placeholder="留空使用默认">
      </div>
      <button class="save-btn" onclick="saveSettings()">保存</button>
      <div class="settings-msg" id="settings-msg"></div>
    </div>
  `;
  try {
    const cfg = await api('/api/settings/llm');
    const sel = document.getElementById('llm-provider');
    const providers = cfg.available_providers || [];
    sel.innerHTML = providers.map(p => `<option value="${p}">${p}</option>`).join('');
    if (cfg.provider) sel.value = cfg.provider;
    if (cfg.model) document.getElementById('llm-model').value = cfg.model;
    if (cfg.base_url) document.getElementById('llm-baseurl').value = cfg.base_url;
    document.getElementById('key-hint').textContent =
      cfg.has_api_key ? '已配置Key (留空保留)' : '未配置';
  } catch (e) {
    showSettingsMsg('加载失败: ' + e.message, 'err');
  }
}

async function saveSettings() {
  const msg = document.getElementById('settings-msg');
  msg.className = 'settings-msg';
  msg.textContent = '保存中...';
  const payload = {
    provider: document.getElementById('llm-provider').value,
    model: document.getElementById('llm-model').value.trim(),
    api_key: document.getElementById('llm-key').value,
    base_url: document.getElementById('llm-baseurl').value.trim() || null,
  };
  if (!payload.model) { showSettingsMsg('请填写Model', 'err'); return; }
  try {
    await api('/api/settings/llm', { method: 'POST', body: JSON.stringify(payload) });
    showSettingsMsg('已保存', 'ok');
  } catch (e) {
    showSettingsMsg('保存失败: ' + e.message, 'err');
  }
}

function showSettingsMsg(text, kind) {
  const msg = document.getElementById('settings-msg');
  msg.className = 'settings-msg ' + kind;
  msg.textContent = text;
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// === AI对话面板 (Phase 4) ===
let chatSessionId = null;
let chatStage = 'init';
const CHAT_STAGES = ['init', 'classify', 'understand', 'plan', 'convert', 'audit', 'done'];
const STAGE_LABEL = {
  init: '上传', classify: '分类', understand: '理解',
  plan: '规划', convert: '转换', audit: '审计', done: '完成',
};

function renderChat() {
  const el = document.getElementById('main-content');
  el.innerHTML = `
    <div class="chat-wrap">
      <div class="chat-header">
        <span class="ch-title">Beacon AI 助手</span>
        <span class="ch-status" id="chat-status">就绪</span>
      </div>
      <div class="stage-bar" id="stage-bar">
        ${CHAT_STAGES.map(s => `<div class="stage-node" data-stage="${s}">${STAGE_LABEL[s]}</div>`).join('')}
      </div>
      <div class="chat-messages" id="chat-messages"></div>
      <div class="chat-input-bar">
        <textarea id="chat-input" rows="1" placeholder="输入消息... (Enter发送, Shift+Enter换行)"
          onkeydown="handleChatKey(event)" oninput="autoGrow(this)"></textarea>
        <button class="send-btn" id="chat-send" onclick="sendMessage()">发送</button>
      </div>
    </div>
  `;
  if (!chatSessionId) renderChatEmpty();
  else {
    // 恢复会话视图(简化: 直接渲染空提示)
    renderChatEmpty();
  }
  updateStageBar();
}

function renderChatEmpty() {
  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML = `
    <div class="chat-empty">
      <div class="ce-icon">📐</div>
      <div style="font-size:15px;">上传STP文件开启AI对话转换</div>
      <div class="chat-dropzone" id="chat-dropzone" onclick="document.getElementById('chat-file-input').click()">
        <div style="font-size:28px; margin-bottom:8px;">📁</div>
        <div>拖拽STP/STEP文件到此处，或点击选择</div>
        <div style="font-size:12px; margin-top:6px; opacity:0.7;">支持 .stp / .step</div>
        <input type="file" id="chat-file-input" class="file-input" accept=".stp,.step">
      </div>
    </div>
  `;
  setupChatDropzone();
}

function setupChatDropzone() {
  const dz = document.getElementById('chat-dropzone');
  if (!dz) return;
  const input = document.getElementById('chat-file-input');
  ['dragenter', 'dragover'].forEach(ev => {
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(ev => {
    dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('dragover'); });
  });
  dz.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) startChat(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', e => {
    if (e.target.files.length) startChat(e.target.files[0]);
  });
}

async function startChat(file) {
  const suffix = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!['.stp', '.step'].includes(suffix)) {
    appendMsg('system', '仅支持 .stp / .step 文件');
    return;
  }
  // 切换到对话消息模式
  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML = '';
  appendMsg('user', '上传文件: ' + file.name);
  setChatStatus('上传中...');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const data = await api('/api/chat/start', { method: 'POST', body: fd });
    chatSessionId = data.session_id || data.id;
    chatStage = data.stage || 'init';
    updateStageBar();
    if (data.message) appendMsg('ai', data.message, data.stage);
    setChatStatus('会话 #' + chatSessionId);
  } catch (e) {
    appendMsg('system', '启动失败: ' + e.message);
    setChatStatus('错误');
  }
}

function appendMsg(role, text, stage) {
  const msgs = document.getElementById('chat-messages');
  if (!msgs) return;
  // 清除空状态占位
  const empty = msgs.querySelector('.chat-empty');
  if (empty) empty.remove();

  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = renderMarkdown(text);

  // AI消息带确认/纠正按钮(非done阶段)
  if (role === 'ai' && stage && stage !== 'done') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.innerHTML = `
      <button class="act-btn confirm" onclick="confirmStage(this)">确认</button>
      <button class="act-btn correct" onclick="correctStage(this)">纠正</button>
    `;
    div.appendChild(actions);
  }
  // done阶段附加下载按钮
  if (role === 'ai' && stage === 'done') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.innerHTML = `<a class="download-btn" href="#" onclick="downloadDxf(event)">下载 DXF</a>`;
    div.appendChild(actions);
  }
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function renderMarkdown(text) {
  // 简单Markdown: 转义→粗体→列表→换行
  let s = escapeHtml(text);
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // 行内代码
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // 无序列表
  const lines = s.split('\n');
  let html = '', inUl = false;
  for (const ln of lines) {
    const m = ln.match(/^\s*[-*]\s+(.*)$/);
    if (m) {
      if (!inUl) { html += '<ul>'; inUl = true; }
      html += '<li>' + m[1] + '</li>';
    } else {
      if (inUl) { html += '</ul>'; inUl = false; }
      if (ln.trim() === '') html += '<br>';
      else html += ln + '<br>';
    }
  }
  if (inUl) html += '</ul>';
  return html;
}

function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
}

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  if (!chatSessionId) {
    appendMsg('system', '请先上传STP文件开启会话');
    return;
  }
  appendMsg('user', text);
  input.value = '';
  autoGrow(input);
  setChatStatus('AI思考中...');
  toggleSend(false);
  try {
    const data = await api('/api/chat/' + chatSessionId + '/message', {
      method: 'POST', body: JSON.stringify({ message: text }),
    });
    if (data.stage) { chatStage = data.stage; updateStageBar(); }
    if (data.message) appendMsg('ai', data.message, data.stage);
    setChatStatus('会话 #' + chatSessionId);
  } catch (e) {
    appendMsg('system', '发送失败: ' + e.message);
    setChatStatus('错误');
  } finally {
    toggleSend(true);
  }
}

async function confirmStage(btn) {
  if (!chatSessionId) return;
  btn.disabled = true;
  setChatStatus('处理中...');
  try {
    const data = await api('/api/chat/' + chatSessionId + '/confirm', { method: 'POST' });
    if (data.stage) { chatStage = data.stage; updateStageBar(); }
    if (data.message) appendMsg('ai', data.message, data.stage);
    setChatStatus('会话 #' + chatSessionId);
  } catch (e) {
    appendMsg('system', '确认失败: ' + e.message);
    btn.disabled = false;
  }
}

async function correctStage(btn) {
  if (!chatSessionId) return;
  const input = document.getElementById('chat-input');
  const hint = input.value.trim();
  setChatStatus('处理中...');
  try {
    const body = { message: hint || '请重新评估本阶段' };
    const data = await api('/api/chat/' + chatSessionId + '/correct', {
      method: 'POST', body: JSON.stringify(body),
    });
    if (hint) { appendMsg('user', hint); input.value = ''; autoGrow(input); }
    if (data.stage) { chatStage = data.stage; updateStageBar(); }
    if (data.message) appendMsg('ai', data.message, data.stage);
    setChatStatus('会话 #' + chatSessionId);
  } catch (e) {
    appendMsg('system', '纠正失败: ' + e.message);
  }
}

async function downloadDxf(e) {
  e.preventDefault();
  if (!chatSessionId) return;
  try {
    const res = await fetch(API + '/api/chat/' + chatSessionId + '/download', {
      headers: { 'Authorization': 'Bearer ' + TOKEN },
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = (chatSessionId || 'beacon') + '.dxf';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    appendMsg('system', '下载失败: ' + err.message);
  }
}

function updateStageBar() {
  const idx = CHAT_STAGES.indexOf(chatStage);
  document.querySelectorAll('.stage-node').forEach(el => {
    const si = CHAT_STAGES.indexOf(el.dataset.stage);
    el.classList.remove('done', 'active');
    if (si < idx) el.classList.add('done');
    else if (si === idx) el.classList.add('active');
  });
}

function setChatStatus(t) {
  const el = document.getElementById('chat-status');
  if (el) el.textContent = t;
}

function toggleSend(enable) {
  const btn = document.getElementById('chat-send');
  if (btn) btn.disabled = !enable;
}

// === 启动 ===
if (TOKEN && USER) enterApp();
</script>
</body>
</html>"""


def get_html() -> str:
    """返回SPA HTML字符串."""
    return HTML

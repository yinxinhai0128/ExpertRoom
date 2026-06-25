'use strict';

// ── 状态 ──────────────────────────────────────────────────────
let currentRoomId = null;
let agentsCache = {};
let activeSSE = null;
let thinkingEl = null;

const $ = id => document.getElementById(id);

// ── 初始化 ────────────────────────────────────────────────────
async function init() {
  await loadConfig();
  await loadHistoryRooms();

  $('new-room-btn').addEventListener('click', openModal);
  $('modal-cancel').addEventListener('click', closeModal);
  $('modal-create').addEventListener('click', createRoom);
  $('send-btn').addEventListener('click', sendUserMessage);
  $('user-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendUserMessage();
    }
  });
  $('user-input').addEventListener('input', autoResize);

  // Agent 管理器
  $('manage-agents-btn').addEventListener('click', openAgentManager);
  $('agent-modal-close').addEventListener('click', closeAgentManager);
  $('agent-modal-overlay').addEventListener('click', e => {
    if (e.target === $('agent-modal-overlay')) closeAgentManager();
  });
  $('add-agent-btn').addEventListener('click', openNewAgent);
  $('af-cancel').addEventListener('click', showAgentListView);
  $('af-save').addEventListener('click', saveAgent);
}

function autoResize() {
  const ta = $('user-input');
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
}

// ── API helpers ───────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function esc(s) {
  return String(s || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

// ── 配置加载 ──────────────────────────────────────────────────
async function loadConfig() {
  const data = await api('/api/config');
  agentsCache = data.agents || {};
}

// ── Modal ─────────────────────────────────────────────────────
function openModal() {
  // 渲染 agent 复选框
  const wrap = $('agent-checkboxes');
  wrap.innerHTML = '';
  for (const [id, a] of Object.entries(agentsCache)) {
    const chip = document.createElement('div');
    chip.className = 'agent-checkbox selected';
    chip.dataset.id = id;
    chip.innerHTML = `${esc(a.avatar || '🤖')} ${esc(a.name)}`;
    chip.addEventListener('click', () => chip.classList.toggle('selected'));
    wrap.appendChild(chip);
  }
  $('topic-input').value = '';
  $('goal-input').value = '';
  $('modal-overlay').classList.add('open');
  $('topic-input').focus();
}

function closeModal() {
  $('modal-overlay').classList.remove('open');
}

async function createRoom() {
  const topic = $('topic-input').value.trim();
  if (!topic) { alert('请输入话题'); return; }

  const goal = $('goal-input').value.trim();
  const selectedIds = [...$('agent-checkboxes').querySelectorAll('.selected')]
    .map(el => el.dataset.id);

  const btn = $('modal-create');
  btn.disabled = true;
  btn.textContent = '创建中…';

  try {
    const room = await api('/api/rooms', {
      method: 'POST',
      body: JSON.stringify({ topic, goal, agent_ids: selectedIds }),
    });
    closeModal();
    await switchRoom(room.id);
  } catch (err) {
    alert('创建失败：' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '开始讨论';
  }
}

// ── 切换 / 加载房间 ───────────────────────────────────────────
async function switchRoom(roomId) {
  // 停止旧 SSE
  if (activeSSE) { activeSSE.close(); activeSSE = null; }

  currentRoomId = roomId;
  const room = await api(`/api/rooms/${roomId}`);

  // 顶栏
  $('room-topic').textContent = room.topic;
  $('room-goal').textContent = room.goal ? `目标：${room.goal}` : '';
  setProgress(0, 0);

  // 侧边栏 agents
  renderAgentSidebar(room.agent_ids || []);

  // 清空消息区
  $('messages').innerHTML = '';

  // 如果已完成，加载历史消息
  if (room.status === 'done') {
    const msgs = await api(`/api/rooms/${roomId}/messages`);
    msgs.forEach(renderMessage);
    const artifacts = await api(`/api/rooms/${roomId}/artifacts`);
    artifacts.forEach(a => renderArtifact(a.content, a.id));
    addSystemMsg('讨论已结束');
    setInputEnabled(false);
    return;
  }

  // 开始 SSE 流
  setInputEnabled(true);
  startSSE(roomId);

  // 更新历史房间列表
  await loadHistoryRooms();
}

// ── SSE ───────────────────────────────────────────────────────
function startSSE(roomId) {
  const es = new EventSource(`/api/rooms/${roomId}/stream`);
  activeSSE = es;

  es.onmessage = e => {
    let evt;
    try { evt = JSON.parse(e.data); } catch (_) { return; }
    handleEvent(evt);
  };

  es.onerror = () => {
    es.close();
    activeSSE = null;
  };
}

function handleEvent(evt) {
  switch (evt.type) {
    case 'system':
      addSystemMsg(evt.content);
      break;

    case 'agents':
      break;

    case 'thinking':
      // 并行模式：每个 agent 有独立的 thinking 指示器
      showThinkingFor(evt.agent_id, evt.agent_name, evt.avatar);
      setAgentStatus(evt.agent_id, 'thinking');
      break;

    case 'thinking_done':
      // 某个 agent 已回来，移除它的 thinking 指示器
      removeThinkingFor(evt.agent_id);
      setAgentStatus(evt.agent_id, 'idle');
      break;

    case 'round_start':
      addRoundSeparator(evt.round);
      break;

    case 'message':
      removeThinkingFor(evt.agent_id);  // 兜底：确保消息前思考泡消失
      renderMessage(evt);
      setAgentStatus(evt.agent_id, 'idle');
      break;

    case 'user_message':
      renderMessage({ ...evt, agent_id: 'user', agent_name: '你', avatar: '🧑' });
      break;

    case 'tool_call':
      renderToolCall(evt);
      break;

    case 'tool_result':
      renderToolResult(evt);
      break;

    case 'goal_progress':
      setProgress(evt.current, evt.target, evt.description);
      break;

    case 'synthesize_start':
      removeAllThinking();
      addSystemMsg('正在生成讨论总结…');
      break;

    case 'artifact':
      renderArtifact(evt.content, evt.artifact_id);
      break;

    case 'done':
      removeAllThinking();
      addSystemMsg(`讨论结束（共 ${evt.turns} 轮）`);
      setInputEnabled(false);
      loadHistoryRooms();
      break;

    case 'goal_achieved':
      addSystemMsg(`目标达成：${evt.message || ''}`);
      break;

    case 'error':
      addSystemMsg(`⚠ ${evt.content}`);
      break;
  }

  scrollToBottom();
}

// ── 消息渲染 ──────────────────────────────────────────────────
function renderMessage(msg) {
  const isUser = msg.agent_id === 'user';
  const div = document.createElement('div');
  div.className = `msg${isUser ? ' user-msg' : ''}`;
  div.dataset.agentId = msg.agent_id;

  const avatar = esc(msg.avatar || agentsCache[msg.agent_id]?.avatar || '🤖');
  const name = esc(msg.agent_name || msg.agent_id);
  const content = esc(msg.content || '');
  const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

  div.innerHTML = `
    <div class="msg-avatar">${avatar}</div>
    <div class="msg-body">
      <div class="msg-header">
        <span class="msg-name">${name}</span>
        <span class="msg-time">${time}</span>
      </div>
      <div class="msg-content">${content}</div>
    </div>`;

  $('messages').appendChild(div);
}

function renderToolCall(evt) {
  const block = document.createElement('div');
  block.className = 'msg';
  block.id = `tc-${evt.agent_id}-${Date.now()}`;
  block.dataset.agentId = evt.agent_id;

  const inputStr = typeof evt.tool_input === 'object'
    ? JSON.stringify(evt.tool_input, null, 2)
    : String(evt.tool_input || '');

  block.innerHTML = `
    <div class="msg-avatar">${esc(evt.avatar || '🔧')}</div>
    <div class="msg-body" style="max-width:80%">
      <div class="tool-block">
        <div class="tool-header" onclick="toggleTool(this)">
          <span class="tool-icon">🔧</span>
          <span class="tool-name">${esc(evt.tool_name)}</span>
          <span class="msg-name" style="margin-left:4px;font-size:11px;color:var(--text-dim)">${esc(evt.agent_name)}</span>
          <span class="tool-toggle">▶ 展开</span>
        </div>
        <div class="tool-body">
          <div class="tool-section-label">输入</div>
          <pre>${esc(inputStr)}</pre>
        </div>
      </div>
    </div>`;

  $('messages').appendChild(block);
  // 存 id 供 tool_result 追加
  block._toolName = evt.tool_name;
}

function renderToolResult(evt) {
  // 找最近的 tool_call 块追加结果
  const allBlocks = [...$('messages').querySelectorAll('.tool-block')];
  const lastBlock = allBlocks[allBlocks.length - 1];
  if (lastBlock) {
    const body = lastBlock.querySelector('.tool-body');
    if (body) {
      const resDiv = document.createElement('div');
      resDiv.innerHTML = `
        <div class="tool-section-label">结果</div>
        <pre>${esc(String(evt.content || '').slice(0, 800))}</pre>`;
      body.appendChild(resDiv);
    }
  }
}

function toggleTool(header) {
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.tool-toggle');
  body.classList.toggle('open');
  toggle.textContent = body.classList.contains('open') ? '▼ 收起' : '▶ 展开';
}

function renderArtifact(content, artifactId) {
  const div = document.createElement('div');
  div.className = 'msg';
  div.innerHTML = `
    <div class="msg-avatar">📄</div>
    <div class="msg-body" style="max-width:90%">
      <div class="artifact-block">
        <div class="artifact-header">
          <span class="artifact-title">讨论总结</span>
          ${artifactId
            ? `<a class="artifact-download" href="/api/artifacts/${artifactId}/download" download>⬇ 下载</a>`
            : ''}
        </div>
        <div class="artifact-content">${esc(content)}</div>
      </div>
    </div>`;
  $('messages').appendChild(div);
}

// ── thinking 指示器（并行：每个 agent 独立） ───────────────────
function showThinkingFor(agentId, agentName, avatar) {
  const existingId = `thinking-${agentId}`;
  if (document.getElementById(existingId)) return;  // 已经在显示

  const div = document.createElement('div');
  div.id = existingId;
  div.className = 'msg thinking-row';
  div.innerHTML = `
    <div class="msg-avatar">${esc(avatar || agentsCache[agentId]?.avatar || '🤖')}</div>
    <div class="msg-body">
      <div class="thinking-msg">
        <span class="msg-name">${esc(agentName)}</span> 正在思考
        <span class="thinking-dots"><span>·</span><span>·</span><span>·</span></span>
      </div>
    </div>`;
  $('messages').appendChild(div);
  scrollToBottom();
}

function removeThinkingFor(agentId) {
  const el = document.getElementById(`thinking-${agentId}`);
  if (el) el.remove();
  const card = $(`agent-card-${agentId}`);
  card?.classList.remove('active');
}

function removeAllThinking() {
  document.querySelectorAll('.thinking-row').forEach(el => el.remove());
  document.querySelectorAll('.agent-card').forEach(c => c.classList.remove('active'));
}

// ── 轮次分隔线 ────────────────────────────────────────────────
function addRoundSeparator(round) {
  const div = document.createElement('div');
  div.className = 'round-separator';
  div.innerHTML = `<span>第 ${round} 轮</span>`;
  $('messages').appendChild(div);
}

// ── 系统消息 ──────────────────────────────────────────────────
function addSystemMsg(text) {
  const div = document.createElement('div');
  div.className = 'system-msg';
  div.textContent = text;
  $('messages').appendChild(div);
}

// ── Agent 侧边栏 ──────────────────────────────────────────────
function renderAgentSidebar(agentIds) {
  const container = $('agent-list');
  container.innerHTML = '';
  agentIds.forEach(id => {
    const a = agentsCache[id];
    if (!a) return;
    const card = document.createElement('div');
    card.className = 'agent-card';
    card.id = `agent-card-${id}`;
    card.innerHTML = `
      <div class="agent-avatar">${esc(a.avatar || '🤖')}</div>
      <div class="agent-info">
        <div class="agent-name">${esc(a.name)}</div>
        <div class="agent-role">${esc(a.identity || '')}</div>
      </div>
      <div class="agent-status" id="agent-status-${id}">待机</div>`;
    container.appendChild(card);
  });
}

function setAgentStatus(agentId, status) {
  const el = $(`agent-status-${agentId}`);
  const card = $(`agent-card-${agentId}`);
  if (!el) return;
  if (status === 'thinking') {
    el.className = 'agent-status thinking';
    el.textContent = '思考中…';
    card?.classList.add('active');
  } else {
    el.className = 'agent-status';
    el.textContent = '待机';
    card?.classList.remove('active');
  }
}

// ── 进度条 ────────────────────────────────────────────────────
function setProgress(current, target, desc) {
  const pct = target > 0 ? Math.min(100, Math.round(current / target * 100)) : 0;
  $('progress-fill').style.width = pct + '%';
  $('progress-label').textContent = desc || (target > 0 ? `${current} / ${target}` : '进度');
}

// ── 用户插话 ──────────────────────────────────────────────────
async function sendUserMessage() {
  if (!currentRoomId) return;
  const ta = $('user-input');
  const content = ta.value.trim();
  if (!content) return;

  ta.value = '';
  ta.style.height = '42px';

  try {
    await api(`/api/rooms/${currentRoomId}/inject`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  } catch (err) {
    addSystemMsg('插话失败：' + err.message);
  }
}

// ── 历史房间 ──────────────────────────────────────────────────
async function loadHistoryRooms() {
  try {
    const rooms = await api('/api/rooms');
    const container = $('history-rooms');
    container.innerHTML = '';
    rooms.slice(0, 10).forEach(r => {
      const div = document.createElement('div');
      div.className = `room-item${r.id === currentRoomId ? ' current' : ''}`;
      div.textContent = r.topic.slice(0, 24);
      div.title = r.topic;
      div.addEventListener('click', () => switchRoom(r.id));
      container.appendChild(div);
    });
  } catch (_) {}
}

// ── 工具函数 ──────────────────────────────────────────────────
function setInputEnabled(enabled) {
  $('user-input').disabled = !enabled;
  $('send-btn').disabled = !enabled;
}

function scrollToBottom() {
  const el = $('messages');
  el.scrollTop = el.scrollHeight;
}

// 全局 toggleTool 供 onclick 调用
window.toggleTool = toggleTool;

// ── Agent 管理器 ───────────────────────────────────────────────
let editingAgentId = null;   // null = 新建，string = 编辑现有

function openAgentManager() {
  editingAgentId = null;
  showAgentListView();
  loadAgentList();
  $('agent-modal-overlay').classList.add('open');
}

function closeAgentManager() {
  $('agent-modal-overlay').classList.remove('open');
}

function showAgentListView() {
  $('agent-list-view').style.display = '';
  $('agent-form-view').style.display = 'none';
  $('agent-modal-title').textContent = '管理智能体';
}

function showAgentFormView(isNew) {
  $('agent-list-view').style.display = 'none';
  $('agent-form-view').style.display = '';
  $('agent-modal-title').textContent = isNew ? '新建智能体' : '编辑智能体';
}

async function loadAgentList() {
  const container = $('agent-cards');
  container.innerHTML = '<div style="color:var(--text-dim);padding:8px">加载中…</div>';
  try {
    const agents = await api('/api/agents');
    container.innerHTML = '';
    if (!Object.keys(agents).length) {
      container.innerHTML = '<div style="color:var(--text-dim);padding:8px">暂无智能体</div>';
      return;
    }
    for (const [id, a] of Object.entries(agents)) {
      const card = document.createElement('div');
      card.className = 'agent-manage-card';
      card.innerHTML = `
        <span class="agent-manage-avatar">${esc(a.avatar || '🤖')}</span>
        <div class="agent-manage-info">
          <span class="agent-manage-name">${esc(a.name)}</span>
          <span class="agent-manage-meta">${esc(a.identity || '')} · ${esc(a.backend || '')}</span>
        </div>
        <div class="agent-manage-actions">
          <button class="btn-edit" data-id="${esc(id)}">编辑</button>
          <button class="btn-delete" data-id="${esc(id)}">删除</button>
        </div>`;
      card.querySelector('.btn-edit').addEventListener('click', () => openEditAgent(id));
      card.querySelector('.btn-delete').addEventListener('click', () => confirmDeleteAgent(id, a.name));
      container.appendChild(card);
    }
  } catch (err) {
    container.innerHTML = `<div style="color:#f87171">加载失败：${esc(err.message)}</div>`;
  }
}

async function openEditAgent(agentId) {
  editingAgentId = agentId;
  showAgentFormView(false);
  // 加载详情
  try {
    const data = await api(`/api/agents/${agentId}/detail`);
    $('af-id').value = agentId;
    $('af-id').disabled = true;
    $('af-id-note').textContent = 'ID 创建后不可修改';
    $('af-name').value = data.name || '';
    $('af-avatar').value = data.avatar || '';
    $('af-backend').value = data.backend || 'hermes';
    $('af-identity').value = data.identity || '';
    $('af-expertise').value = data.expertise || '';
    $('af-traits').value = (data.personality?.traits || []).join(', ');
    $('af-tone').value = data.speaking_style?.tone || '';
    $('af-goals').value = (data.goals?.public || []).join(', ');
    $('af-memory').value = (data.memory?.long_term || []).join(', ');
    $('af-enabled').checked = data.enabled !== false;
  } catch (err) {
    alert('加载失败：' + err.message);
    showAgentListView();
  }
}

function openNewAgent() {
  editingAgentId = null;
  showAgentFormView(true);
  $('af-id').value = '';
  $('af-id').disabled = false;
  $('af-id-note').textContent = '例如：lawyer（仅字母/数字/下划线）';
  $('af-name').value = '';
  $('af-avatar').value = '';
  $('af-backend').value = 'hermes';
  $('af-identity').value = '';
  $('af-expertise').value = '';
  $('af-traits').value = '';
  $('af-tone').value = '';
  $('af-goals').value = '';
  $('af-memory').value = '';
  $('af-enabled').checked = true;
}

function splitCSV(str) {
  return str.split(',').map(s => s.trim()).filter(Boolean);
}

async function saveAgent() {
  const name = $('af-name').value.trim();
  if (!name) { alert('请填写名称'); return; }

  const payload = {
    name,
    avatar: $('af-avatar').value.trim() || '🤖',
    backend: $('af-backend').value,
    identity: $('af-identity').value.trim(),
    expertise: $('af-expertise').value.trim(),
    traits: splitCSV($('af-traits').value),
    tone: $('af-tone').value.trim(),
    goals: splitCSV($('af-goals').value),
    long_term: splitCSV($('af-memory').value),
    enabled: $('af-enabled').checked,
  };

  const btn = $('af-save');
  btn.disabled = true;
  btn.textContent = '保存中…';

  try {
    if (editingAgentId) {
      await api(`/api/agents/${editingAgentId}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
    } else {
      const agentId = $('af-id').value.trim();
      if (!agentId) { alert('请填写 ID'); return; }
      await api(`/api/agents?agent_id=${encodeURIComponent(agentId)}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    }
    // 刷新配置缓存
    await loadConfig();
    showAgentListView();
    loadAgentList();
  } catch (err) {
    alert('保存失败：' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '保存';
  }
}

async function confirmDeleteAgent(agentId, agentName) {
  if (!confirm(`确认删除「${agentName}」？此操作不可撤销。`)) return;
  try {
    await api(`/api/agents/${agentId}`, { method: 'DELETE' });
    await loadConfig();
    loadAgentList();
  } catch (err) {
    alert('删除失败：' + err.message);
  }
}

// ── 启动 ───────────────────────────────────────────────────────
init().catch(console.error);

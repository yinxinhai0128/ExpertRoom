/* ── State ────────────────────────────────────────────────────── */
let currentRoomId = null;
let currentRoomStatus = null;
let currentActiveSession = false;   // true only when an SSE stream is connected
let eventSource = null;
let allAgents = {};
let adapterHealth = {};

/* ── Bootstrap ───────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  loadAdapterHealth();
  loadAgents();
  loadRoomList();

  $('btn-new-room').addEventListener('click', openNewRoomModal);
  $('btn-manage-agents').addEventListener('click', openAgentManager);
  $('btn-start').addEventListener('click', startDiscussion);
  $('btn-pause').addEventListener('click', pauseDiscussion);
  $('btn-resume').addEventListener('click', resumeDiscussion);
  $('btn-stop').addEventListener('click', stopDiscussion);
  $('btn-summarize').addEventListener('click', summarizeDiscussion);

  $('modal-cancel').addEventListener('click', closeNewRoomModal);
  $('modal-close').addEventListener('click', closeNewRoomModal);
  $('modal-create').addEventListener('click', createRoom);

  $('agent-modal-close').addEventListener('click', closeAgentManager);
  $('add-agent-btn').addEventListener('click', openNewAgent);
  $('af-cancel').addEventListener('click', showAgentList);
  $('af-save').addEventListener('click', saveAgent);

  $('send-btn').addEventListener('click', sendMessage);
  $('user-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  $('user-input').addEventListener('input', function() { autoResize(this); });
});

function $(id) { return document.getElementById(id); }

/* ── Adapter health ──────────────────────────────────────────── */
async function loadAdapterHealth() {
  try {
    const r = await fetch('/api/adapters/health');
    adapterHealth = await r.json();
    renderAdapterHealth();
  } catch { /* silent */ }
}

function renderAdapterHealth() {
  const el = $('adapter-health-list');
  el.innerHTML = '';
  for (const [name, info] of Object.entries(adapterHealth)) {
    const row = document.createElement('div');
    row.className = 'health-row';
    row.innerHTML =
      `<span class="health-dot ${info.available ? 'dot-ok' : 'dot-err'}"></span>` +
      `<span class="health-name">${name}</span>` +
      (!info.available
        ? `<span class="health-warn" title="${escAttr(info.reason)}">⚠</span>`
        : '');
    el.appendChild(row);
  }
}

/* ── Agents ──────────────────────────────────────────────────── */
async function loadAgents() {
  try {
    const r = await fetch('/api/agents');
    allAgents = await r.json();
  } catch { /* silent */ }
}

function renderRoomAgentSidebar(participants) {
  const el = $('agent-list');
  el.innerHTML = '';
  const ids = Object.keys(participants).length > 0
    ? Object.keys(participants)
    : Object.keys(allAgents);
  if (!ids.length) { el.innerHTML = '<div class="dim">暂无专家</div>'; return; }
  ids.forEach(aid => {
    const a = participants[aid] || allAgents[aid];
    if (!a) return;
    const row = document.createElement('div');
    row.className = 'agent-row';
    row.id = `agent-row-${aid}`;
    row.innerHTML =
      `<span class="agent-avatar">${a.avatar || '🤖'}</span>` +
      `<div class="agent-row-info">` +
        `<span class="agent-row-name">${escHtml(a.name)}</span>` +
        `<span class="agent-row-status dim" id="agent-status-${aid}">待机</span>` +
      `</div>`;
    el.appendChild(row);
  });
}

function updateAgentStatus(agentId, status, cls) {
  const el = $(`agent-status-${agentId}`);
  if (!el) return;
  el.textContent = status;
  el.className = `agent-row-status ${cls || ''}`;
}

/* ── Room list ───────────────────────────────────────────────── */
async function loadRoomList() {
  try {
    const r = await fetch('/api/rooms');
    const rooms = await r.json();
    renderRoomList(rooms);
  } catch { /* silent */ }
}

function renderRoomList(rooms) {
  const el = $('history-rooms');
  el.innerHTML = '';
  if (!rooms.length) { el.innerHTML = '<div class="dim">暂无记录</div>'; return; }
  rooms.forEach(room => {
    const es = effectiveStatus(room.status, room.active_session || false);
    const item = document.createElement('div');
    item.className = 'room-item' + (room.id === currentRoomId ? ' active' : '');
    item.innerHTML =
      `<div class="room-item-topic">${escHtml(room.topic)}</div>` +
      `<div class="room-item-meta">` +
        `<span class="badge badge-${statusClass(es)}">${statusLabel(es)}</span>` +
        `<span class="dim">${room.turn_count} 轮</span>` +
      `</div>`;
    item.addEventListener('click', () => openRoom(room.id));
    el.appendChild(item);
  });
}

function statusClass(s) {
  const map = { ready:'idle', running:'running', interrupted:'paused',
                paused:'paused', stopped:'stopped', synthesizing:'running',
                done:'done', failed:'err', error:'err' };
  return map[s] || 'idle';
}
function statusLabel(s) {
  const map = { ready:'待开始', running:'进行中', interrupted:'可继续',
                paused:'已暂停', stopped:'已停止', synthesizing:'综合中',
                done:'已完成', failed:'失败', error:'错误' };
  return map[s] || s;
}

// Derive display status: "running" without active session = "interrupted"
function effectiveStatus(status, activeSession) {
  if (status === 'running' && !activeSession) return 'interrupted';
  return status;
}

/* ── Open existing room ──────────────────────────────────────── */
async function openRoom(roomId) {
  try {
    const r = await fetch(`/api/rooms/${roomId}`);
    if (!r.ok) return;
    const room = await r.json();
    currentRoomId = room.id;
    currentRoomStatus = room.status;
    currentActiveSession = room.active_session || false;
    updateRoomMeta(room);

    const mr = await fetch(`/api/rooms/${roomId}/messages`);
    const msgs = await mr.json();
    clearMessages();
    msgs.forEach(m => appendHistoryMessage(m));

    buildTargetAgentSelect(room.agent_ids);

    // Load artifact for done/stopped rooms
    if (['done', 'stopped'].includes(room.status)) {
      const ar = await fetch(`/api/rooms/${roomId}/artifacts`);
      const arts = await ar.json();
      const report = arts.find(a => a.artifact_type === 'report');
      if (report) renderArtifact(report);
    }

    // Recalculate progress from stored messages
    fetch(`/api/rooms/${roomId}/progress`)
      .then(r => r.json())
      .then(p => updateProgress(p))
      .catch(() => {});

    updateControlsForStatus(room.status, room.active_session || false);
    loadRoomList();
  } catch { /* silent */ }
}

/* ── New room modal ──────────────────────────────────────────── */
function openNewRoomModal() {
  const container = $('agent-checkboxes');
  container.innerHTML = '';
  Object.entries(allAgents).forEach(([aid, a]) => {
    const health = adapterHealth[a.backend];
    const unavail = health && !health.available;
    const label = document.createElement('label');
    label.className = 'agent-check-label' + (unavail ? ' agent-unavail' : '');
    label.innerHTML =
      `<input type="checkbox" value="${aid}" ${unavail ? '' : 'checked'} />` +
      `<span>${a.avatar || '🤖'} ${escHtml(a.name)}</span>` +
      (a.identity ? `<span class="dim">${escHtml(a.identity)}</span>` : '') +
      (unavail
        ? `<span class="warn-tag" title="${escAttr(health.reason)}">⚠ ${a.backend} 不可用</span>`
        : '');
    container.appendChild(label);
  });
  $('topic-input').value = '';
  $('goal-input').value = '';
  $('mode-select').value = 'moderated';
  showModal('modal-overlay');
}
function closeNewRoomModal() { hideModal('modal-overlay'); }

async function createRoom() {
  const topic = $('topic-input').value.trim();
  if (!topic) { alert('请输入话题'); return; }
  const agentIds = Array.from(
    document.querySelectorAll('#agent-checkboxes input:checked')
  ).map(cb => cb.value);
  if (!agentIds.length) { alert('请至少选择一位专家'); return; }

  try {
    const r = await fetch('/api/rooms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic,
        goal: $('goal-input').value.trim(),
        agent_ids: agentIds,
        discussion_mode: $('mode-select').value,
      }),
    });
    if (!r.ok) { const e = await r.json(); alert(e.detail || '创建失败'); return; }
    const room = await r.json();
    closeNewRoomModal();
    currentRoomId = room.id;
    currentRoomStatus = room.status;
    clearMessages();
    updateRoomMeta(room);
    buildTargetAgentSelect(room.agent_ids);
    // Pre-render sidebar with selected agents
    const selected = {};
    agentIds.forEach(aid => { if (allAgents[aid]) selected[aid] = allAgents[aid]; });
    renderRoomAgentSidebar(selected);
    updateControlsForStatus(room.status, false);
    loadRoomList();
  } catch (e) { alert('创建失败：' + e); }
}

/* ── Room lifecycle controls ─────────────────────────────────── */
async function startDiscussion() {
  if (!currentRoomId) return;
  try {
    const r = await fetch(`/api/rooms/${currentRoomId}/start`, { method: 'POST' });
    if (!r.ok) {
      const e = await r.json();
      appendSystemMsg('❌ 无法启动：' + (e.detail || '未知错误'));
      return;
    }
    connectSSE(currentRoomId);
  } catch (e) { appendSystemMsg('❌ 启动失败：' + e); }
}

async function pauseDiscussion() {
  if (!currentRoomId) return;
  await fetch(`/api/rooms/${currentRoomId}/pause`, { method: 'POST' });
  currentRoomStatus = 'paused';
  currentActiveSession = false;
  updateControlsForStatus('paused', false);
}

async function resumeDiscussion() {
  if (!currentRoomId) return;
  const r = await fetch(`/api/rooms/${currentRoomId}/resume`, { method: 'POST' });
  if (!r.ok) return;
  connectSSE(currentRoomId);
}

async function stopDiscussion() {
  if (!currentRoomId) return;
  if (!confirm('停止当前讨论？停止后仍可生成总结。')) return;
  await fetch(`/api/rooms/${currentRoomId}/stop`, { method: 'POST' });
  if (eventSource) { eventSource.close(); eventSource = null; }
  currentRoomStatus = 'stopped';
  currentActiveSession = false;
  updateControlsForStatus('stopped', false);
  loadRoomList();
}

async function summarizeDiscussion() {
  if (!currentRoomId) return;
  appendSystemMsg('正在生成总结…');
  $('btn-summarize').disabled = true;
  try {
    const r = await fetch(`/api/rooms/${currentRoomId}/synthesize`, { method: 'POST' });
    if (!r.ok) { const e = await r.json(); appendSystemMsg('总结失败：' + (e.detail || '')); return; }
    const data = await r.json();
    renderArtifact({ artifact_id: data.artifact_id, content: data.content, artifact_type: 'report', is_mock: data.is_mock });
    currentActiveSession = false;
    updateControlsForStatus('done', false);
    updateChecklist({ summary_ready: true });
    loadRoomList();
  } catch (e) { appendSystemMsg('总结失败：' + e); }
}

function updateControlsForStatus(status, activeSession) {
  if (activeSession === undefined) activeSession = currentActiveSession;
  currentRoomStatus = status;
  currentActiveSession = activeSession;

  const es = effectiveStatus(status, activeSession);
  const badge = $('room-status-badge');
  badge.textContent = statusLabel(es);
  badge.className = `badge badge-${statusClass(es)}`;

  // Start: first-time (ready) or re-enter (stopped / interrupted)
  const canStart = ['ready', 'stopped', 'interrupted'].includes(es);
  $('btn-start').disabled = !canStart;
  $('btn-start').textContent = es === 'ready' ? '▶ 开始' : '▶ 继续';

  // Pause: only when actually streaming
  $('btn-pause').disabled = es !== 'running';

  // Resume: only when paused (active session waiting)
  $('btn-resume').disabled = es !== 'paused';

  // Stop: when actively running or paused
  $('btn-stop').disabled = !['running', 'paused'].includes(es);

  // Summarize: any state where there could be messages, except ready/done/synthesizing
  const canSummarize = currentRoomId && !['ready', 'done', 'synthesizing'].includes(es);
  $('btn-summarize').disabled = !canSummarize;

  // Input: usable whenever the room has started (messages persist durably)
  const inputActive = ['running', 'paused', 'stopped', 'interrupted'].includes(es);
  $('user-input').disabled          = !inputActive;
  $('send-btn').disabled            = !inputActive;
  $('target-agent-select').disabled = !inputActive;
}

/* ── SSE connection ──────────────────────────────────────────── */
function connectSSE(roomId) {
  if (eventSource) { eventSource.close(); }
  currentActiveSession = true;
  updateControlsForStatus(currentRoomStatus || 'running', true);
  eventSource = new EventSource(`/api/rooms/${roomId}/stream`);

  eventSource.onmessage = e => {
    try { handleEvent(JSON.parse(e.data)); } catch { /* skip malformed */ }
  };
  eventSource.onerror = () => {
    currentActiveSession = false;
    if (currentRoomStatus !== 'done' && currentRoomStatus !== 'stopped') {
      // Connection dropped unexpectedly — mark as interrupted
      appendSystemMsg('⚠ 连接断开，讨论中断。可点「▶ 继续」恢复。');
      updateControlsForStatus(currentRoomStatus || 'running', false);
    }
    eventSource.close();
    eventSource = null;
  };
}

function handleEvent(evt) {
  switch (evt.type) {
    case 'system':
      appendSystemMsg(evt.content);
      break;
    case 'agents':
      allAgents = Object.assign({}, allAgents, evt.agents);
      renderRoomAgentSidebar(evt.agents);
      break;
    case 'thinking':
      updateAgentStatus(evt.agent_id, '思考中…', 'status-thinking');
      showThinkingFor(evt.agent_id, evt.agent_name, evt.avatar);
      break;
    case 'thinking_done':
      updateAgentStatus(evt.agent_id, '已发言', 'status-done');
      removeThinkingFor(evt.agent_id);
      break;
    case 'message':
      removeThinkingFor(evt.agent_id);
      appendMessage(evt);
      if (evt.agent_id !== 'user') {
        updateAgentStatus(evt.agent_id, '已发言', 'status-done');
      }
      break;
    case 'round_start':
      addRoundSeparator(evt.round);
      document.querySelectorAll('[id^="agent-status-"]').forEach(el => {
        el.textContent = '待机';
        el.className = 'agent-row-status dim';
      });
      break;
    case 'goal_progress':
      updateProgress(evt);
      break;
    case 'synthesize_start':
      appendSystemMsg('📋 正在综合讨论成果…');
      updateControlsForStatus('synthesizing', false);
      break;
    case 'artifact':
      renderArtifact(evt);
      updateChecklist({ summary_ready: true });
      break;
    case 'done':
      removeAllThinking();
      appendSystemMsg('✅ 讨论完成，共 ' + evt.turns + ' 轮');
      currentActiveSession = false;
      updateControlsForStatus('done', false);
      loadRoomList();
      if (eventSource) { eventSource.close(); eventSource = null; }
      break;
    case 'error':
      removeAllThinking();
      appendSystemMsg('❌ ' + evt.content);
      break;
  }
  // Keep status badge in sync during active stream
  if (['system','thinking','message','round_start'].includes(evt.type)
      && currentRoomStatus !== 'done' && currentRoomStatus !== 'synthesizing') {
    if (currentRoomStatus !== 'running' || !currentActiveSession) {
      currentRoomStatus = 'running';
      updateControlsForStatus('running', true);
    }
  }
}

/* ── Thinking indicators ─────────────────────────────────────── */
function showThinkingFor(agentId, agentName, avatar) {
  const eid = `thinking-${agentId}`;
  if (document.getElementById(eid)) return;
  const div = document.createElement('div');
  div.id = eid;
  div.className = 'msg thinking-row';
  div.innerHTML =
    `<div class="msg-avatar">${avatar || '🤖'}</div>` +
    `<div class="msg-body">` +
      `<div class="msg-name">${escHtml(agentName)}</div>` +
      `<div class="thinking-dots"><span></span><span></span><span></span></div>` +
    `</div>`;
  appendToMessages(div);
}
function removeThinkingFor(agentId) {
  const el = document.getElementById(`thinking-${agentId}`);
  if (el) el.remove();
}
function removeAllThinking() {
  document.querySelectorAll('.thinking-row').forEach(el => el.remove());
}

/* ── Messages ────────────────────────────────────────────────── */
function appendMessage(msg) {
  if (!msg.content) return;
  const isUser = msg.agent_id === 'user';
  const div = document.createElement('div');
  div.className = 'msg' + (isUser ? ' msg-user' : '');
  if (isUser) {
    div.innerHTML =
      `<div class="msg-body user-bubble">${escHtml(msg.content)}</div>` +
      `<div class="msg-avatar">🧑</div>`;
  } else {
    div.innerHTML =
      `<div class="msg-avatar">${escHtml(msg.avatar || '🤖')}</div>` +
      `<div class="msg-body">` +
        `<div class="msg-name">${escHtml(msg.agent_name)}</div>` +
        `<div class="msg-content">${escHtml(msg.content)}</div>` +
      `</div>`;
  }
  appendToMessages(div);
}

function appendHistoryMessage(m) {
  if (m.message_type === 'system') { appendSystemMsg(m.content); return; }
  appendMessage({ agent_id: m.agent_id, agent_name: m.agent_name,
                  avatar: m.avatar, content: m.content });
}

function appendSystemMsg(text) {
  if (!text) return;
  const div = document.createElement('div');
  div.className = 'system-msg';
  div.textContent = text;
  appendToMessages(div);
}

function addRoundSeparator(round) {
  const div = document.createElement('div');
  div.className = 'round-separator';
  div.innerHTML = `<span>第 ${round} 轮</span>`;
  appendToMessages(div);
}

function appendToMessages(el) {
  const container = $('messages');
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function clearMessages() { $('messages').innerHTML = ''; }

/* ── Artifact / summary ──────────────────────────────────────── */
function renderArtifact(art) {
  const container = $('messages');
  const prev = container.querySelector('.artifact-card');
  if (prev) prev.remove();

  const card = document.createElement('div');
  card.className = 'artifact-card';
  const downloadLink = art.artifact_id
    ? `<a class="artifact-download" href="/api/artifacts/${art.artifact_id}/download" target="_blank">下载</a>`
    : '';
  const mockWarn = art.is_mock
    ? `<div class="artifact-mock-warn">⚠ 测试模式：总结由 mock 后端生成，非真实模型输出</div>`
    : '';
  card.innerHTML =
    `<div class="artifact-header"><span>📄 讨论总结</span>${downloadLink}</div>` +
    mockWarn +
    `<div class="artifact-body">${markdownToHtml(art.content || '')}</div>`;
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;
}

function markdownToHtml(md) {
  const lines = md.split('\n');
  let html = '';
  let inList = false;
  lines.forEach(line => {
    if (line.startsWith('## ')) {
      if (inList) { html += '</ul>'; inList = false; }
      html += `<div class="art-section-title">${escHtml(line.slice(3))}</div>`;
    } else if (/^(\d+\.|-)\s/.test(line.trim())) {
      if (!inList) { html += '<ul class="art-list">'; inList = true; }
      html += `<li>${escHtml(line.replace(/^(\d+\.|-)\s*/, ''))}</li>`;
    } else {
      if (inList) { html += '</ul>'; inList = false; }
      if (line.trim()) html += `<p class="art-p">${escHtml(line)}</p>`;
    }
  });
  if (inList) html += '</ul>';
  return html;
}

/* ── Progress / checklist ────────────────────────────────────── */
function updateProgress(evt) {
  const ck = evt.checklist;
  if (!ck) return;
  updateChecklist(ck);

  const boolKeys = ['problem_defined','risks_identified','tradeoffs_discussed','next_actions_ready','summary_ready'];
  const done = boolKeys.filter(k => ck[k]).length + (ck.solutions_count > 0 ? 1 : 0);
  const total = boolKeys.length + 1;
  const pct = Math.round((done / total) * 100);
  $('progress-fill').style.width = pct + '%';
  $('progress-label').textContent = pct + '%';

  if (evt.description) {
    $('goal-text').textContent = evt.description;
    $('goal-text').classList.remove('dim');
  }
}

function updateChecklist(ck) {
  setCheck('ck-problem',   ck.problem_defined);
  setCheck('ck-risks',     ck.risks_identified);
  setCheck('ck-tradeoffs', ck.tradeoffs_discussed);
  setCheck('ck-actions',   ck.next_actions_ready);
  setCheck('ck-summary',   ck.summary_ready);
  if (ck.solutions_count !== undefined) {
    $('ck-solutions-count').textContent = ck.solutions_count;
    setCheck('ck-solutions', ck.solutions_count > 0);
  }
}

function setCheck(id, done) {
  const el = $(id);
  if (!el) return;
  const icon = el.querySelector('.ck-icon');
  el.classList.toggle('ck-done', !!done);
  if (icon) icon.textContent = done ? '✓' : '○';
}

/* ── User input ──────────────────────────────────────────────── */
async function sendMessage() {
  const content = $('user-input').value.trim();
  if (!content || !currentRoomId) return;
  const target = $('target-agent-select').value;
  $('user-input').value = '';
  autoResize($('user-input'));

  // Show immediately in UI
  appendMessage({ agent_id: 'user', agent_name: '你', avatar: '🧑', content });

  try {
    await fetch(`/api/rooms/${currentRoomId}/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, target_agent_id: target }),
    });
  } catch (e) { appendSystemMsg('发送失败：' + e); }
}

function buildTargetAgentSelect(agentIds) {
  const sel = $('target-agent-select');
  sel.innerHTML = '<option value="">全体专家</option>';
  (agentIds || []).forEach(aid => {
    const a = allAgents[aid];
    if (!a) return;
    const opt = document.createElement('option');
    opt.value = aid;
    opt.textContent = (a.avatar || '🤖') + ' ' + a.name;
    sel.appendChild(opt);
  });
}

/* ── Room meta display ───────────────────────────────────────── */
function updateRoomMeta(room) {
  $('room-title').textContent = room.topic;
  $('goal-text').textContent = room.goal || '自由讨论';
  $('goal-text').classList.toggle('dim', !room.goal);
}

/* ── Agent manager modal ─────────────────────────────────────── */
function openAgentManager() {
  showModal('agent-modal-overlay');
  loadAgentList();
}
function closeAgentManager() { hideModal('agent-modal-overlay'); }

async function loadAgentList() {
  try {
    const r = await fetch('/api/agents');
    const agents = await r.json();
    allAgents = agents;
    const container = $('agent-cards');
    container.innerHTML = '';
    Object.entries(agents).forEach(([aid, a]) => {
      const card = document.createElement('div');
      card.className = 'agent-manage-card';
      card.innerHTML =
        `<span class="agent-manage-avatar">${a.avatar || '🤖'}</span>` +
        `<div class="agent-manage-info">` +
          `<strong>${escHtml(a.name)}</strong>` +
          `<span class="dim">${escHtml(a.identity || a.expertise || '')}</span>` +
          `<span class="dim">${a.backend}</span>` +
        `</div>` +
        `<div class="agent-manage-actions">` +
          `<button class="btn-edit" onclick="openEditAgent('${aid}')">编辑</button>` +
          `<button class="btn-delete" onclick="confirmDeleteAgent('${aid}')">删除</button>` +
        `</div>`;
      container.appendChild(card);
    });
    showAgentList();
  } catch { /* silent */ }
}

function showAgentList() {
  $('agent-list-view').style.display = 'block';
  $('agent-form-view').style.display = 'none';
  $('agent-modal-title').textContent = '管理专家智能体';
}

async function openEditAgent(agentId) {
  try {
    const r = await fetch(`/api/agents/${agentId}/detail`);
    const d = await r.json();
    $('af-id').value = agentId;
    $('af-id').disabled = true;
    $('af-id-note').textContent = '已有 ID 不可修改';
    $('af-name').value = d.name || '';
    $('af-avatar').value = d.avatar || '';
    $('af-backend').value = d.backend || 'hermes';
    $('af-identity').value = d.identity || '';
    $('af-expertise').value = d.expertise || '';
    $('af-traits').value = ((d.personality || {}).traits || []).join(', ');
    $('af-tone').value = ((d.speaking_style || {}).tone) || '';
    $('af-goals').value = ((d.goals || {}).public || []).join(', ');
    $('af-memory').value = ((d.memory || {}).long_term || []).join(', ');
    $('af-enabled').checked = d.enabled !== false;
    $('agent-form-view')._editId = agentId;
    $('agent-list-view').style.display = 'none';
    $('agent-form-view').style.display = 'block';
    $('agent-modal-title').textContent = '编辑：' + d.name;
  } catch { /* silent */ }
}

function openNewAgent() {
  ['af-id','af-name','af-avatar','af-identity','af-expertise','af-traits','af-tone','af-goals','af-memory']
    .forEach(id => { $(id).value = ''; });
  $('af-id').disabled = false;
  $('af-id-note').textContent = '';
  $('af-backend').value = 'hermes';
  $('af-enabled').checked = true;
  $('agent-form-view')._editId = null;
  $('agent-list-view').style.display = 'none';
  $('agent-form-view').style.display = 'block';
  $('agent-modal-title').textContent = '新建专家';
}

async function saveAgent() {
  const editId = $('agent-form-view')._editId;
  const agentId = editId || $('af-id').value.trim();
  if (!agentId) { alert('请输入 ID'); return; }
  const payload = {
    name: $('af-name').value.trim() || agentId,
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
  try {
    const url = editId
      ? `/api/agents/${editId}`
      : `/api/agents?agent_id=${encodeURIComponent(agentId)}`;
    const method = editId ? 'PUT' : 'POST';
    const r = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) { const e = await r.json(); alert(e.detail || '保存失败'); return; }
    await loadAgentList();
    await loadAgents();
  } catch (e) { alert('保存失败：' + e); }
}

async function confirmDeleteAgent(agentId) {
  if (!confirm('删除专家「' + agentId + '」？此操作不可撤销')) return;
  try {
    await fetch(`/api/agents/${agentId}`, { method: 'DELETE' });
    await loadAgentList();
    await loadAgents();
  } catch { /* silent */ }
}

/* ── Utilities ───────────────────────────────────────────────── */
function showModal(id)  { $(id).classList.remove('hidden'); }
function hideModal(id)  { $(id).classList.add('hidden'); }

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function escAttr(s) { return escHtml(s); }
function splitCSV(s) { return s.split(',').map(x => x.trim()).filter(Boolean); }

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 140) + 'px';
}

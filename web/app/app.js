/* ==========================================================================
 * KnowOps ToC 前端 · app.js
 * 零构建 · 原生 ES module · 无第三方依赖
 *
 * 引用编号映射的核心在这里：
 *   - `CitationMap`（见下）：把后端给的 eid（如 e_3）映射成前端编号 1、2、3…
 *   - `renderText()`：把 delta.text 里的 {e_3} 占位符替换成 <sup class="cite">3</sup>
 *   - `↗` 跳转分支：见 `buildCitationCard()` 里 `if (c.url)` 那一行
 * ========================================================================== */

/* ------------------------------------------------------------------ 工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

/* 正文占位符：{e_3} 这种。引用是数据不是文本，必须前端映射成编号。
 * eid 含中文（doc_id 直接取文件名，如 OMS-发货与物流时效承诺），
 * 字符集必须与后端 nodes.py 的 _EID 一致：字母数字下划线 + 中文 + 点 + 连字符。
 * 漏掉中文就会把 {e_OMS-发货...} 原样显示给用户（2026-11-19 实测踩到）。 */
const EID_RE = /\{([A-Za-z0-9_\u4e00-\u9fff.\-]+)\}/g;

/* ------------------------------------------------------------------ 身份 */
/* 本期固定几个演示身份，收敛到 getSubject() 单点。真登录是独立需求，别混进来。 */
const IDENTITIES = [
  {
    key: 'employee',
    role: '员工',
    subject: {
      user_id: 'u_2048',
      account_status: 'active',
      dept_paths: ['D10', 'D10.3', 'D10.3.7'],
      roles: ['employee'],
      subject_epoch: 1,
    },
  },
  {
    key: 'ops',
    role: '仓配运营',
    subject: {
      user_id: 'u_3102',
      account_status: 'active',
      dept_paths: ['D10', 'D10.3'],
      roles: ['employee', 'fulfillment_ops'],
      subject_epoch: 3,
    },
  },
  {
    key: 'manager',
    role: '部门主管',
    subject: {
      user_id: 'u_0081',
      account_status: 'active',
      dept_paths: ['D10'],
      roles: ['employee', 'manager'],
      subject_epoch: 5,
    },
  },
];

const currentIdentity = { key: 'employee' };

function getSubject() {
  const id = IDENTITIES.find((x) => x.key === currentIdentity.key) || IDENTITIES[0];
  return id.subject;
}

/* 当前 ACL epoch：失权判定基准。后端未下发时用 citation 自己的 acl_epoch 兜底，
 * 此时所有引用都视为"在权"。 */
let currentAclEpoch = null;

/* ------------------------------------------------------------------ 引用编号映射 */
/* 把 eid -> 编号（1-based，按 citation 帧到达顺序）。 */
class CitationMap {
  constructor() { this.order = []; this.index = new Map(); }

  add(eid) {
    if (this.index.has(eid)) return this.index.get(eid);
    const n = this.order.length + 1;
    this.order.push(eid);
    this.index.set(eid, n);
    return n;
  }

  get(eid) { return this.index.get(eid); }
  get numberCount() { return this.order.length; }

  // 从持久化的 eid 数组重建（历史消息恢复用）
  static fromOrder(order) {
    const cm = new CitationMap();
    for (const eid of order) cm.add(eid);
    return cm;
  }
}

/* ------------------------------------------------------------------ 极简 markdown */
/* 只认：**粗体**、空行分段、单换行 <br>、- 列表。绝不 innerHTML 拼未转义内容。 */
function renderText(raw, citeMap, streaming) {
  let out = esc(raw);
  // 1. 占位符 -> 角标（必须在段落切分前做，避免被 <p> 打断）
  out = out.replace(EID_RE, (_m, eid) => {
    const n = citeMap ? citeMap.get(eid) : null;
    // 未登记的 eid 直接丢弃，绝不把 {e_xxx} 原文裸露给用户（铁律）。
    if (n == null) return '';
    return `<sup class="cite" data-eid="${esc(eid)}">${n}</sup>`;
  });
  // 2. **粗体**
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // 3. 分段（空行）
  let html = out.split(/\n{2,}/).map((p) => {
    // 单换行 -> <br>
    const line = p.replace(/\n/g, '<br>');
    // 列表项：以 "- " 开头的行
    if (line.includes('<br>') && /^- /.test(line)) {
      const items = line.split('<br>').map((li) => li.replace(/^-\s*/, '')).join('</li><li>');
      return `<ul><li>${items}</li></ul>`;
    }
    return `<p>${line}</p>`;
  }).join('');
  if (streaming) html += '<span class="cursor"></span>';
  return html;
}

/* ------------------------------------------------------------------ 状态 */
const MODE = {
  KNOWLEDGE: 'knowledge',
  FULFILLMENT: 'fulfillment',
};

const state = {
  mode: MODE.KNOWLEDGE,
  sessionId: crypto.randomUUID(),
  turnSeq: 1,
  activeTaskId: null,      // 工单排障当前 task_id
  currentCiteMap: null,    // 当前轮次的引用映射
  currentCitations: [],    // 当前轮次的引用对象数组
  lastEventId: 0,          // 断线续传水位
  streaming: false,
  health: { knowledge: false, fulfillment: false },
};

/* ------------------------------------------------------------------ DOM 引用 */
const el = {
  sidebar: $('#sidebar'),
  sidebarBackdrop: $('#sidebarBackdrop'),
  sidebarToggle: $('#sidebarToggle'),
  sidebarCollapse: $('#sidebarCollapse'),
  newChatBtn: $('#newChatBtn'),
  topbarNewBtn: $('#topbarNewBtn'),
  sessionList: $('#sessionList'),
  footDot: $('#footDot'),
  footText: $('#footText'),
  topbarMode: $('#topbarMode'),
  identityChip: $('#identityChip'),
  identityChipRole: $('#identityChipRole'),
  identityChipUser: $('#identityChipUser'),
  identityDropdown: $('#identityDropdown'),
  hero: $('#hero'),
  chat: $('#chat'),
  chatScroll: $('#chatScroll'),
  messages: $('#messages'),
  input: $('#input'),
  inputBottom: $('#inputBottom'),
  sendBtn: $('#sendBtn'),
  sendBtnBottom: $('#sendBtnBottom'),
  uploadBtn: $('#uploadBtn'),
  entries: $('#entries'),
  entryKnowledge: $('#entryKnowledge'),
  entryFulfillment: $('#entryFulfillment'),
  drawer: $('#drawer'),
  drawerBackdrop: $('#drawerBackdrop'),
  drawerTitle: $('#drawerTitle'),
  drawerClose: $('#drawerClose'),
  drawerDocTitle: $('#drawerDocTitle'),
  drawerDocLoc: $('#drawerDocLoc'),
  drawerPreview: $('#drawerPreview'),
  drawerCopy: $('#drawerCopy'),
  drawerOpen: $('#drawerOpen'),
  toast: $('#toast'),
  topbarUploadBtn: $('#topbarUploadBtn'),
  uploadModal: $('#uploadModal'),
  uploadClose: $('#uploadClose'),
  uploadCancel: $('#uploadCancel'),
  uploadDrop: $('#uploadDrop'),
  uploadFile: $('#uploadFile'),
  uploadFileInfo: $('#uploadFileInfo'),
  uploadFileName: $('#uploadFileName'),
  uploadFileRemove: $('#uploadFileRemove'),
  uploadSubmit: $('#uploadSubmit'),
  uploadStatus: $('#uploadStatus'),
  chunkChildSize: $('#chunkChildSize'),
  chunkParentSize: $('#chunkParentSize'),
  chunkOverlap: $('#chunkOverlap'),
  chunkAcl: $('#chunkAcl'),
};

const PLACEHOLDER = {
  [MODE.KNOWLEDGE]: '例如：签收异常超过 48 小时该怎么处理？',
  [MODE.FULFILLMENT]: '例如：订单 SO20261101003 卡在已出库未签收',
};

const STATUS_TEXT = {
  [MODE.KNOWLEDGE]: '正在检索…',
  [MODE.FULFILLMENT]: '正在分析现象…',
};

/* ------------------------------------------------------------------ 会话持久化 */
function loadSessions() {
  try { return JSON.parse(localStorage.getItem('knowops.sessions') || '[]'); }
  catch { return []; }
}
function saveSessions(list) { localStorage.setItem('knowops.sessions', JSON.stringify(list)); }

/* 只恢复本地缓存的问答（后端无状态，别假装有服务端历史）。 */
function loadSessionMessages(sid) {
  try { return JSON.parse(localStorage.getItem(`knowops.msgs.${sid}`) || '[]'); }
  catch { return []; }
}
function saveSessionMessages(sid, msgs) { localStorage.setItem(`knowops.msgs.${sid}`, JSON.stringify(msgs)); }

/* ------------------------------------------------------------------ 服务状态 */
async function checkHealth() {
  const checks = [
    ['knowledge', '/healthz'],
    ['fulfillment', `http://${location.hostname}:8002/healthz`],
  ];
  for (const [key, url] of checks) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 2500);
      const r = await fetch(url, { signal: ctrl.signal });
      clearTimeout(t);
      state.health[key] = r.ok;
    } catch {
      state.health[key] = false;
    }
  }
  updateHealthUI();
}

function updateHealthUI() {
  const k = state.health.knowledge;
  const f = state.health.fulfillment;
  const ok = k && f;
  const warn = k || f;
  el.footDot.className = 'foot-dot' + (ok ? ' is-ok' : warn ? ' is-warn' : ' is-err');
  el.footText.textContent = ok ? '服务正常' : warn ? '部分服务不可用' : '服务不可用';
  el.entryKnowledge.classList.toggle('is-disabled', !k);
  el.entryFulfillment.classList.toggle('is-disabled', !f);
}

/* ------------------------------------------------------------------ Toast */
let toastTimer = null;
function toast(msg) {
  el.toast.textContent = msg;
  el.toast.hidden = false;
  el.toast.classList.add('is-show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.toast.classList.remove('is-show');
    setTimeout(() => { el.toast.hidden = true; }, 200);
  }, 2200);
}

/* ------------------------------------------------------------------ 模式切换 */
function setMode(mode) {
  if (state.streaming) { toast('回答生成中，请稍候'); return; }
  if (mode === MODE.FULFILLMENT && !state.health.fulfillment) {
    toast('工单排障服务未启动，暂不可用');
    return;
  }
  state.mode = mode;
  el.topbarMode.textContent = mode === MODE.KNOWLEDGE ? '知识检索' : '工单排障';
  el.entryKnowledge.classList.toggle('is-selected', mode === MODE.KNOWLEDGE);
  el.entryFulfillment.classList.toggle('is-selected', mode === MODE.FULFILLMENT);
  el.entryKnowledge.setAttribute('aria-checked', mode === MODE.KNOWLEDGE);
  el.entryFulfillment.setAttribute('aria-checked', mode === MODE.FULFILLMENT);
  const ph = PLACEHOLDER[mode];
  el.input.placeholder = ph;
  el.inputBottom.placeholder = ph;
  el.input.focus();
}

/* ------------------------------------------------------------------ 会话列表渲染 */
function renderSessionList() {
  const sessions = loadSessions();
  el.sessionList.innerHTML = '';
  if (sessions.length === 0) return;

  const groups = [
    { label: '今天', key: 'today' },
    { label: '昨天', key: 'yesterday' },
    { label: '更早', key: 'older' },
  ];
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 86400000;

  const buckets = { today: [], yesterday: [], older: [] };
  for (const s of sessions) {
    const t = s.updatedAt || 0;
    if (t >= todayStart) buckets.today.push(s);
    else if (t >= yesterdayStart) buckets.yesterday.push(s);
    else buckets.older.push(s);
  }

  for (const g of groups) {
    if (buckets[g.key].length === 0) continue;
    const label = document.createElement('div');
    label.className = 'session-group-label';
    label.textContent = g.label;
    el.sessionList.appendChild(label);
    for (const s of buckets[g.key]) {
      const btn = document.createElement('button');
      btn.className = 'session-item' + (s.id === state.sessionId ? ' is-active' : '');
      btn.innerHTML = `<span class="session-item-title">${esc(s.title)}</span>`;
      btn.addEventListener('click', () => switchSession(s.id));
      el.sessionList.appendChild(btn);
    }
  }
}

function switchSession(sid) {
  if (state.streaming) { toast('回答生成中，请稍候'); return; }
  state.sessionId = sid;
  state.turnSeq = 1;
  state.activeTaskId = null;
  state.currentCiteMap = null;
  state.currentCitations = [];
  const msgs = loadSessionMessages(sid);
  renderMessages(msgs);
  renderSessionList();
  closeSidebarOnMobile();
}

function newChat() {
  if (state.streaming) { toast('回答生成中，请稍候'); return; }
  state.sessionId = crypto.randomUUID();
  state.turnSeq = 1;
  state.activeTaskId = null;
  state.currentCiteMap = null;
  state.currentCitations = [];
  el.messages.innerHTML = '';
  el.hero.hidden = false;
  el.chat.hidden = true;
  renderSessionList();
  el.input.value = '';
  el.input.focus();
}

/* ------------------------------------------------------------------ 消息渲染（历史恢复） */
function renderMessages(msgs) {
  el.messages.innerHTML = '';
  if (msgs.length === 0) {
    el.hero.hidden = false;
    el.chat.hidden = true;
    return;
  }
  el.hero.hidden = true;
  el.chat.hidden = false;
  for (const m of msgs) {
    if (m.role === 'user') {
      appendUserMessage(m.content, false);
    } else {
      appendAssistantMessage(m);
    }
  }
  scrollToBottom();
}

/* ------------------------------------------------------------------ 消息节点 */
function appendUserMessage(text, save = true) {
  const div = document.createElement('div');
  div.className = 'msg-user';
  div.textContent = text;
  el.messages.appendChild(div);
  if (save) {
    pushMessage({ role: 'user', content: text });
  }
  scrollToBottom();
}

/* 助手消息返回一个包装器，供流式追加。 */
function createAssistantMessage() {
  const wrap = document.createElement('div');
  wrap.className = 'msg-assistant';
  el.messages.appendChild(wrap);
  return wrap;
}

function appendAssistantMessage(m) {
  const wrap = createAssistantMessage();
  if (m.status) {
    const st = document.createElement('div');
    st.className = 'msg-status';
    st.textContent = m.status;
    wrap.appendChild(st);
  }
  const body = document.createElement('div');
  body.className = 'msg-body';
  body.innerHTML = m.html;
  wrap.appendChild(body);
  if (m.citations && m.citations.length) {
    // 历史恢复：citeMap 持久化存的是 eid 数组，重建回 CitationMap 实例，
    // 否则 buildCitationCard 里 citeMap.get() 会对数组报错（编号全 0）。
    const citeMap = m.citeMap instanceof CitationMap
      ? m.citeMap
      : CitationMap.fromOrder(m.citeMap || []);
    wrap.appendChild(buildCitationsBlock(m.citations, citeMap, m.usedEids));
  }
  if (m.verdictBar) {
    wrap.appendChild(buildVerdictBar(m.verdictBar.type, m.verdictBar.text));
  }
  wrap.appendChild(buildActions());
  return wrap;
}

function pushMessage(m) {
  const sid = state.sessionId;
  const msgs = loadSessionMessages(sid);
  msgs.push(m);
  saveSessionMessages(sid, msgs);
  // 更新侧栏标题（首条用户消息前 20 字）
  const sessions = loadSessions();
  const found = sessions.find((s) => s.id === sid);
  const title = (msgs.find((x) => x.role === 'user')?.content || '新对话').slice(0, 20);
  if (found) {
    found.title = title;
    found.updatedAt = Date.now();
  } else {
    sessions.unshift({ id: sid, title, updatedAt: Date.now() });
  }
  saveSessions(sessions);
  renderSessionList();
}

function scrollToBottom() {
  el.chatScroll.scrollTop = el.chatScroll.scrollHeight;
}

/* ------------------------------------------------------------------ 结算提示条 */
function buildVerdictBar(type, text) {
  const bar = document.createElement('div');
  bar.className = `verdict-bar verdict-${type}`;
  bar.textContent = text;
  return bar;
}

function buildActions() {
  const bar = document.createElement('div');
  bar.className = 'msg-actions';
  const copy = document.createElement('button');
  copy.className = 'action-btn';
  copy.textContent = '复制';
  copy.addEventListener('click', () => {
    const body = bar.parentElement.querySelector('.msg-body');
    navigator.clipboard.writeText(body.textContent).then(() => toast('已复制'));
  });
  const regen = document.createElement('button');
  regen.className = 'action-btn';
  regen.textContent = '重新生成';
  regen.addEventListener('click', () => {
    const msgs = loadSessionMessages(state.sessionId);
    const lastUser = [...msgs].reverse().find((x) => x.role === 'user');
    if (lastUser) send(lastUser.content);
  });
  const like = document.createElement('button');
  like.className = 'action-btn';
  like.textContent = '👍';
  like.addEventListener('click', () => { localStorage.setItem('knowops.feedback', 'up'); toast('感谢反馈'); });
  const dislike = document.createElement('button');
  dislike.className = 'action-btn';
  dislike.textContent = '👎';
  dislike.addEventListener('click', () => { localStorage.setItem('knowops.feedback', 'down'); toast('感谢反馈'); });
  bar.append(copy, regen, like, dislike);
  return bar;
}

/* ------------------------------------------------------------------ 引用区 */
function buildCitationsBlock(citations, citeMap, usedEids) {
  const container = document.createElement('div');
  container.className = 'citations';
  if (citations.length > 5) container.classList.add('is-collapsed');

  const head = document.createElement('div');
  head.className = 'citations-head';
  const used = new Set(usedEids || []);
  const usedCount = citations.filter((c) => used.has(c.eid)).length;
  head.innerHTML = `
    <span class="c-title">🔗 引用 <span class="c-count">${citations.length} 条</span></span>
    <span class="c-count">已引用 ${usedCount} / 共 ${citations.length}</span>
    <span class="c-caret">▾</span>`;
  head.addEventListener('click', () => container.classList.toggle('is-collapsed'));

  const list = document.createElement('div');
  list.className = 'citations-list';
  for (const c of citations) {
    list.appendChild(buildCitationCard(c, citeMap, container));
  }

  container.append(head, list);
  return container;
}

/* 引用卡片。↗ 跳转分支的开启条件在这里：`if (c.url)`。 */
function buildCitationCard(c, citeMap, container) {
  const card = document.createElement('button');
  card.className = 'cite-card';
  const n = citeMap.get(c.eid) ?? 0;
  const lost = c.acl_epoch != null && currentAclEpoch != null && c.acl_epoch !== currentAclEpoch;
  if (lost) card.classList.add('is-lost');

  // 徽标：失权时替换为「已失权」
  const badge = document.createElement('span');
  badge.className = 'cite-badge';
  badge.textContent = lost ? '已失权' : n;

  const info = document.createElement('span');
  info.className = 'cite-info';
  const metaParts = [];
  if (c.page_no) metaParts.push(`第 ${c.page_no} 页`);
  if (c.section_path) metaParts.push(c.section_path);
  metaParts.push(c.doc_id);
  info.innerHTML = `
    <div class="cite-info-title">${esc(c.title)}</div>
    <div class="cite-info-meta">${esc(metaParts.join(' · '))}</div>`;

  const actions = document.createElement('span');
  actions.className = 'cite-actions';

  // ↗：仅在 c.url 存在时渲染（L2 兼容分支），否则不出现死链
  if (c.url) {
    const open = document.createElement('a');
    open.className = 'cite-action';
    open.textContent = '↗';
    open.href = c.url;
    open.target = '_blank';
    open.rel = 'noopener';
    open.title = '打开原文';
    open.addEventListener('click', (e) => e.stopPropagation());
    actions.appendChild(open);
  }

  // ↘：打开详情抽屉（始终存在）
  const detail = document.createElement('span');
  detail.className = 'cite-action';
  detail.textContent = '↘';
  detail.title = '查看详情';
  actions.appendChild(detail);

  card.append(badge, info, actions);

  card.addEventListener('click', (e) => {
    e.stopPropagation();
    if (lost) { toast('该文档权限已变更，引用不可查看'); return; }
    openDrawer(c, n);
    // 高亮正文中所有该 eid 的角标
    flashSupByEid(c.eid);
    // 卡片聚焦
    container.querySelectorAll('.cite-card').forEach((x) => x.classList.remove('is-focus'));
    card.classList.add('is-focus');
  });

  return card;
}

function flashSupByEid(eid) {
  $$(`sup.cite[data-eid="${eid}"]`).forEach((sup) => {
    sup.classList.remove('is-flash');
    void sup.offsetWidth; // 重触发动画
    sup.classList.add('is-flash');
    setTimeout(() => sup.classList.remove('is-flash'), 1200);
  });
}

/* ------------------------------------------------------------------ 抽屉 */
let drawerCitation = null;

function openDrawer(citation, n) {
  drawerCitation = citation;
  el.drawerTitle.textContent = `引用 ${n}`;
  el.drawerDocTitle.textContent = citation.title;
  const locParts = [];
  if (citation.page_no) locParts.push(`第 ${citation.page_no} 页`);
  if (citation.section_path) locParts.push(citation.section_path);
  el.drawerDocLoc.textContent = locParts.join(' · ');
  el.drawerPreview.innerHTML = renderSourcePreview(citation);
  el.drawerOpen.hidden = !citation.url;
  if (citation.url) el.drawerOpen.href = citation.url;
  el.drawer.classList.add('is-open');
  el.drawer.setAttribute('aria-hidden', 'false');
  el.drawerBackdrop.hidden = false;
  requestAnimationFrame(() => el.drawerBackdrop.classList.add('is-open'));
}

function closeDrawer() {
  el.drawer.classList.remove('is-open');
  el.drawer.setAttribute('aria-hidden', 'true');
  el.drawerBackdrop.classList.remove('is-open');
  setTimeout(() => { el.drawerBackdrop.hidden = true; }, 180);
  drawerCitation = null;
}

/* 原文预览：优先用 citation.content（后端已在 citation 帧里带上原文），
 * 缺省时降级提示。不伪造、不额外 fetch。 */
function renderSourcePreview(citation) {
  if (citation.content) {
    return `<div class="drawer-preview">${esc(citation.content)}</div>`;
  }
  return `<div class="drawer-preview drawer-preview-empty">该引用暂未提供原文内容。</div>`;
}

/* ------------------------------------------------------------------ 发送 */
function activeInput() {
  return el.chat.hidden ? el.input : el.inputBottom;
}

async function send(text) {
  if (state.streaming) return;
  if (!text || !text.trim()) return;
  if (state.mode === MODE.KNOWLEDGE && !state.health.knowledge) { toast('知识检索服务未启动'); return; }
  if (state.mode === MODE.FULFILLMENT && !state.health.fulfillment) { toast('工单排障服务未启动'); return; }

  const q = text.trim();
  el.hero.hidden = true;
  el.chat.hidden = false;
  appendUserMessage(q);
  activeInput().value = '';

  state.streaming = true;
  updateSendButtons();

  if (state.mode === MODE.KNOWLEDGE) {
    await runKnowledge(q);
  } else {
    await runFulfillment(q);
  }

  state.streaming = false;
  updateSendButtons();
  state.turnSeq += 1;
}

function updateSendButtons() {
  const disabled = state.streaming;
  el.sendBtn.disabled = disabled;
  el.sendBtnBottom.disabled = disabled;
}

/* ------------------------------------------------------------------ SSE 解析 */
async function streamSSE(url, body, handlers) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    // 非 2xx：尝试读 JSON 错误体
    let msg = `请求失败（HTTP ${res.status}）`;
    try {
      const j = await res.json();
      if (j?.message) msg = j.message;
    } catch {}
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let event = 'message';
  let dataLines = [];

  const dispatch = () => {
    if (dataLines.length === 0) return;
    const data = dataLines.join('\n');
    dataLines = [];
    if (!data.trim()) return;
    let payload;
    try { payload = JSON.parse(data); } catch { return; }
    handlers[event]?.(payload);
    if (handlers['*']) handlers['*'](event, payload);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).replace(/\r$/, '');
      buf = buf.slice(idx + 1);
      if (line === '') { dispatch(); event = 'message'; continue; }
      if (line.startsWith('event:')) { event = line.slice(6).trim(); }
      else if (line.startsWith('data:')) { dataLines.push(line.slice(5).trimStart()); }
    }
  }
  dispatch();
}

/* ------------------------------------------------------------------ 知识检索 */
async function runKnowledge(q) {
  const wrap = createAssistantMessage();
  const status = document.createElement('div');
  status.className = 'msg-status';
  status.innerHTML = `${STATUS_TEXT[MODE.KNOWLEDGE]}<span class="dots"><i></i><i></i><i></i></span>`;
  wrap.appendChild(status);

  const body = document.createElement('div');
  body.className = 'msg-body';
  wrap.appendChild(body);

  const citeMap = new CitationMap();
  let citations = [];
  let usedEids = new Set();
  let html = '';
  let finishReason = null;
  let degraded = [];
  let lastTraceId = null;

  try {
    await streamSSE('/v1/knowledge/answers', {
      query: q,
      subject_ctx: getSubject(),
      session_id: state.sessionId,
      turn_seq: state.turnSeq,
    }, {
      meta: (p) => {
        lastTraceId = p.trace_id;
        if (p.degraded?.length) {
          const b = document.createElement('div');
          b.className = 'degrade-banner';
          b.textContent = '部分能力已降级：' + p.degraded.map(degradeLabel).join('、');
          wrap.insertBefore(b, status);
        }
      },
      citation: (p) => {
        citations = p.citations || [];
        for (const c of citations) citeMap.add(c.eid);
        status.innerHTML = `已找到 ${citations.length} 条依据，正在组织答案…<span class="dots"><i></i><i></i><i></i></span>`;
      },
      delta: (p) => {
        const text = p.text || '';
        (p.eids || []).forEach((e) => usedEids.add(e));
        html += text;
        body.innerHTML = renderText(html, citeMap, true);
        scrollToBottom();
      },
      done: (p) => {
        finishReason = p.finish_reason;
        degraded = p.degraded || [];
        if (p.citations) citations = p.citations;
        if (p.usage) { /* 本期不展示 token 明细 */ }
        status.innerHTML = '';
        status.style.display = 'none';
      },
      input_required: (p) => {
        status.innerHTML = '';
        status.style.display = 'none';
        renderInputRequired(wrap, p, 'knowledge');
      },
      error: (p) => {
        lastTraceId = p.trace_id;
        renderError(wrap, p);
        status.style.display = 'none';
      },
    });
  } catch (err) {
    renderError(wrap, { code: 'UPSTREAM_UNAVAILABLE', message: err.message });
    status.style.display = 'none';
  }

  // 收尾
  status.remove();
  body.innerHTML = renderText(html, citeMap, false);

  if (finishReason) {
    state.currentCiteMap = citeMap;
    state.currentCitations = citations;
    const verdict = verdictForReason(finishReason, degraded, state.activeTaskId);
    if (verdict) wrap.appendChild(buildVerdictBar(verdict.type, verdict.text));
  }

  if (citations.length) {
    wrap.appendChild(buildCitationsBlock(citations, citeMap, usedEids));
  }
  wrap.appendChild(buildActions());

  // 持久化（含引用与正文 HTML，供本地恢复）
  pushMessage({
    role: 'assistant',
    html: body.innerHTML,
    citations,
    citeMap: [...citeMap.order],
    usedEids: [...usedEids],
    verdictBar: finishReason ? verdictForReason(finishReason, degraded, state.activeTaskId) : null,
  });

  scrollToBottom();
}

function degradeLabel(d) {
  const map = {
    rerank: '排序服务', classify: '分类服务', generate: '生成服务',
    judge: '裁判服务', graph: '图谱服务', a2a: 'Agent 服务', retrieval: '检索服务',
  };
  return (map[d] || d) + '超时';
}

function verdictForReason(reason, degraded, taskId) {
  switch (reason) {
    case 'abstained':
      return { type: 'abstained', text: '没有找到足够依据。建议换个问法，或转人工协助。' };
    case 'escalated':
      return { type: 'escalated', text: `已为你转人工${taskId ? '，工单号 ' + taskId : ''}。` };
    case 'degraded':
      return { type: 'degraded', text: '本次' + (degraded || []).map(degradeLabel).join('、') + '，结果可能不够精确。' };
    default:
      return null;
  }
}

function renderError(wrap, p) {
  const div = document.createElement('div');
  div.className = 'error-block';
  div.innerHTML = `${esc(p.message || '出错了')}${p.trace_id ? `<div class="trace" title="点击复制">trace_id: ${esc(p.trace_id)}</div>` : ''}`;
  const trace = div.querySelector('.trace');
  if (trace) trace.addEventListener('click', () => navigator.clipboard.writeText(p.trace_id).then(() => toast('已复制 trace_id')));
  wrap.appendChild(div);
}

/* ------------------------------------------------------------------ 工单排障 */
async function runFulfillment(q) {
  const wrap = createAssistantMessage();
  const status = document.createElement('div');
  status.className = 'msg-status';
  status.innerHTML = `${STATUS_TEXT[MODE.FULFILLMENT]}<span class="dots"><i></i><i></i><i></i></span>`;
  wrap.appendChild(status);

  const body = document.createElement('div');
  body.className = 'msg-body';
  wrap.appendChild(body);

  const timeline = document.createElement('div');
  timeline.className = 'timeline';

  let html = '';
  let taskId = null;

  try {
    await streamSSE(`http://${location.hostname}:8002/v1/fulfillment/tasks`, {
      symptom: q,
      subject_ctx: getSubject(),
      session_id: state.sessionId,
    }, {
      meta: () => {},
      progress: (p) => {
        if (!timeline.parentElement) wrap.appendChild(timeline);
        const item = document.createElement('div');
        item.className = 'tl-item is-active';
        item.innerHTML = `<span class="tl-check">✓</span><span class="tl-note">${esc(p.note || p.node)}</span>`;
        timeline.appendChild(item);
        status.innerHTML = `正在核查：${esc(p.note || p.node)}<span class="dots"><i></i><i></i><i></i></span>`;
        scrollToBottom();
      },
      evidence: (p) => {
        const tag = document.createElement('span');
        const v = p.verdict;
        tag.className = `evidence-tag evidence-${v}`;
        tag.textContent = v === 'support' ? '成立' : v === 'refute' ? '不成立' : '不确定';
        const summary = document.createElement('div');
        summary.className = 'evidence-summary';
        summary.textContent = p.summary || '';
        timeline.appendChild(tag);
        if (p.summary) timeline.appendChild(summary);
        scrollToBottom();
      },
      action_proposal: (p) => {
        renderApprovalCard(wrap, p.approval, taskId);
      },
      delta: (p) => {
        html += p.text || '';
        body.innerHTML = renderText(html, null, true);
        scrollToBottom();
      },
      done: (p) => {
        taskId = p.task_id;
        state.activeTaskId = p.task_id;
        status.innerHTML = '';
        status.style.display = 'none';
      },
      input_required: (p) => {
        taskId = p.task_id;
        state.activeTaskId = p.task_id;
        status.innerHTML = '';
        status.style.display = 'none';
        renderInputRequired(wrap, p, 'fulfillment');
      },
      error: (p) => {
        renderError(wrap, p);
        status.style.display = 'none';
      },
    });
  } catch (err) {
    renderError(wrap, { code: 'UPSTREAM_UNAVAILABLE', message: err.message });
    status.style.display = 'none';
  }

  status.remove();
  if (html) {
    body.innerHTML = renderText(html, null, false);
  }
  wrap.appendChild(buildActions());

  pushMessage({
    role: 'assistant',
    html: body.innerHTML,
    timeline: timeline.innerHTML,
    taskId: state.activeTaskId,
  });

  scrollToBottom();
}

/* 审批卡：action_type 翻成人话，risk_level 色标，批准/拒绝。 */
const ACTION_LABELS = {
  retry_push_carrier: '重新推送承运商',
  cancel_order: '取消订单',
  refund: '退款',
  adjust_inventory: '调整库存',
  notify_customer: '通知客户',
};

function renderApprovalCard(wrap, approval, taskId) {
  const card = document.createElement('div');
  card.className = 'approval-card';
  const label = ACTION_LABELS[approval.action_type] || approval.action_type;
  const risk = approval.risk_level;
  const riskText = risk === 'high' ? '高风险' : risk === 'medium' ? '中风险' : '低风险';
  card.innerHTML = `
    <div class="approval-head">
      <span>建议操作：${esc(label)}</span>
      <span class="risk-tag risk-${esc(risk)}">${riskText}</span>
    </div>
    <div class="approval-actions">
      <button class="btn btn-primary" data-dec="approve">批准</button>
      <button class="btn btn-danger" data-dec="reject">拒绝</button>
    </div>
    <div class="approval-reason">
      <input type="text" placeholder="请输入拒绝原因（必填）" />
      <button class="btn btn-ghost" data-confirm="reject">确认拒绝</button>
    </div>`;

  const approveBtn = card.querySelector('[data-dec="approve"]');
  const rejectBtn = card.querySelector('[data-dec="reject"]');
  const reasonBox = card.querySelector('.approval-reason');
  const confirmBtn = card.querySelector('[data-confirm="reject"]');

  approveBtn.addEventListener('click', () => resumeTask(taskId, {
    approval_id: approval.approval_id,
    decision: 'approve',
    comment: null,
  }, card));

  rejectBtn.addEventListener('click', () => {
    reasonBox.classList.add('is-open');
  });

  confirmBtn.addEventListener('click', () => {
    const input = reasonBox.querySelector('input');
    const comment = input.value.trim();
    if (!comment) { toast('拒绝原因不能为空'); return; }
    resumeTask(taskId, {
      approval_id: approval.approval_id,
      decision: 'reject',
      comment,
    }, card);
  });

  wrap.appendChild(card);
  scrollToBottom();
}

/* input_required：审批卡或澄清表单。 */
function renderInputRequired(wrap, p, mode) {
  if (p.kind === 'approval' && p.approval) {
    renderApprovalCard(wrap, p.approval, p.task_id);
    return;
  }
  if (p.kind === 'clarification') {
    const card = document.createElement('div');
    card.className = 'clarify-card';
    card.innerHTML = `<div class="clarify-q">${esc(p.question || '请补充以下信息')}</div>`;
    const schema = p.schema || {};
    const fields = schema.fields || schema;
    const fieldMap = {};
    const entries = Object.entries(fields || {});
    for (const [name, def] of entries) {
      const type = typeof def === 'string' ? def : def?.type || 'string';
      const wrapEl = document.createElement('div');
      wrapEl.className = 'clarify-field';
      const isEnum = type === 'enum' || Array.isArray(def?.enum);
      if (isEnum) {
        const options = Array.isArray(def?.enum) ? def.enum : (def?.options || []);
        wrapEl.innerHTML = `<label>${esc(name)}</label><select data-field="${esc(name)}">${options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join('')}</select>`;
      } else {
        wrapEl.innerHTML = `<label>${esc(name)}</label><input type="text" data-field="${esc(name)}" />`;
      }
      card.appendChild(wrapEl);
      fieldMap[name] = wrapEl.querySelector('[data-field]');
    }
    const submit = document.createElement('button');
    submit.className = 'btn btn-primary';
    submit.textContent = '提交';
    submit.addEventListener('click', () => {
      const values = {};
      for (const [name, elm] of Object.entries(fieldMap)) values[name] = elm.value;
      resumeTask(p.task_id, null, values, card);
    });
    card.appendChild(submit);
    wrap.appendChild(card);
    scrollToBottom();
  }
}

/* 恢复任务：重传 subject_ctx，接新 SSE 流继续渲染。 */
async function resumeTask(taskId, approval, clarification, cardEl) {
  if (!taskId) { toast('缺少任务 ID'); return; }
  if (cardEl) cardEl.remove();

  const wrap = createAssistantMessage();
  const status = document.createElement('div');
  status.className = 'msg-status';
  status.innerHTML = `<span class="dots"><i></i><i></i><i></i></span>`;
  wrap.appendChild(status);
  const body = document.createElement('div');
  body.className = 'msg-body';
  wrap.appendChild(body);
  const timeline = document.createElement('div');
  timeline.className = 'timeline';

  const reqBody = {
    task_id: taskId,
    subject_ctx: getSubject(),
    last_event_id: state.lastEventId || undefined,
  };
  if (approval) reqBody.approval = approval;
  if (clarification) reqBody.clarification = { fields: clarification };

  let html = '';

  try {
    await streamSSE(`http://${location.hostname}:8002/v1/fulfillment/tasks/${taskId}/resume`, reqBody, {
      progress: (p) => {
        if (!timeline.parentElement) wrap.appendChild(timeline);
        const item = document.createElement('div');
        item.className = 'tl-item is-active';
        item.innerHTML = `<span class="tl-check">✓</span><span class="tl-note">${esc(p.note || p.node)}</span>`;
        timeline.appendChild(item);
        scrollToBottom();
      },
      evidence: (p) => {
        const tag = document.createElement('span');
        tag.className = `evidence-tag evidence-${p.verdict}`;
        tag.textContent = p.verdict === 'support' ? '成立' : p.verdict === 'refute' ? '不成立' : '不确定';
        timeline.appendChild(tag);
        scrollToBottom();
      },
      action_proposal: (p) => renderApprovalCard(wrap, p.approval, taskId),
      delta: (p) => { html += p.text || ''; body.innerHTML = renderText(html, null, true); scrollToBottom(); },
      done: (p) => { status.remove(); },
      input_required: (p) => { status.remove(); renderInputRequired(wrap, p, 'fulfillment'); },
      error: (p) => { renderError(wrap, p); status.remove(); },
    });
  } catch (err) {
    renderError(wrap, { code: 'UPSTREAM_UNAVAILABLE', message: err.message });
    status.remove();
  }

  if (html) body.innerHTML = renderText(html, null, false);
  wrap.appendChild(buildActions());
  scrollToBottom();
}

/* ------------------------------------------------------------------ 身份切换 */
function renderIdentityDropdown() {
  el.identityDropdown.innerHTML = '';
  for (const id of IDENTITIES) {
    const opt = document.createElement('button');
    opt.className = 'identity-option' + (id.key === currentIdentity.key ? ' is-active' : '');
    opt.innerHTML = `
      <span class="id-role">${esc(id.role)}</span>
      <span class="id-user">${esc(id.subject.user_id)}</span>
      <span class="id-check">✓</span>`;
    opt.addEventListener('click', () => {
      switchIdentity(id.key);
    });
    el.identityDropdown.appendChild(opt);
  }
}

function switchIdentity(key) {
  const id = IDENTITIES.find((x) => x.key === key);
  if (!id) return;
  currentIdentity.key = key;
  el.identityChipRole.textContent = id.role;
  el.identityChipUser.textContent = id.subject.user_id;
  closeIdentityDropdown();
  renderIdentityDropdown();
  toast(`已切换为「${id.role}」`);
}

function toggleIdentityDropdown() {
  const open = !el.identityDropdown.hidden;
  open ? closeIdentityDropdown() : openIdentityDropdown();
}
function openIdentityDropdown() {
  el.identityDropdown.hidden = false;
  el.identityChip.setAttribute('aria-expanded', 'true');
}
function closeIdentityDropdown() {
  el.identityDropdown.hidden = true;
  el.identityChip.setAttribute('aria-expanded', 'false');
}

/* ------------------------------------------------------------------ 文档入库 */
let uploadSelectedFile = null;

function openUploadModal() {
  el.uploadModal.hidden = false;
  el.uploadStatus.textContent = '';
  el.uploadStatus.className = 'upload-status';
}
function closeUploadModal() {
  el.uploadModal.hidden = true;
  resetUploadFile();
}

function resetUploadFile() {
  uploadSelectedFile = null;
  el.uploadFile.value = '';
  el.uploadFileInfo.hidden = true;
  el.uploadDrop.hidden = false;
  el.uploadSubmit.disabled = true;
  el.uploadStatus.textContent = '';
  el.uploadStatus.className = 'upload-status';
}

function setUploadFile(file) {
  uploadSelectedFile = file;
  el.uploadFileName.textContent = file.name;
  el.uploadFileInfo.hidden = false;
  el.uploadDrop.hidden = true;
  el.uploadSubmit.disabled = false;
}

async function submitUpload() {
  if (!uploadSelectedFile) { toast('请先选择文件'); return; }
  const fd = new FormData();
  fd.append('file', uploadSelectedFile);
  fd.append('child_size', el.chunkChildSize.value);
  fd.append('parent_size', el.chunkParentSize.value);
  fd.append('overlap', el.chunkOverlap.value);
  fd.append('strategy', 'semantic');
  fd.append('acl_mode', el.chunkAcl.value);

  el.uploadSubmit.disabled = true;
  el.uploadStatus.textContent = '正在解析并入库…';
  el.uploadStatus.className = 'upload-status';

  try {
    const res = await fetch('/v1/knowledge/documents', { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      el.uploadStatus.textContent = data.error || `入库失败（HTTP ${res.status}）`;
      el.uploadStatus.className = 'upload-status is-err';
      el.uploadSubmit.disabled = false;
      return;
    }
    el.uploadStatus.textContent = `入库成功：${data.title}（${data.children} 个子块${data.embedded ? '，已向量化' : '，仅 BM25'}）`;
    el.uploadStatus.className = 'upload-status is-ok';
    toast('文档已入库，可开始检索');
    setTimeout(closeUploadModal, 1200);
  } catch (err) {
    el.uploadStatus.textContent = `请求失败：${err.message}`;
    el.uploadStatus.className = 'upload-status is-err';
    el.uploadSubmit.disabled = false;
  }
}

/* ------------------------------------------------------------------ 事件绑定 */
function bindInput(textarea, sendBtn) {
  const sync = () => {
    sendBtn.disabled = state.streaming || !textarea.value.trim();
    // 自动增高
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  };
  textarea.addEventListener('input', sync);
  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!sendBtn.disabled) send(textarea.value);
    }
  });
  return sync;
}

function bindComposer() {
  const syncHero = bindInput(el.input, el.sendBtn);
  const syncChat = bindInput(el.inputBottom, el.sendBtnBottom);
  el.sendBtn.addEventListener('click', () => send(el.input.value));
  el.sendBtnBottom.addEventListener('click', () => send(el.inputBottom.value));

  el.entryKnowledge.addEventListener('click', () => setMode(MODE.KNOWLEDGE));
  el.entryFulfillment.addEventListener('click', () => setMode(MODE.FULFILLMENT));
  // 键盘：Tab 移动，Enter 选中
  [el.entryKnowledge, el.entryFulfillment].forEach((card) => {
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); card.click(); }
    });
  });

  // 移动端侧栏
  el.sidebarToggle.addEventListener('click', () => openSidebarOnMobile());
  el.sidebarCollapse.addEventListener('click', () => document.body.classList.add('sidebar-collapsed'));
  el.sidebarBackdrop.addEventListener('click', () => closeSidebarOnMobile());

  el.newChatBtn.addEventListener('click', newChat);
  el.topbarNewBtn.addEventListener('click', newChat);

  // 抽屉
  el.drawerClose.addEventListener('click', closeDrawer);
  el.drawerBackdrop.addEventListener('click', closeDrawer);
  el.drawerCopy.addEventListener('click', () => {
    if (!drawerCitation) return;
    const c = drawerCitation;
    const parts = [c.title];
    if (c.page_no) parts.push(`第 ${c.page_no} 页`);
    if (c.section_path) parts.push(c.section_path);
    parts.push(c.doc_id);
    navigator.clipboard.writeText(parts.join(' · ')).then(() => toast('已复制引用信息'));
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });

  // 身份菜单
  el.identityChip.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleIdentityDropdown();
  });
  document.addEventListener('click', (e) => {
    if (!el.identityDropdown.hidden && !e.target.closest('#identityMenu')) {
      closeIdentityDropdown();
    }
  });

  // 新增文档弹窗
  el.topbarUploadBtn.addEventListener('click', openUploadModal);
  el.uploadClose.addEventListener('click', closeUploadModal);
  el.uploadCancel.addEventListener('click', closeUploadModal);
  el.uploadModal.addEventListener('click', (e) => {
    if (e.target === el.uploadModal) closeUploadModal();
  });

  el.uploadDrop.addEventListener('click', () => el.uploadFile.click());
  el.uploadDrop.addEventListener('dragover', (e) => {
    e.preventDefault();
    el.uploadDrop.classList.add('is-drag');
  });
  el.uploadDrop.addEventListener('dragleave', () => el.uploadDrop.classList.remove('is-drag'));
  el.uploadDrop.addEventListener('drop', (e) => {
    e.preventDefault();
    el.uploadDrop.classList.remove('is-drag');
    const f = e.dataTransfer.files?.[0];
    if (f) setUploadFile(f);
  });
  el.uploadFile.addEventListener('change', () => {
    const f = el.uploadFile.files?.[0];
    if (f) setUploadFile(f);
  });
  el.uploadFileRemove.addEventListener('click', resetUploadFile);
  el.uploadSubmit.addEventListener('click', submitUpload);
}

function openSidebarOnMobile() {
  if (window.innerWidth <= 1024) {
    el.sidebar.classList.add('is-open');
    el.sidebarBackdrop.hidden = false;
  } else {
    document.body.classList.toggle('sidebar-collapsed');
  }
}
function closeSidebarOnMobile() {
  el.sidebar.classList.remove('is-open');
  el.sidebarBackdrop.hidden = true;
}

/* 正文角标联动（事件委托）：点角标 -> 打开抽屉 + 卡片高亮。 */
function bindSupDelegation() {
  el.messages.addEventListener('click', (e) => {
    const sup = e.target.closest('sup.cite');
    if (!sup) return;
    const eid = sup.dataset.eid;
    const n = state.currentCiteMap?.get(eid);
    const c = state.currentCitations.find((x) => x.eid === eid);
    if (!c) return;
    openDrawer(c, n);
    // 对应卡片加 is-focus
    const cards = $$('.cite-card', el.messages);
    cards.forEach((card) => card.classList.remove('is-focus'));
    // 通过徽标文本匹配编号
    for (const card of cards) {
      const badge = card.querySelector('.cite-badge');
      if (badge && badge.textContent === String(n)) {
        card.classList.add('is-focus');
        card.scrollIntoView({ block: 'nearest' });
      }
    }
  });
}

/* ------------------------------------------------------------------ 初始化 */
async function init() {
  bindComposer();
  bindSupDelegation();
  setMode(MODE.KNOWLEDGE);
  renderIdentityDropdown();

  // 恢复最近会话
  const sessions = loadSessions();
  renderSessionList();
  if (sessions.length > 0) {
    state.sessionId = sessions[0].id;
    const msgs = loadSessionMessages(state.sessionId);
    if (msgs.length) renderMessages(msgs);
  }

  checkHealth();
  setInterval(checkHealth, 30000);
}

init();

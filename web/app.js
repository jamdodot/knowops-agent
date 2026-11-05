// KnowOps 演示页
// 零构建链：原生 ES module，浏览器直接跑。不引前端框架——
// 这个页面的职责是"把已有的接口和产物展示清楚"，加一层框架只会增加讲课成本。

const CONTRACT = '1.0.0';
const API_RUNS = '/v1/eval/runs';
const API_ANSWERS = '/v1/knowledge/answers';

// 排障域是**另一个进程**（:8002），所以这里是绝对地址而不是同源路径。
// 跟着 location.hostname 走，避免"页面在 localhost、接口写 127.0.0.1"时
// 被 CORS 当成两个 origin 拒掉。
const API_OPS = `http://${location.hostname || 'localhost'}:8002`;

//: 演示用身份。生产里 subject_ctx 由 Java 网关按登录态注入，前端拿不到也不该拼。
const PERSONAS = {
  employee: {
    label: '普通员工',
    dept: 'D10',
    ctx: {
      user_id: 'u_1001', account_status: 'active', dept_paths: ['D10', 'D10.3'],
      roles: ['employee'], groups: [], approval_doc_ids: [], subject_epoch: 1,
    },
  },
  payroll: {
    label: '薪酬专员',
    dept: 'D10.9',
    ctx: {
      user_id: 'u_9001', account_status: 'active', dept_paths: ['D10', 'D10.9'],
      roles: ['payroll'], groups: [], approval_doc_ids: [], subject_epoch: 1,
    },
  },
  finance: {
    label: '财务',
    dept: 'D10.3',
    ctx: {
      user_id: 'u_3001', account_status: 'active', dept_paths: ['D10', 'D10.3'],
      roles: ['employee', 'finance'], groups: [], approval_doc_ids: [], subject_epoch: 2,
    },
  },
};

const EXAMPLES = [
  '单笔 6 万元的费用报销要谁审批？',
  '跨部门资金调拨要几级审批？',
  '事假需要提前多久申请？',
  '报销款项审批通过后多久发放？',
  '员工出差住宿标准是多少？',
];

//: finish_reason 的中文说法。三个字段各自有用，不能只显示一个：
//: 徽章文字回答"这次成了没成"，kind 决定颜色，hint 解释"为什么"——
//: 只给英文原值的话，看的人得先知道 degraded 和 abstained 的区别才看得懂。
const FINISH_LABEL = {
  answered: ['已作答', 'ok', '模型给出了带引用的答案'],
  degraded: ['已降级', 'warn', '有环节降级（撞预算或上游不可用），答案仍可用但可能不完整'],
  abstained: ['证据不足', 'warn', '检索到的证据撑不住结论，按拒答处理'],
  escalated: ['已转人工', 'warn', '自动处理失败，已移交人工'],
  error: ['出错', 'bad', '请求失败，没有产出答案'],
};

function finishBadge(reason) {
  const hit = FINISH_LABEL[reason];
  if (hit) return hit;
  // 未知值照原样显示，但要标成异常态：静默翻译成"已作答"会把新状态藏起来
  return [reason || '未知', 'bad', `未登记的状态：${reason}`];
}

// ---------------------------------------------------------------- 工具
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(v >= 0.995 || v === 0 ? 0 : 1)}%`);
const num = (v, digits = 0) => (v == null ? '—' : Number(v).toFixed(digits));
const ms = (v) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`);
const sid = () => `web-${Math.random().toString(36).slice(2, 10)}`;

const EID_RE = /\{([A-Za-z0-9_\u4e00-\u9fff.\-]+)\}/g;

// 极简 markdown：只认段落、换行、加粗。答案本来就是短段落，引第三方渲染器不值。
function renderText(raw, eidIndex, streaming) {
  let out = esc(raw);
  out = out.replace(EID_RE, (_m, eid) => {
    const n = eidIndex.get(eid);
    return `<sup class="eid" data-eid="${esc(eid)}" title="${esc(eid)}">${n ?? '?'}</sup>`;
  });
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  const html = out
    .split(/\n{2,}/)
    .map((p) => (p.trim() ? `<p>${p.replace(/\n/g, '<br>')}</p>` : ''))
    .join('');
  return html || (streaming ? '' : '<div class="empty">（空答案）</div>');
}

// ---------------------------------------------------------------- SSE
// 用 fetch 而不是 EventSource：EventSource 只支持 GET，而问答是 POST + JSON body。
// SSE 帧格式在这里手解：空行分帧，`:` 开头是心跳注释，data 一定是 JSON。
//
// 抽成通用函数是因为排障域也要用同一套解析：两处各写一遍，
// 迟早出现"一处支持多行 data、另一处不支持"的偏差。
async function consumeSSE(resp, onEvent) {
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      msg = body.message || body.code || msg;
    } catch { /* 非 JSON 错误体，保留状态码 */ }
    throw new Error(msg);
  }
  if (!resp.body) throw new Error('浏览器不支持流式响应');

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (block) => {
    if (!block || block.startsWith(':')) return;
    let event = 'message';
    const dataLines = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim();
      else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
    }
    if (!dataLines.length) return;
    let data = {};
    try { data = JSON.parse(dataLines.join('\n')); } catch { /* 留空，交给下游判类型 */ }
    onEvent(event, data);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      dispatch(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}

async function streamAnswer({ query, subject, graph, onEvent, signal }) {
  const resp = await fetch(API_ANSWERS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Contract-Version': CONTRACT },
    body: JSON.stringify({
      query,
      subject_ctx: subject,
      session_id: sid(),
      turn_seq: 1,
      ...(graph ? { graph } : {}),
    }),
    signal,
  });
  await consumeSSE(resp, onEvent);
}

// ---------------------------------------------------------------- 视图切换
function initNav() {
  $$('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b === btn));
      $$('.view').forEach((v) => { v.hidden = v.id !== `view-${btn.dataset.view}`; });
      if (btn.dataset.view === 'eval') loadEvalRuns();
      // 排障域可能在打开页面之后才起，每次进这一页都重探一次
      if (btn.dataset.view === 'ops') checkOpsHealth();
    });
  });
}

//: 左下角只报"服务通不通"，不报图名。
//: 图名是**每次请求**的选项（右上角能切），写死在这里会跟当前选择打架：
//: 切到 Agentic 后左下角还写着"管线式"，看起来像切换没生效。
//: 本次实际用的是哪张图，由指标行的 `graph` 字段负责说。
const DOMAIN_LABEL = { knowledge: '知识域', fulfillment: '排障域' };

async function checkHealth() {
  const state = $('#svc-state');
  const text = $('#svc-text');
  try {
    const r = await fetch('/healthz');
    const d = await r.json();
    state.classList.add('is-ok');
    text.textContent = `${DOMAIN_LABEL[d.domain] || d.domain} · 服务就绪`;
    text.title = `默认图 ${d.graph}`;
  } catch {
    state.classList.add('is-bad');
    text.textContent = '服务未连通';
  }
}

// ---------------------------------------------------------------- 问答
const chat = {
  raw: '', citations: [], eidIndex: new Map(),
  traceId: '', degraded: [], usage: null, finish: null,
  t0: 0, running: false, controller: null,
  //: 本次提问用的控制流。存在 chat 里而不是只读 DOM：请求发出后用户可能又点了切换，
  //: 结果回来时要说清"这条答案是哪张图跑的"。
  graph: 'pipeline', answeredGraph: '',
};

//: 未引用的那几条默认收起来。用户展开过一次后，同一页面内保持展开——
//: 每次作答都重新折叠的话，正在看证据的人会被打断。
let citesExpanded = false;

function paintAnswer(streaming) {
  const body = $('#answer-body');
  body.innerHTML = chat.raw
    ? renderText(chat.raw, chat.eidIndex, streaming)
    : '<div class="empty">输入问题后，答案会逐字出现。</div>';
  body.classList.toggle('cursor-blink', !!streaming);
  if (streaming) body.scrollTop = body.scrollHeight;
}

let rafPending = false;
function scheduleAnswer() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => { rafPending = false; paintAnswer(chat.running); markUsedCitations(); });
}

function paintCitations() {
  const list = $('#cite-list');
  if (!chat.citations.length) {
    $('#cite-count').textContent = '0';
    list.innerHTML = '<div class="empty" style="padding:10px">本次作答没有引用任何证据。</div>';
    // 上一轮留下的"另有 N 条"必须清掉：它现在指向已经不存在的条目
    const more = $('#cite-more');
    if (more) more.hidden = true;
    return;
  }
  list.innerHTML = chat.citations.map((c, i) => {
    // title 与 doc_id 常常是同一个值（doc_id 就是文件名），拼两个等于同一行重复两遍
    const meta = [
      c.section_path,
      c.page_no ? `p.${c.page_no}` : null,
      c.doc_id !== c.title ? c.doc_id : null,
    ].filter(Boolean).join(' · ');
    return `
    <div class="cite-card" data-cite="${esc(c.eid)}">
      <div class="cite-head">
        <span class="cite-num">${i + 1}</span>
        <span class="cite-title">${esc(c.title || c.doc_id)}</span>
      </div>
      <div class="cite-meta">${esc(meta)}</div>
    </div>`;
  }).join('');
  markUsedCitations();
}

//: 侧栏列出的是"本次发出的引用集合"，正文了不一定全用到。
//: 不标出来的话，观众会觉得"6 条引用但正文只提到 2 个"是渲染 bug。
//: 但一屏 5 条里 3 条灰着，真正被引用的两条会被淹没——所以默认折叠未引用的。
function markUsedCitations() {
  const used = new Set((chat.raw.match(EID_RE) || []).map((m) => m.slice(1, -1)));
  let unused = 0;
  $$('.cite-card').forEach((card) => {
    const isUnused = !used.has(card.dataset.cite);
    card.classList.toggle('is-unused', isUnused);
    if (isUnused) unused += 1;
  });
  if (chat.citations.length) {
    $('#cite-count').textContent = `${used.size}/${chat.citations.length}`;
  }
  const more = $('#cite-more');
  if (!more) return;
  const panel = $('.cite-panel');
  panel?.classList.toggle('is-collapsed', !citesExpanded);
  more.hidden = unused === 0;
  if (unused) {
    more.textContent = citesExpanded
      ? `收起 ${unused} 条未被正文引用的证据`
      : `另有 ${unused} 条相关证据未被正文引用 · 展开`;
  }
  // 正文还没到的时候（只有 citation 帧）不显示这个按钮：
  // 此刻所有条目都"未引用"，数字会在流式过程中一路跳，读起来像出错。
  if (!chat.raw) more.hidden = true;
}

function focusCitation(eid) {
  $$('.cite-card').forEach((el) => el.classList.toggle('is-focus', el.dataset.cite === eid));
  const target = $(`.cite-card[data-cite="${CSS.escape(eid)}"]`);
  if (target) target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function setBadge(text, kind, hint) {
  const el = $('#finish-badge');
  el.hidden = !text;
  el.textContent = text || '';
  el.className = `badge ${kind || ''}`.trim();
  el.title = hint || '';
}

function paintMetrics() {
  const el = $('#metrics');
  if (!chat.usage && !chat.finish) { el.innerHTML = ''; return; }
  const u = chat.usage || {};
  const tokens = (u.input_tokens || 0) + (u.output_tokens || 0);
  const elapsed = chat.t0 ? performance.now() - chat.t0 : 0;
  const parts = [
    `<span><span class="k">tokens</span> ${tokens || '—'}</span>`,
    `<span><span class="k">steps</span> ${u.steps ?? '—'}</span>`,
    `<span><span class="k">elapsed</span> ${ms(elapsed)}</span>`,
  ];
  // 成本恒为 0 时干脆不显示：`$0.00000` 不是"很便宜"，是"这格没配价目表"，
  // 摆在演示页上只会让人以为算错了。
  if (u.cost_usd) parts.push(`<span><span class="k">cost</span> $${u.cost_usd.toFixed(4)}</span>`);
  if (chat.answeredGraph) parts.push(`<span><span class="k">graph</span> ${esc(chat.answeredGraph)}</span>`);
  if (chat.traceId) parts.push(`<span><span class="k">trace</span> ${esc(chat.traceId.slice(0, 12))}</span>`);
  el.innerHTML = parts.join('');
}

function paintNotice() {
  const el = $('#chat-notice');
  const items = chat.degraded || [];
  if (!items.length) { el.hidden = true; return; }
  el.hidden = false;
  el.className = 'notice';
  el.textContent = `本次结果有降级：${items.join('、')} —— 结果可能不如完整链路准确。`;
}

async function submitAsk(query) {
  if (chat.running || !query.trim()) return;
  chat.controller?.abort();
  chat.controller = new AbortController();
  Object.assign(chat, {
    raw: '', citations: [], eidIndex: new Map(), traceId: '',
    degraded: [], usage: null, finish: null, t0: performance.now(), running: true,
    answeredGraph: '',
  });

  const btn = $('#ask-btn');
  btn.disabled = true;
  btn.textContent = '生成中';
  setSegDisabled(true);
  // 秒表：Agentic 要跑 60 秒以上，只写"生成中"的话用户会以为页面死了。
  // 用 setInterval 而不是 requestAnimationFrame——这里只需要"每秒提醒一次"，不需要跟帧。
  const tick = setInterval(() => {
    const s = Math.max(0, Math.round((performance.now() - chat.t0) / 1000));
    btn.textContent = `生成中 ${s}s`;
  }, 1000);
  setBadge('', '');
  $('#chat-notice').hidden = true;
  paintCitations();
  paintAnswer(true);

  const personaKey = $('#identity').value;
  const persona = PERSONAS[personaKey] || PERSONAS.employee;

  try {
    await streamAnswer({
      query,
      subject: persona.ctx,
      graph: chat.graph,
      signal: chat.controller.signal,
      onEvent: (event, data) => {
        if (event === 'meta') {
          chat.traceId = data.trace_id || '';
          chat.degraded = data.degraded || [];
          paintNotice();
        } else if (event === 'citation') {
          chat.citations = data.citations || [];
          chat.eidIndex = new Map(chat.citations.map((c, i) => [c.eid, i + 1]));
          paintCitations();
          scheduleAnswer();
        } else if (event === 'delta') {
          chat.raw += data.text || '';
          scheduleAnswer();
        } else if (event === 'done') {
          chat.finish = data.finish_reason || '';
          chat.usage = data.usage || {};
          if (data.degraded) { chat.degraded = data.degraded; paintNotice(); }
          if (data.citations && !chat.citations.length) {
            chat.citations = data.citations;
            chat.eidIndex = new Map(chat.citations.map((c, i) => [c.eid, i + 1]));
            paintCitations();
          }
        } else if (event === 'error') {
          const e = data.error || data;
          throw new Error(e.message || e.code || '服务端错误');
        }
      },
    });
    chat.running = false;
    paintAnswer(false);
    chat.answeredGraph = chat.graph;
    const reason = chat.finish || '';
    if (reason) setBadge(...finishBadge(reason));
  } catch (err) {
    chat.running = false;
    paintAnswer(false);
    if (err.name === 'AbortError') return;
    setBadge(...FINISH_LABEL.error);
    const el = $('#chat-notice');
    el.hidden = false;
    el.className = 'notice danger';
    el.textContent = `请求失败：${err.message}`;
  } finally {
    chat.running = false;
    clearInterval(tick);
    btn.disabled = false;
    btn.textContent = '发送';
    setSegDisabled(false);
    paintMetrics();
  }
}

//: 两个控制流的代价差得太远（实测 4s vs 40s），切换时必须把这件事说出来，
//: 否则用户会以为页面卡死；更糟的是撞上 deadline 后看到"证据不足"，
//: 从而得出"Agentic 更差"的错误结论——那是预算不够，不是能力不够。
const GRAPH_HINT = {
  pipeline: '',
  agentic: 'Agentic 会多跑意图分析、计划、证据评级与改写回环：同一道题通常 60~95 秒，管线式 5~10 秒。'
    + '慢的这几步换回来的是可解释的判定过程，不是更高的正确率。',
};

function paintGraphHint() {
  const el = $('#graph-hint');
  if (!el) return;
  const text = GRAPH_HINT[chat.graph] || '';
  el.hidden = !text;
  el.textContent = text;
}

function setSegDisabled(disabled) {
  $$('.seg-btn').forEach((b) => { b.disabled = disabled; });
}

function initGraphSeg() {
  $$('.seg-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      // 跑着的时候不让切：切换只影响下一次请求，中途改状态会让"正在生成"的
      // 那条答案标错图名。
      if (chat.running) return;
      chat.graph = btn.dataset.graph;
      $$('.seg-btn').forEach((b) => b.classList.toggle('is-on', b === btn));
      paintGraphHint();
    });
  });
  paintGraphHint();
}

function initChat() {
  $('#hints').innerHTML = EXAMPLES
    .map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`)
    .join('');

  $('#hints').addEventListener('click', (e) => {
    const btn = e.target.closest('.chip');
    if (!btn) return;
    $('#question').value = btn.dataset.q;
    submitAsk(btn.dataset.q);
  });

  $('#ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    submitAsk($('#question').value);
  });

  $('#answer-body').addEventListener('click', (e) => {
    const sup = e.target.closest('sup.eid');
    if (sup) focusCitation(sup.dataset.eid);
  });

  $('#cite-list').addEventListener('click', (e) => {
    const card = e.target.closest('.cite-card');
    if (card) focusCitation(card.dataset.cite);
  });

  $('#cite-more').addEventListener('click', () => {
    citesExpanded = !citesExpanded;
    markUsedCitations();
  });

  initGraphSeg();
}

// ---------------------------------------------------------------- 权限对比
async function runOne(persona) {
  const state = { text: '', citations: [], usage: null, finish: null, error: null };
  try {
    await streamAnswer({
      query: $('#cmp-question').value,
      subject: persona.ctx,
      graph: chat.graph,
      onEvent: (event, data) => {
        if (event === 'citation') state.citations = data.citations || [];
        else if (event === 'delta') state.text += data.text || '';
        else if (event === 'done') { state.finish = data.finish_reason; state.usage = data.usage || {}; }
        else if (event === 'error') { const e = data.error || data; state.error = e.message || e.code; }
      },
    });
  } catch (err) {
    state.error = err.message;
  }
  return state;
}

function cmpColumnHtml(personaKey, state) {
  const p = PERSONAS[personaKey];
  let body;
  let badge = ['', ''];
  if (state.pending) {
    // 初始态和"检索中"必须是独立状态：否则空引用会被渲染成"拦截"，
    // 页面一打开就显示"两个人都被拦住"，读起来像结论。
    body = `<div class="empty" style="padding:28px 16px;text-align:center">${state.running ? '检索中……' : '等待对比'}</div>`;
    badge = [state.running ? '检索中' : '待对比', ''];
  } else if (state.error) {
    body = `<div class="blocked"><div class="blocked-title">请求失败</div><div class="blocked-sub">${esc(state.error)}</div></div>`;
    badge = ['error', 'bad'];
  } else if (!state.citations.length) {
    // 引用为 0 = 没检索到任何这条主体可访问的证据。这是权限生效的直接证据，
    // 不是"模型懒得答"——所以要标成拦截而不是空白。
    body = `<div class="blocked">
        <div class="blocked-title">无可见证据 · 未作答</div>
        <div class="blocked-sub">检索阶段就没有命中该主体可访问的片段</div>
      </div>`;
    badge = ['拦截', 'bad', '检索返回 0 条该主体可访问的证据，因此未作答'];
  } else {
    body = `<div class="cmp-body">${renderText(state.text, new Map(state.citations.map((c, i) => [c.eid, i + 1])), false)}</div>`;
    badge = finishBadge(state.finish || 'answered');
  }

  const u = state.usage || {};
  const tokens = (u.input_tokens || 0) + (u.output_tokens || 0);
  return `
    <article class="panel cmp-col">
      <div class="panel-head">
        <div class="cmp-id">
          <span class="cmp-persona">${esc(p.label)}</span>
          <span class="cmp-dept">${esc(p.dept)} · ${esc(p.ctx.roles.join('/'))}</span>
        </div>
        <span class="badge ${badge[1]}" title="${esc(badge[2] || '')}">${esc(badge[0])}</span>
      </div>
      ${body}
      <div class="cmp-stats">
        <span>引用 ${state.citations.length}</span>
        <span>steps ${u.steps ?? '—'}</span>
        <span>tokens ${tokens || '—'}</span>
      </div>
    </article>`;
}

//: 结论条：把"两列答案不一样长"翻译成一句能直接读的话。
//: 只摆两个答案的话，看的人要自己去数引用条数才看得懂权限起没起作用——
//: 而"谁命中了哪几篇"正好是这个演示最该被一眼看到的东西。
function paintVerdict(keys, results) {
  const el = $('#cmp-verdict');
  if (!el) return;
  const [ra, rb] = results;
  const pa = PERSONAS[keys[0]];
  const pb = PERSONAS[keys[1]];
  const docsOf = (r) => new Set((r?.citations || []).map((c) => c.doc_id || c.title));
  const da = docsOf(ra);
  const db = docsOf(rb);
  const na = (ra?.citations || []).length;
  const nb = (rb?.citations || []).length;
  const onlyA = [...da].filter((d) => !db.has(d));
  const onlyB = [...db].filter((d) => !da.has(d));

  const list = (arr) => {
    const head = arr.slice(0, 3).map((d) => `《${esc(d)}》`).join('、');
    return arr.length > 3 ? `${head} 等 ${arr.length} 篇` : head;
  };

  let head;
  if (na === 0 && nb === 0) {
    head = '两个身份都没检索到可访问证据 —— 这个问题的答案不在任何一方可读的文档里。';
  } else if (onlyA.length === 0 && onlyB.length === 0) {
    head = `两边命中的是同一批文档（各 <span class="v-num">${na}</span> 条）—— 这个问题没有体现权限差异，换个问题试试。`;
  } else {
    const parts = [];
    if (onlyB.length) parts.push(`仅 <b>${esc(pb.label)}</b> 命中 ${list(onlyB)}`);
    if (onlyA.length) parts.push(`仅 <b>${esc(pa.label)}</b> 命中 ${list(onlyA)}`);
    head = `同一问题：${esc(pa.label)} <span class="v-num">${na}</span> 条，`
      + `${esc(pb.label)} <span class="v-num">${nb}</span> 条。${parts.join('；')}。`;
  }

  el.hidden = false;
  el.innerHTML = `${head}
    <div class="v-muted" style="margin-top:4px">同一个问题、同一次检索、同一套排序 —— 差别只来自 SQL 里的权限条件，不来自回答之后的打码。</div>`;
}

async function submitCompare() {
  const grid = $('#cmp-grid');
  const btn = $('#cmp-btn');
  if (!grid || btn.disabled) return;
  const q = $('#cmp-question').value.trim();
  if (!q) return;

  btn.disabled = true;
  btn.textContent = '检索中';
  setSegDisabled(true);
  const keys = ['employee', 'payroll'];
  $('#cmp-verdict').hidden = true;
  grid.innerHTML = keys.map((k) => cmpColumnHtml(k, { pending: true, running: true, citations: [], usage: {} })).join('');

  try {
    const results = await Promise.all(keys.map((k) => runOne(PERSONAS[k])));
    grid.innerHTML = keys.map((k, i) => cmpColumnHtml(k, results[i])).join('');
    paintVerdict(keys, results);
  } finally {
    btn.disabled = false;
    btn.textContent = '并发对比';
    setSegDisabled(false);
  }
}

function initCompare() {
  $('#cmp-form').addEventListener('submit', (e) => { e.preventDefault(); submitCompare(); });
  $$('[data-cmp]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('#cmp-question').value = btn.dataset.cmp;
      submitCompare();
    });
  });
  $('#cmp-grid').innerHTML = ['employee', 'payroll']
    .map((k) => cmpColumnHtml(k, { pending: true, citations: [], usage: {} }))
    .join('');
}

// ---------------------------------------------------------------- 评测看板
let evalLoaded = false;
let retrievalGroups = [];

async function jsonOrThrow(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

//: 检索层一次跑批产出两个子键（rerank0 / rerank1），按 tag 归组后并排对比。
//: 单个检索引擎的绝对分数没什么可看的，开关前后的差值才是结论。
function groupRetrieval(items) {
  const map = new Map();
  for (const it of items) {
    if (!map.has(it.tag)) map.set(it.tag, []);
    map.get(it.tag).push(it);
  }
  return Array.from(map.entries())
    .filter(([, list]) => list.length >= 2)
    .map(([tag, list]) => ({ tag, items: [...list].sort((a, b) => a.key.localeCompare(b.key)) }));
}

const rerankLabel = (key) =>
  key.endsWith('rerank0') ? 'rerank 关' : key.endsWith('rerank1') ? 'rerank 开' : key.split('__').pop();

function delta(value, base, kind) {
  if (value == null || base == null) return '—';
  const d = value - base;
  if (Math.abs(d) < 1e-9) return '0';
  const sign = d > 0 ? '+' : '';
  if (kind === 'ms') return `${sign}${Math.round(d)}ms`;
  return `${sign}${d.toFixed(4)}`;
}

function renderRetrievalTable(g) {
  const [off, on] = g.items;
  const a = off.summary.overall || {};
  const b = on.summary.overall || {};
  const rows = [
    ['Recall@10', num(a['recall@10'], 4), num(b['recall@10'], 4), '—'],
    ['MRR@10', num(a['mrr@10'], 4), num(b['mrr@10'], 4), delta(b['mrr@10'], a['mrr@10'])],
    ['nDCG@10', num(a['ndcg@10'], 4), num(b['ndcg@10'], 4), delta(b['ndcg@10'], a['ndcg@10'])],
    ['p50 延迟', ms(a.p50_ms), ms(b.p50_ms), delta(b.p50_ms, a.p50_ms, 'ms')],
  ].map(([k, v1, v2, d]) => `<tr>
      <td>${esc(k)}</td>
      <td class="num">${v1}</td>
      <td class="num">${v2}</td>
      <td class="num">${esc(d)}</td>
    </tr>`).join('');

  return `
    <div class="panel" style="margin-bottom:14px">
      <div class="panel-head">
        <span>检索层 · ${esc(g.tag)}</span>
        <span class="count">n=${a.n ?? 0} 有金标题</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>指标</th><th class="num">${esc(rerankLabel(off.key))}</th><th class="num">${esc(rerankLabel(on.key))}</th><th class="num">变化</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
}

//: 只有最新一批是"当前结论"，旧批是"过程"。
//: 五张同结构的表并排堆着，看的人得先分辨哪张才算数——这不算信息，算噪音。
function renderRetrievalBlock() {
  if (!retrievalGroups.length) return '';
  const [latest, ...rest] = retrievalGroups;
  const older = rest.length
    ? `<details class="older">
        <summary>更早的检索批（${rest.length}）— 已被上面这批取代，仅留档</summary>
        ${rest.map(renderRetrievalTable).join('')}
      </details>`
    : '';
  return renderRetrievalTable(latest) + older;
}

async function loadEvalRuns(force) {
  if (evalLoaded && !force) return;
  const sel = $('#run-select');
  try {
    const data = await jsonOrThrow(API_RUNS);
    const all = data.runs || [];
    const e2e = all.filter((r) => r.kind === 'e2e');
    retrievalGroups = groupRetrieval(all.filter((r) => r.kind === 'retrieval'));

    if (!e2e.length) {
      $('#eval-content').innerHTML = '<div class="empty muted">eval/runs 下还没有端到端跑批结果。</div>';
      sel.innerHTML = '';
      return;
    }
    evalLoaded = true;
    sel.innerHTML = e2e
      .map((r) => `<option value="${esc(r.key)}">${esc(r.key)} · ${esc((r.created_at || '').slice(0, 16).replace('T', ' '))}</option>`)
      .join('');
    // 默认落在 agentic 那档：它有失败 case、有成本对比，比 100% 的基线更能说明问题
    const prefer = e2e.find((r) => r.key.includes('agentic')) || e2e[0];
    sel.value = prefer.key;
    await renderRun(prefer.key);
  } catch (err) {
    $('#eval-content').innerHTML = `<div class="notice danger">读取评测结果失败：${esc(err.message)}</div>`;
  }
}

function kpiHtml(label, value, unit, kind) {
  return `<div class="kpi ${kind || ''}">
      <div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value">${value}${unit ? `<small>${esc(unit)}</small>` : ''}</div>
    </div>`;
}

async function renderRun(tag) {
  const content = $('#eval-content');
  content.innerHTML = '<div class="empty muted">加载中……</div>';
  let doc;
  try {
    doc = await jsonOrThrow(`${API_RUNS}/${encodeURIComponent(tag)}`);
  } catch (err) {
    content.innerHTML = `<div class="notice danger">加载 ${esc(tag)} 失败：${esc(err.message)}</div>`;
    return;
  }

  const s = doc.summary || {};
  const cases = doc.cases || [];
  const g = s.golden || {};
  const na = s.no_answer || {};
  const un = s.unauthorized || {};
  const cost = s.cost || {};
  const lat = s.latency || {};

  $('#run-meta').textContent =
    `${doc.git_sha || '—'} · graph=${(doc.config || {}).graph || '—'} · n=${cases.length}`;

  const kpis = [
    kpiHtml('golden 正确率', pct(g.accuracy), `n=${g.n ?? 0}`, g.accuracy >= 0.9 ? 'good' : ''),
    kpiHtml('硬拒答率', pct(na.hard_abstain_rate), `n=${na.n ?? 0}`, ''),
    kpiHtml('越权泄漏', pct(un.leak_rate), `n=${un.n ?? 0}`, un.leak_rate > 0 ? 'bad' : 'good'),
    kpiHtml('每问 tokens', num(cost.avg_tokens_per_case), '', ''),
    kpiHtml('平均步数', num(cost.avg_steps, 2), '', ''),
    kpiHtml('p50 / p95', `${ms(lat.p50_ms)}`, ms(lat.p95_ms), ''),
  ].join('');

  const dims = Object.entries(g.by_query_type || {})
    .map(([k, v]) => `<tr>
        <td>${esc(k)}</td>
        <td class="num">${v.n}</td>
        <td class="num">${pct(v.accuracy)}</td>
      </tr>`).join('');

  const fails = cases.filter((c) => c.correct === false);
  const failHtml = fails.length
    ? fails.map((c, i) => `
      <div class="fail-item">
        <div class="fail-head" data-fail="${i}">
          <span class="fail-id">${esc(c.id)}</span>
          <span class="fail-q">${esc(c.query)}</span>
          <span class="badge bad">${esc(c.finish_reason || '?')}</span>
        </div>
        <div class="fail-body" data-failbody="${i}" hidden>
          <div class="muted">归因 ${esc(c.attribute || '—')} · 引用 ${c.n_citations ?? 0} · 数据集 ${esc(c.dataset || '—')}</div>
          <pre>${esc(c.answer || '（无答案）')}</pre>
        </div>
      </div>`).join('')
    : '<div class="empty muted" style="padding:14px">这一档没有失败 case。</div>';

  content.innerHTML = `
    <div class="kpi-row">${kpis}</div>

    <div class="panel" style="margin-bottom:14px">
      <div class="panel-head"><span>分维度（golden）</span><span class="count">${g.n ?? 0} 条</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>query_type</th><th class="num">n</th><th class="num">正确率</th></tr></thead>
          <tbody>${dims || '<tr><td colspan="3" class="muted">无数据</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span>失败 case</span><span class="count">${fails.length}</span></div>
      <div class="fail-list">${failHtml}</div>
    </div>

    ${renderRetrievalBlock()}`;

  $$('[data-fail]', content).forEach((head) => {
    head.addEventListener('click', () => {
      const body = $(`[data-failbody="${head.dataset.fail}"]`, content);
      if (body) body.hidden = !body.hidden;
    });
  });
}

function initEval() {
  $('#run-select').addEventListener('change', (e) => renderRun(e.target.value));
  $('#run-reload').addEventListener('click', () => { evalLoaded = false; loadEvalRuns(true); });
}

// ---------------------------------------------------------------- 订单排障
// 这一块和知识问答的本质差别只有一句：**断流 ≠ 任务失败**。
// 写动作之前要人工审批，审批可能等很久，HTTP 长连接撑不住，所以流会正常关闭、
// 任务停在 input_required，恢复是**新的一条流**（POST .../resume）。
// 界面上必须把这件事说出来，否则"流断了"会被当成"跑挂了"。

const NODE_LABEL = {
  analyze_symptom: '分析症状',
  clarify: '追问补充信息',
  build_hypotheses: '生成假设',
  gather_evidence: '逐条取证',
  evaluate: '评估假设',
  refine: '调整假设',
  compose_diagnosis: '汇总结论',
  propose_action: '提议动作',
  human_approve: '等待人工审批',
  escalate: '转人工',
};

//: verdict 只决定颜色，不改变文案：support/refute/irrelevant 是判定口径，
//: 翻成"支持/否证/无关"已经是原意，再润色就会失真。
const VERDICT_LABEL = {
  support: ['支持', 'ok'],
  refute: ['否证', 'bad'],
  irrelevant: ['无关', 'mute'],
};

const OPS_STATE_LABEL = {
  submitted: ['已提交', 'warn', '任务已创建，等待开始'],
  working: ['诊断中', 'warn', '图正在执行'],
  input_required: ['待你决定', 'warn', '写动作之前必须人工审批；也可能是症状信息不足需要补充'],
  completed: ['已完成', 'ok', '诊断结束，结论可用'],
  failed: ['失败', 'bad', '任务失败或按预算中止'],
  canceled: ['已取消', 'bad', '任务被显式取消'],
};

//: 根因是模型自由生成的字符串，没有枚举约束——所以未知值必须原样显示。
//: 强行翻译成"未知根因"会把新出现的根因类型藏起来，这在排障场景里是致命的。
const ROOT_CAUSE_LABEL = {
  carrier_stuck: '承运商滞留',
  carrier_delay: '承运商延误',
  out_of_stock: '库存不足',
  address_invalid: '收货地址异常',
  payment_failed: '支付未成功',
  duplicate_outbound: '重复出库',
  rule_misjudge: '规则误判',
};

const OPS_SAMPLES = [
  ['承运商滞留', '订单 O20261100001：客户下单三天了还没收到货，查物流一直停在武汉分拨中心不动'],
  ['缺货未发', '订单 O20261100006：下单两天了还显示待发货，客服只说在协调，客户催得急'],
  ['地址异常', '订单 O20261100011：客户说物流显示派送异常但没收到任何电话或短信'],
  ['扣款未到账', '订单 O20261100016：客户说钱已经从银行卡扣了，但订单一直显示待支付'],
  ['重复出库', '订单 O20261100021：客户收到两个一样的包裹，问是不是发重了'],
  ['赔付误判', '订单 O20261100026：系统提示这单超时该赔付，但承运商说是台风封路，到底要不要赔'],
];

const ops = {
  taskId: '', state: '', finish: '', running: false, controller: null,
  steps: [], evidence: [], approval: null, question: null, schema: null,
  result: null, usage: null, traceId: '', t0: 0, online: false,
  //: 全程累计。一个任务会被审批切成两段流，每段一个 ledger——
  //: 只显示最后一段的 usage，会得到"跑了 4 步却写着 steps 1"这种自相矛盾的数字。
  tokens: 0,
  //: 诊断耗时，不含审批等待。审批可能等人几十分钟，算进耗时就失去了意义。
  elapsed: 0,
};

function accumulateUsage(u) {
  if (!u) return;
  const n = (u.input_tokens || 0) + (u.output_tokens || 0);
  if (n > 0) ops.tokens += n;
}

function opsBadge(state, kind, hint) {
  const el = $('#ops-state');
  el.hidden = !state;
  el.textContent = state || '';
  el.className = `badge ${kind || ''}`.trim();
  el.title = hint || '';
}

function paintOpsState() {
  const hit = OPS_STATE_LABEL[ops.state];
  if (hit) { opsBadge(...hit); return; }
  if (ops.state) opsBadge(ops.state, 'bad', `未登记的任务状态：${ops.state}`);
  else opsBadge('', '', '');
}

function paintOpsMetrics() {
  const el = $('#ops-metrics');
  if (!ops.steps.length && !ops.tokens) { el.innerHTML = ''; return; }
  const elapsed = ops.elapsed + (ops.running && ops.t0 ? performance.now() - ops.t0 : 0);
  const parts = [
    `<span><span class="k">节点</span> ${ops.steps.length}</span>`,
    `<span><span class="k">tokens</span> ${ops.tokens || '—'}</span>`,
    `<span><span class="k">elapsed</span> ${ms(elapsed)}</span>`,
  ];
  if (ops.taskId) parts.push(`<span><span class="k">task</span> ${esc(ops.taskId)}</span>`);
  if (ops.traceId) parts.push(`<span><span class="k">trace</span> ${esc(ops.traceId.slice(0, 12))}</span>`);
  el.innerHTML = parts.join('');
}

function paintOpsTimeline() {
  const el = $('#ops-timeline');
  if (!ops.steps.length && !ops.evidence.length) {
    el.innerHTML = '<div class="empty">提交症状后，节点进度与证据判定会实时出现在这里。</div>';
    return;
  }
  const steps = ops.steps.map((s, i) => `
      <li class="ops-step">
        <span class="ops-idx">${esc(s.step ?? i + 1)}</span>
        <span class="ops-node">${esc(NODE_LABEL[s.node] || s.node || '')}</span>
        ${s.note ? `<span class="ops-note">${esc(s.note)}</span>` : ''}
      </li>`).join('');

  const evid = ops.evidence.map((e) => {
    const [label, kind] = VERDICT_LABEL[e.verdict] || [e.verdict || '—', 'mute'];
    return `<li class="ops-evid">
        <span class="ops-verdict ${kind}">${esc(label)}</span>
        <span class="ops-hyp">${esc(e.hypothesis_id || '')}</span>
        ${e.summary ? `<span class="ops-note">${esc(e.summary)}</span>` : ''}
      </li>`;
  }).join('');

  el.innerHTML = `${
    steps ? `<div class="ops-sec">节点</div><ol class="ops-steps">${steps}</ol>` : ''
  }${
    evid ? `<div class="ops-sec">证据判定</div><ul class="ops-evid-list">${evid}</ul>` : ''
  }`;
  el.scrollTop = el.scrollHeight;
}

function paintOpsResult() {
  const el = $('#ops-result');
  const conf = $('#ops-conf');
  const r = ops.result;

  if (!r || !r.root_cause) {
    el.innerHTML = ops.state === 'completed'
      ? '<div class="empty">任务已完成，但没有给出根因。</div>'
      : '<div class="empty">还没有结论。</div>';
    conf.textContent = '';
    return;
  }

  const label = ROOT_CAUSE_LABEL[r.root_cause] || r.root_cause;
  conf.textContent = r.confidence == null ? '' : `置信度 ${pct(r.confidence)}`;
  el.innerHTML = `
    <div class="ops-root">
      <div class="ops-root-label">根因</div>
      <div class="ops-root-name">${esc(label)}</div>
      ${label === r.root_cause ? '' : `<div class="ops-root-raw">${esc(r.root_cause)}</div>`}
    </div>
    ${r.summary ? `<div class="ops-summary">${renderText(r.summary, new Map(), false)}</div>` : ''}`;
}

function paintOpsApproval() {
  const el = $('#ops-approval');
  if (ops.state !== 'input_required' || (!ops.approval && !ops.question)) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  el.hidden = false;

  if (!ops.approval) {
    // 澄清：按 schema.fields 的属性生成输入框。schema 缺失时退化成单个文本框，
    // 不猜字段名——猜错了后端会 422，用户看到的是一次支付了 token 的失败。
    const props = (ops.schema && ops.schema.properties) || null;
    const fields = props
      ? Object.keys(props).map((k) => `
          <label class="ops-field">
            <span>${esc((props[k] && props[k].description) || k)}</span>
            <input type="text" data-ops-field="${esc(k)}" autocomplete="off">
          </label>`).join('')
      : '<label class="ops-field"><span>补充信息</span><input type="text" data-ops-field="answer" autocomplete="off"></label>';
    el.innerHTML = `
      <div class="ops-ask">
        <div class="ops-ask-title">需要补充信息</div>
        <div class="ops-ask-q">${esc(ops.question || '症状里缺少关键信息')}</div>
        ${fields}
        <button type="button" class="btn" data-ops-act="clarify">提交并继续</button>
      </div>`;
    return;
  }

  const ap = ops.approval;
  const risk = { low: ['低风险', 'ok'], medium: ['中风险', 'warn'], high: ['高风险', 'bad'] }[ap.risk_level]
    || [ap.risk_level || '未知风险', 'warn'];
  el.innerHTML = `
    <div class="ops-ask">
      <div class="ops-ask-title">等待审批 · 写动作前</div>
      <div class="ops-action">
        <code>${esc(ap.action_type)}</code>
        <span class="ops-risk ${risk[1]}">${esc(risk[0])}</span>
      </div>
      ${ap.params ? `<pre class="ops-params">${esc(JSON.stringify(ap.params, null, 2))}</pre>` : ''}
      <input type="text" id="ops-comment" placeholder="拒绝原因（可选，会回灌给模型重新规划）" autocomplete="off">
      <div class="ops-ask-btns">
        <button type="button" class="btn" data-ops-act="approve">通过并执行</button>
        <button type="button" class="btn btn-ghost" data-ops-act="reject">拒绝</button>
      </div>
    </div>`;
}

function paintOpsNotice(text, kind = 'danger') {
  const el = $('#ops-notice');
  el.hidden = !text;
  el.className = `notice ${kind}`;
  el.textContent = text || '';
}

//: 排障域是独立进程。没起来的时候不要让人对着"请求失败"猜——
//: 直接说清是哪个端口、怎么起。
async function checkOpsHealth() {
  const box = $('#ops-svc');
  const text = $('#ops-svc-text');
  try {
    const r = await fetch(`${API_OPS}/healthz`, { signal: AbortSignal.timeout(3000) });
    const d = await r.json();
    ops.online = true;
    box.className = 'ops-svc is-ok';
    text.textContent = `排障域 :8002 · 就绪${d.tasks ? ` · 任务 ${d.tasks}` : ''}`;
  } catch {
    ops.online = false;
    box.className = 'ops-svc is-bad';
    text.textContent = '排障域 :8002 未连通';
    text.title = '另起一个进程：python scripts/kw.py serve-fulfillment';
  }
}

async function fetchOpsTask() {
  if (!ops.taskId) return null;
  try {
    const r = await fetch(`${API_OPS}/v1/fulfillment/tasks/${encodeURIComponent(ops.taskId)}`);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

async function onOpsEvent(event, data) {
  switch (event) {
    case 'start':
      ops.traceId = data.trace_id || ops.traceId;
      paintOpsMetrics();
      break;

    case 'progress':
      ops.steps.push({ step: data.step, node: data.node, note: data.note });
      paintOpsTimeline();
      break;

    case 'evidence':
      ops.evidence.push({ hypothesis_id: data.hypothesis_id, verdict: data.verdict, summary: data.summary });
      paintOpsTimeline();
      break;

    case 'action_proposal':
      ops.approval = data.approval || null;
      break;

    case 'input_required': {
      // 流在这里正常关闭：任务不是失败，是停在等人。
      ops.state = 'input_required';
      ops.taskId = data.task_id || ops.taskId;
      ops.approval = data.approval || ops.approval;
      ops.question = data.question || null;
      ops.schema = data.schema || null;
      paintOpsState();
      paintOpsApproval();
      // input_required 事件不带 usage，得回头查任务才拿得到这一段的消耗
      const paused = await fetchOpsTask();
      if (paused) accumulateUsage(paused.usage);
      paintOpsMetrics();
      break;
    }

    case 'done': {
      ops.finish = data.finish_reason || '';
      ops.usage = data.usage || null;
      accumulateUsage(data.usage);
      ops.taskId = data.task_id || ops.taskId;
      const task = await fetchOpsTask();
      if (task) {
        ops.state = task.state || ops.state;
        ops.result = task.result || null;
      } else {
        ops.state = data.finish_reason === 'answered' ? 'completed' : 'failed';
      }
      ops.approval = null;
      ops.question = null;
      paintOpsState();
      paintOpsApproval();
      paintOpsResult();
      paintOpsMetrics();
      break;
    }

    case 'error':
      ops.state = 'failed';
      paintOpsState();
      paintOpsNotice(`${data.code || 'ERROR'}：${data.message || '请求失败'}`);
      break;

    default:
      break;
  }
}

async function setOpsRunning(running, label) {
  ops.running = running;
  const btn = $('#ops-btn');
  btn.disabled = running;
  btn.textContent = running ? (label || '诊断中') : '开始诊断';
}

//: 一段流结束时把耗时并入累计，t0 归零。
//: 不这么做的话，"提交→等审批 3 分钟→点通过"会把 3 分钟算成诊断耗时。
function closeOpsSegment() {
  if (ops.t0) {
    ops.elapsed += performance.now() - ops.t0;
    ops.t0 = 0;
  }
}

async function submitOps(symptom) {
  const text = String(symptom || '').trim();
  if (!text || ops.running) return;

  Object.assign(ops, {
    taskId: '', state: 'working', finish: '', steps: [], evidence: [],
    approval: null, question: null, schema: null, result: null, usage: null,
    traceId: '', t0: performance.now(), tokens: 0, elapsed: 0,
  });
  ops.controller = new AbortController();
  paintOpsTimeline();
  paintOpsResult();
  paintOpsApproval();
  paintOpsState();
  paintOpsMetrics();
  paintOpsNotice('');
  setOpsRunning(true);

  try {
    const resp = await fetch(`${API_OPS}/v1/fulfillment/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Contract-Version': CONTRACT },
      body: JSON.stringify({
        symptom: text,
        subject_ctx: PERSONAS.employee.ctx,
        session_id: sid(),
      }),
      signal: ops.controller.signal,
    });
    await consumeSSE(resp, onOpsEvent);
  } catch (err) {
    if (err.name === 'AbortError') return;
    ops.state = 'failed';
    paintOpsState();
    const hint = ops.online
      ? `请求失败：${err.message}`
      : '排障域没有响应。它是独立进程，需要另开一个终端跑：python scripts/kw.py serve-fulfillment（:8002）';
    paintOpsNotice(hint);
    checkOpsHealth();
  } finally {
    closeOpsSegment();
    setOpsRunning(false);
    paintOpsMetrics();
  }
}

async function resumeOps(payload) {
  if (!ops.taskId || ops.running) return;
  setOpsRunning(true, '继续中');
  paintOpsNotice('');
  ops.controller = new AbortController();
  ops.t0 = performance.now();

  try {
    const resp = await fetch(`${API_OPS}/v1/fulfillment/tasks/${encodeURIComponent(ops.taskId)}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Contract-Version': CONTRACT },
      // 恢复必须重传 subject_ctx：恢复不是"延续旧权限"，后端会重新校验
      body: JSON.stringify({ task_id: ops.taskId, subject_ctx: PERSONAS.employee.ctx, ...payload }),
      signal: ops.controller.signal,
    });
    // 批准之后任务继续跑，所以这里的状态先回到 working
    ops.state = 'working';
    ops.approval = null;
    ops.question = null;
    paintOpsState();
    paintOpsApproval();
    await consumeSSE(resp, onOpsEvent);
  } catch (err) {
    if (err.name === 'AbortError') return;
    ops.state = 'failed';
    paintOpsState();
    paintOpsNotice(`恢复失败：${err.message}`);
  } finally {
    closeOpsSegment();
    setOpsRunning(false);
    paintOpsMetrics();
  }
}

function initOps() {
  $('#ops-hints').innerHTML = OPS_SAMPLES
    .map(([label, text]) => `<button type="button" class="chip" data-symptom="${esc(text)}" title="${esc(text)}">${esc(label)}</button>`)
    .join('');

  $('#ops-hints').addEventListener('click', (e) => {
    const btn = e.target.closest('.chip');
    if (!btn) return;
    $('#ops-symptom').value = btn.dataset.symptom;
    submitOps(btn.dataset.symptom);
  });

  $('#ops-form').addEventListener('submit', (e) => {
    e.preventDefault();
    submitOps($('#ops-symptom').value);
  });

  $('#ops-approval').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-ops-act]');
    if (!btn) return;

    if (btn.dataset.opsAct === 'approve') {
      // 取值必须是 approved / rejected：契约里是过去分词，写 approve 会被 422 打回来
      resumeOps({ approval: { approval_id: ops.approval.approval_id, decision: 'approved' } });
    } else if (btn.dataset.opsAct === 'reject') {
      resumeOps({
        approval: {
          approval_id: ops.approval.approval_id,
          decision: 'rejected',
          comment: ($('#ops-comment') || {}).value || '',
        },
      });
    } else if (btn.dataset.opsAct === 'clarify') {
      const fields = {};
      $$('[data-ops-field]', $('#ops-approval')).forEach((input) => {
        if (input.value.trim()) fields[input.dataset.opsField] = input.value.trim();
      });
      if (!Object.keys(fields).length) {
        paintOpsNotice('至少填一项再提交', 'warn');
        return;
      }
      resumeOps({ clarification: { fields } });
    }
  });

  checkOpsHealth();
}

// ---------------------------------------------------------------- 启动
initNav();
initChat();
initCompare();
initEval();
initOps();
checkHealth();

// KnowOps 演示页
// 零构建链：原生 ES module，浏览器直接跑。不引前端框架——
// 这个页面的职责是"把已有的接口和产物展示清楚"，加一层框架只会增加讲课成本。

const CONTRACT = '1.0.0';
const API_RUNS = '/v1/eval/runs';
const API_ANSWERS = '/v1/knowledge/answers';

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

// ---------------------------------------------------------------- 视图切换
function initNav() {
  $$('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b === btn));
      $$('.view').forEach((v) => { v.hidden = v.id !== `view-${btn.dataset.view}`; });
      if (btn.dataset.view === 'eval') loadEvalRuns();
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

// ---------------------------------------------------------------- 启动
initNav();
initChat();
initCompare();
initEval();
checkHealth();

/* FinAgent UI — vanilla JS port of FinAgent.dc.html wired to the live API. */
'use strict';

/* ------------------------------------------------------------------ */
/* Utilities                                                          */
/* ------------------------------------------------------------------ */
const esc = (s) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/* Coalesces render() calls that land in the same tick (e.g. a 'log' and a
   'task' WS message for the same LLM call) into one, so the task panel
   doesn't tear down and rebuild twice back-to-back. */
let _renderScheduled = false;
let _afterRenderCbs = [];
function scheduleRender(afterCb) {
  if (afterCb) _afterRenderCbs.push(afterCb);
  if (_renderScheduled) return;
  _renderScheduled = true;
  const flush = () => {
    if (!_renderScheduled) return; // already flushed by the other path below
    _renderScheduled = false;
    render();
    const cbs = _afterRenderCbs; _afterRenderCbs = [];
    cbs.forEach(cb => cb());
  };
  requestAnimationFrame(flush);
  // requestAnimationFrame is paused while the tab is backgrounded, so a
  // status change that lands then (e.g. a task going pending_user and
  // re-enabling the reply box) would otherwise sit un-rendered — reply
  // input looks "stuck" disabled — until some unrelated repaint happens.
  // This timeout fallback guarantees it catches up regardless of visibility.
  setTimeout(flush, 250);
}

/* Minimal markdown -> HTML for LLM-written text blocks (headings, bold/italic,
   inline code, code fences, lists, links). Escapes first, then only ever
   inserts tags around already-escaped text, so it stays XSS-safe. */
function mdInline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
const _isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(s);
function _splitTableRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'));
}
function _renderTable(header, aligns, rows) {
  const cell = (tag, c, idx) => `<${tag} style="text-align:${aligns[idx] || 'left'}">${mdInline(c ?? '')}</${tag}>`;
  const thead = `<tr>${header.map((c, idx) => cell('th', c, idx)).join('')}</tr>`;
  const tbody = rows.map(r => `<tr>${header.map((_, idx) => cell('td', r[idx], idx)).join('')}</tr>`).join('');
  return `<div class="md-table-wrap"><table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`;
}
function mdToHtml(text) {
  const lines = String(text ?? '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let list = null; // {type, items}
  const flushList = () => {
    if (list) out.push(`<${list.type}>${list.items.map(it => `<li>${mdInline(it)}</li>`).join('')}</${list.type}>`);
    list = null;
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith('```')) {
      flushList();
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) { code.push(lines[i]); i++; }
      i++;
      out.push(`<pre class="md-code"><code>${esc(code.join('\n'))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushList();
      const level = Math.min(h[1].length, 6);
      out.push(`<h${level} class="md-h">${mdInline(h[2])}</h${level}>`);
      i++;
      continue;
    }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      if (!list || list.type !== 'ul') { flushList(); list = { type: 'ul', items: [] }; }
      list.items.push(ul[1]);
      i++;
      continue;
    }
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      if (!list || list.type !== 'ol') { flushList(); list = { type: 'ol', items: [] }; }
      list.items.push(ol[1]);
      i++;
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && _isTableSep(lines[i + 1])) {
      flushList();
      const header = _splitTableRow(line);
      const aligns = _splitTableRow(lines[i + 1]).map(c => {
        const t = c.trim();
        if (/^:-+:$/.test(t)) return 'center';
        if (/^-+:$/.test(t)) return 'right';
        if (/^:-+$/.test(t)) return 'left';
        return '';
      });
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() !== '' && lines[i].includes('|')) {
        rows.push(_splitTableRow(lines[i]));
        i++;
      }
      out.push(_renderTable(header, aligns, rows));
      continue;
    }
    flushList();
    if (line.trim() === '') { i++; continue; }
    const para = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== ''
           && !/^(#{1,6})\s+/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])
           && !/^\s*\d+\.\s+/.test(lines[i]) && !lines[i].trim().startsWith('```')
           && !(lines[i].includes('|') && i + 1 < lines.length && _isTableSep(lines[i + 1]))) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p>${mdInline(para.join(' '))}</p>`);
  }
  flushList();
  return out.join('');
}

function timeAgo(epoch) {
  if (!epoch) return '—';
  const s = Math.max(0, (Date.now() / 1000) - epoch);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
function ageShort(seconds) {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}
function fmtNum(v) {
  if (v == null || v === '') return '';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
}
function fmtDuration(ms) {
  if (ms == null) return '—';
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s % 60)}s`;
}
function fmtCost(v) {
  if (v == null) return '—';
  return v < 0.01 && v > 0 ? '<$0.01' : `$${v.toFixed(2)}`;
}
function abbrev(v) {
  const n = Number(v) || 0;
  const sign = n < 0 ? '-' : '+';
  const a = Math.abs(n);
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}${Math.round(a / 1e3)}K`;
  return `${sign}${Math.round(a)}`;
}

const STATUS_STYLE = {
  queued:   { label: 'QUEUED',   color: '#555',    bg: 'rgba(85,85,85,0.15)',   text: '#888' },
  running:  { label: 'RUNNING',  color: '#22c55e', bg: 'rgba(34,197,94,0.15)',  text: '#22c55e' },
  approval: { label: 'APPROVAL', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)', text: '#f59e0b' },
  pending_user: { label: 'AWAITING REPLY', color: '#a78bfa', bg: 'rgba(167,139,250,0.15)', text: '#a78bfa' },
  complete: { label: 'COMPLETE', color: '#3b9eff', bg: 'rgba(59,158,255,0.15)', text: '#3b9eff' },
  failed:   { label: 'FAILED',   color: '#ef4444', bg: 'rgba(239,68,68,0.15)',  text: '#ef4444' },
  denied:   { label: 'DENIED',   color: '#ef4444', bg: 'rgba(239,68,68,0.15)',  text: '#ef4444' },
};
const FILTER_LABELS = { pending_user: 'Awaiting Reply' };
const AGENT_STATUS_COLOR = { running: '#22c55e', waiting: '#f59e0b', idle: '#555' };

async function api(path, opts) {
  const res = await fetch(path, opts ? {
    method: opts.method || 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  } : undefined);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ------------------------------------------------------------------ */
/* State                                                              */
/* ------------------------------------------------------------------ */
const S = {
  view: 'dashboard',
  overview: null,
  tasks: [],
  taskDetail: null,
  selectedTaskId: null,
  taskFilter: 'all',
  taskSearch: '',
  agents: [],
  sources: null,
  pivot: null,
  profile: null,
  feed: [],
  chat: { sessionId: null, messages: [], pending: false, mode: 'chat' },
  sql: { text: '', source: null, refresh: false, result: null, error: null, running: false },
  modal: null,           // 'newtask' | 'addsource' | {type:'config', name}
  newTask: { description: '', agent: 'Recon Agent', sources: [], reasoningEffort: 'medium', approval: true },
  newSource: { name: '', kind: 'clickhouse', params: {} },
  modifyOpen: false, modifyText: '',
  askText: '',
  error: null,
  copiedFlash: null,
  focusMode: false,
  textScale: Number(localStorage.getItem('finagent_text_scale')) || 1,
};

const VIEWS = {
  dashboard: 'Dashboard', tasks: 'Tasks', agents: 'Agents',
  sources: 'Data Sources', analysis: 'Analysis', query: 'Query', profile: 'My Usage',
};
const NAV = [
  { id: 'dashboard', icon: '◫', label: 'Dashboard' },
  { id: 'tasks', icon: '☰', label: 'Tasks' },
  { id: 'agents', icon: '⚙', label: 'Agents' },
  { id: 'sources', icon: '◉', label: 'Data Sources' },
  { id: 'analysis', icon: '▦', label: 'Analysis' },
  { id: 'query', icon: '💬', label: 'Query' },
  { id: 'profile', icon: '👤', label: 'My Usage' },
];

function applyTextScale() {
  document.documentElement.style.setProperty('--text-scale', S.textScale);
}

/* ------------------------------------------------------------------ */
/* Rendering                                                          */
/* ------------------------------------------------------------------ */
function render() {
  const active = document.activeElement;
  const focusId = active && active.id ? active.id : null;
  const selStart = focusId && active.selectionStart != null ? active.selectionStart : null;

  document.getElementById('app').innerHTML = `
    ${renderSidebar()}
    <div class="main">
      ${renderTopbar()}
      <div class="content" id="content">${renderView()}</div>
    </div>
    ${renderModal()}
  `;

  if (focusId) {
    const el = document.getElementById(focusId);
    if (el) {
      el.focus();
      if (selStart != null && el.setSelectionRange) {
        try { el.setSelectionRange(selStart, selStart); } catch (e) { /* type=number etc. */ }
      }
    }
  }
}

function renderSidebar() {
  const approvalCount = S.tasks.filter(t => t.status === 'approval').length;
  const mini = S.focusMode;
  return `
  <div class="sidebar ${mini ? 'mini' : ''}">
    <div class="logo-row">
      <div class="logo-mark" ${mini ? `onclick="App.toggleFocusMode()" title="Exit focus mode"` : ''}>F</div>
      <div>
        <div class="logo-name">FinAgent</div>
        <div class="logo-sub">FINANCE OPS PLATFORM</div>
      </div>
    </div>
    <div class="nav">
      ${NAV.map(n => `
        <div class="nav-item ${S.view === n.id ? 'active' : ''}" onclick="App.go('${n.id}')" title="${mini ? esc(n.label) : ''}">
          <span class="nav-icon">${n.icon}</span><span>${n.label}</span>
          ${n.id === 'tasks' && approvalCount ? `<span class="nav-badge">${approvalCount}</span>` : ''}
        </div>`).join('')}
    </div>
    <div class="side-agents">
      <div class="side-label">AGENTS</div>
      ${S.agents.map(a => `
        <div class="side-agent" title="${mini ? esc(a.name) : ''}">
          <div class="dot" style="background:${AGENT_STATUS_COLOR[a.status] || '#555'}"></div>
          <span>${esc(a.name)}</span>
        </div>`).join('')}
    </div>
    <div class="user-row">
      <div class="avatar">${esc(initials(S.overview?.user || 'FA'))}</div>
      <div>
        <div class="user-name">${esc(S.overview?.user || '—')}</div>
        <div class="user-role">Finance Ops</div>
      </div>
    </div>
  </div>`;
}
function initials(name) {
  return name.split(/\s+/).map(w => w[0] || '').join('').slice(0, 2).toUpperCase();
}

function renderTopbar() {
  const running = S.agents.filter(a => a.status === 'running').length;
  return `
  <div class="topbar">
    <div class="topbar-left">
      <span class="page-title">${VIEWS[S.view]}</span>
      <div class="kbd-chip" onclick="App.openNewTask()" title="New task">⌘K</div>
    </div>
    <div class="topbar-right">
      <div class="text-scale-ctl" title="Text size">
        <button onclick="App.setTextScale(-0.1)" title="Decrease text size">A−</button>
        <span class="text-scale-pct" onclick="App.setTextScale(0)" title="Reset text size">${Math.round(S.textScale * 100)}%</span>
        <button onclick="App.setTextScale(0.1)" title="Increase text size">A+</button>
      </div>
      ${S.view === 'dashboard' ? `<button class="btn-primary" onclick="App.openNewTask()">+ New Task</button>` : ''}
      <div class="live-agents"><div class="pulse"></div>${running} agent${running === 1 ? '' : 's'} running</div>
    </div>
  </div>`;
}

function renderView() {
  switch (S.view) {
    case 'dashboard': return renderDashboard();
    case 'tasks': return renderTasks();
    case 'agents': return renderAgents();
    case 'sources': return renderSources();
    case 'analysis': return renderAnalysis();
    case 'query': return renderQuery();
    case 'profile': return renderProfile();
    default: return '';
  }
}

/* ---------- Context-window banner (shared: task detail + chat) ---------- */
function renderContextBanner(pct, compacted) {
  const pctLabel = Math.round((pct || 0) * 100);
  return `
  <div class="context-banner">
    ${compacted
      ? `↻ Conversation was compacted to stay within the model's context window.`
      : `⚠ Context ${pctLabel}% full — the agent will auto-compact older turns to stay within the model's window.`}
  </div>`;
}

/* ---------------- Profile (learned usage patterns) ---------------- */
function renderProfile() {
  const p = S.profile;
  if (!p) return `<div class="empty-state">Loading…</div>`;
  return `
  <div class="profile-page">
    <div class="mini-label" style="margin-bottom:8px">LEARNED FROM YOUR ACTIVITY — UPDATED NIGHTLY</div>
    ${p.profile_text ? `
      <div class="task-full-text-body profile-text">${mdToHtml(p.profile_text)}</div>
      <div class="task-created" style="margin-top:10px">Last updated ${timeAgo(new Date(p.updated_at).getTime() / 1000)}</div>
    ` : `
      <div class="empty-state">
        Nothing learned yet — the nightly consolidation job builds this from how you use FinAgent
        (queries asked, sources and agents used, working patterns). Check back after it next runs.
      </div>`}
    ${p.error ? `<div style="color:#ef4444;font-size:12px;margin-top:10px" class="mono">${esc(p.error)}</div>` : ''}
  </div>`;
}

/* ---------------- Dashboard ---------------- */
function renderDashboard() {
  const o = S.overview;
  if (!o) return `<div class="empty-state">Loading…</div>`;
  const st = o.stats;
  const freshness = st.cache_newest_age == null ? '—' : ageShort(st.cache_newest_age);
  const hitRate = (st.cache_hits + st.cache_misses) > 0
    ? Math.round(100 * st.cache_hits / (st.cache_hits + st.cache_misses)) : null;
  const stats = [
    { label: 'ACTIVE AGENTS', value: st.agents_total,
      sub: `● ${st.agents_running} running · ${st.agents_idle} idle`, color: '#22c55e' },
    { label: 'PENDING TASKS', value: st.tasks_pending,
      sub: `● ${st.tasks_need_approval} need approval`, color: '#f59e0b' },
    { label: 'DATA FRESHNESS', value: freshness,
      sub: st.cache_newest_age == null ? 'cache is empty' : `last cache: ${ageShort(st.cache_newest_age)} ago`, color: '#888' },
    { label: 'QUERIES TODAY', value: st.queries_today,
      sub: hitRate == null ? 'no queries yet' : `↑ ${hitRate}% cache hit rate`, color: '#3b9eff' },
  ];
  const approvals = S.tasks.filter(t => t.status === 'approval');
  return `
  <div class="dash">
    <div class="stats-row">
      ${stats.map(s => `
        <div class="stat">
          <div class="stat-label">${s.label}</div>
          <div class="stat-value">${esc(String(s.value))}</div>
          <div class="stat-sub" style="color:${s.color}">${esc(s.sub)}</div>
        </div>`).join('')}
    </div>
    <div class="dash-cols">
      <div class="card">
        <div class="card-head">
          <div style="display:flex;align-items:center;gap:8px">
            <span class="card-title">Approval Queue</span>
            ${approvals.length ? `<span class="count-badge">${approvals.length}</span>` : ''}
          </div>
        </div>
        <div>
          ${approvals.length ? approvals.map(t => {
            const a = t.approval || {};
            const cost = a.estimated_cost != null ? `$${a.estimated_cost.toFixed(2)}` : '—';
            return `
            <div class="queue-item">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
                <div style="flex:1">
                  <div class="queue-title">${esc(t.title)}</div>
                  <div class="queue-meta">${esc(t.agent)} → ${esc(a.source || '?')} · est. ${cost}</div>
                </div>
                ${(a.estimated_cost || 0) >= 0.25 ? `<span class="costly-chip">⚠ COSTLY</span>` : ''}
              </div>
              <div style="display:flex;gap:6px;margin-top:8px">
                <button class="btn-approve" onclick="App.decide('${t.id}','approve')">Approve</button>
                <button class="btn-ghost" onclick="App.decide('${t.id}','deny')">Deny</button>
                <button class="btn-ghost" style="margin-left:auto" onclick="App.goTask('${t.id}', true)">Modify</button>
              </div>
            </div>`;
          }).join('') : `<div class="empty-state">Nothing waiting for approval</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-head">
          <span class="card-title">Task Feed</span>
          <span class="mono" style="font-size:10px;color:#555">Live</span>
        </div>
        <div>
          ${S.feed.length ? S.feed.slice(0, 8).map(f => `
            <div class="feed-item">
              <span class="feed-time">${timeAgo(f.ts)}</span>
              <span style="font-size:13px">${esc(f.icon)}</span>
              <div class="feed-text">${esc(f.text)}</div>
            </div>`).join('') : `<div class="empty-state">No activity yet — create a task</div>`}
        </div>
      </div>
    </div>
    <div class="card src-strip">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div class="card-title">Data Sources</div>
        <div class="mono" style="font-size:10px;color:#555">Cache: ${esc(o.cache_backend || 'ClickHouse')}</div>
      </div>
      <div class="src-grid">
        ${(o.sources || []).map(srcTile).join('')}
      </div>
    </div>
  </div>`;
}
function srcTile(s) {
  const f = freshInfo(s.cache);
  const conn = s.connection ? Object.values(s.connection)[0] || s.kind : s.kind;
  return `
  <div class="src-tile">
    <div class="src-tile-name">${esc(s.name)}</div>
    <div class="src-tile-db">${esc(String(conn)).split('/').pop()}</div>
    <div class="src-tile-fresh" style="color:${f.color}">${f.label}</div>
  </div>`;
}
function freshInfo(cache) {
  if (!cache || cache.newest_age == null) return { color: '#555', label: '○ NOT CACHED' };
  if (cache.fresh) return { color: '#22c55e', label: `● FRESH ${ageShort(cache.newest_age)}` };
  return { color: '#f59e0b', label: `● STALE ${ageShort(cache.newest_age)}` };
}

/* ---------------- Tasks ---------------- */
function renderTasks() {
  const filters = ['all', 'running', 'approval', 'pending_user', 'complete', 'queued'];
  const q = S.taskSearch.trim().toLowerCase();
  const matches = (t) => {
    const statusOk = S.taskFilter === 'all'
      || (S.taskFilter === 'complete' ? ['complete', 'failed', 'denied'].includes(t.status)
          : t.status === S.taskFilter);
    if (!statusOk) return false;
    if (!q) return true;
    const haystack = `${t.title} ${t.description} ${t.agent} ${(t.sources || []).join(' ')}`.toLowerCase();
    return haystack.includes(q);
  };
  const visible = S.tasks.filter(matches);
  const selected = S.taskDetail && visible.some(t => t.id === S.taskDetail.id)
    ? S.taskDetail
    : (S.taskDetail && S.tasks.some(t => t.id === S.taskDetail.id) && S.taskFilter === 'all' ? S.taskDetail : S.taskDetail);
  const mini = S.focusMode;
  return `
  <div class="tasks-wrap">
    <div class="task-list-col ${mini ? 'mini' : ''}">
      <div class="task-search">
        <input id="task-search-input" type="text" placeholder="Search tasks…" value="${esc(S.taskSearch)}"
          oninput="App.setTaskSearch(this.value)">
        ${S.taskSearch ? `<button class="task-search-clear" onclick="App.setTaskSearch('')" title="Clear search">×</button>` : ''}
      </div>
      <div class="task-filters">
        ${filters.map(f => `
          <button class="filter-chip ${S.taskFilter === f ? 'active' : ''}"
            onclick="App.setFilter('${f}')">${FILTER_LABELS[f] || (f[0].toUpperCase() + f.slice(1))}</button>`).join('')}
        <span class="task-count">${visible.length} tasks</span>
      </div>
      <div class="task-list">
        ${visible.length ? visible.map(t => {
          const st = STATUS_STYLE[t.status] || STATUS_STYLE.queued;
          const initials = (t.title || '?').trim().slice(0, 2).toUpperCase();
          return `
          <div class="task-row ${S.selectedTaskId === t.id ? 'selected' : ''}"
               style="border-left-color:${st.color}" onclick="App.goTask('${t.id}')" title="${mini ? esc(t.title) : ''}">
            <span class="task-row-dot" style="background:${st.color}">${esc(initials)}</span>
            <div class="task-row-top">
              <span class="task-row-title">${esc(t.title)}</span>
              <span class="status-chip" style="color:${st.text};background:${st.bg}">${st.label}</span>
            </div>
            <div class="task-row-meta">${esc(t.agent)} · ${esc((t.sources || []).join(', ') || 'default')} · ${timeAgo(t.updated_at)}</div>
          </div>`;
        }).join('') : `<div class="empty-state">${S.tasks.length ? 'No tasks match your search' : 'No tasks — press + New Task'}</div>`}
      </div>
    </div>
    <div class="task-detail">${renderTaskDetail(selected)}</div>
  </div>`;
}

const WAITING_WORDS = ['Working on it…', 'Thinking…', 'Reasoning…', 'Almost there…'];

function renderWaitingAnim() {
  const word = WAITING_WORDS[Math.floor(_waitTick / 2.4) % WAITING_WORDS.length];
  return `
  <div class="waiting-anim">
    <div class="wa-spinner">
      <svg width="16" height="16" viewBox="0 0 16 16">
        <circle cx="8" cy="8" r="6" fill="none" stroke="#3a3a4a" stroke-width="2"></circle>
        <path d="M8,2 A6,6 0 0,1 14,8" fill="none" stroke="var(--amber)" stroke-width="2" stroke-linecap="round"></path>
      </svg>
    </div>
    <span class="wa-text" id="wa-word">${esc(word)}</span>
  </div>`;
}

function renderTaskStats(t) {
  if (!t.started_at) return '';
  const elapsedMs = t.duration_ms != null ? t.duration_ms : (Date.now() - t.started_at * 1000);
  const totalTokens = (t.input_tokens || 0) + (t.output_tokens || 0);
  return `
  <div class="task-stats mono">
    <span id="stat-duration" data-live="${t.duration_ms == null ? '1' : '0'}" data-started="${t.started_at}">${fmtDuration(elapsedMs)}</span>
    <span class="sep">·</span>
    <span>↓ ${fmtNum(t.input_tokens || 0)} / ↑ ${fmtNum(t.output_tokens || 0)} tokens (${fmtNum(totalTokens)} total)</span>
    <span class="sep">·</span>
    <span>~${fmtCost(t.estimated_llm_cost)}</span>
  </div>`;
}

function renderTaskDetail(t) {
  if (!t) return `<div class="empty-state">Select a task</div>`;
  const st = STATUS_STYLE[t.status] || STATUS_STYLE.queued;
  const a = t.approval || {};
  const isWaiting = t.status === 'approval';
  const isActive = ['queued', 'running'].includes(t.status);
  return `
  <div class="task-detail-inner">
    <div class="task-head">
      <div>
        <div class="task-title">${esc(t.title)}</div>
        <div class="task-created">Created ${timeAgo(t.created_at)} by ${esc(t.creator || '—')}</div>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <button class="focus-toggle-btn" onclick="App.toggleFocusMode()" title="${S.focusMode ? 'Exit focus mode' : 'Expand — minimize nav & task list'}">
          ${S.focusMode ? '⤡ Exit Focus' : '⛶ Focus'}
        </button>
        <span class="status-chip lg" style="color:${st.text};background:${st.bg}">${st.label}</span>
      </div>
    </div>

    <div class="task-full-text">
      <div class="task-full-text-head">
        <span class="mini-label">TASK DESCRIPTION</span>
        <button class="copy-btn" onclick="App.copyTaskDescription('${t.id}')">
          ${S.copiedFlash === 'task-desc-' + t.id ? '✓ Copied' : '📋 Copy'}
        </button>
      </div>
      <div class="task-full-text-body">${esc(t.description)}</div>
    </div>

    ${t.trace_id ? `
    <div class="task-trace-row">
      <span class="mini-label">TRACE</span>
      <code class="task-trace-id">${esc(t.trace_id)}</code>
      <button class="copy-btn" onclick="App.copyTraceId('${t.id}')" title="Copy trace ID">
        ${S.copiedFlash === 'task-trace-' + t.id ? '✓' : '📋'}
      </button>
      <a class="copy-btn" href="${esc(t.trace_url || '#')}" target="_blank" rel="noopener noreferrer" title="Open trace in SigNoz">🔗</a>
    </div>` : ''}

    ${renderTaskStats(t)}
    ${(t.context_pct || 0) >= 0.75 ? renderContextBanner(t.context_pct, false) : ''}
    ${isActive ? renderWaitingAnim() : ''}

    ${isWaiting ? `
    <div class="approval-banner">
      <div class="approval-title">Agent wants to run:</div>
      <div class="approval-query">${esc(a.query || '')}</div>
      <div class="approval-est">Est. ${a.estimated_rows != null ? fmtNum(a.estimated_rows) : '?'} rows · ~$${(a.estimated_cost ?? 0).toFixed(2)} · source: ${esc(a.source || '?')}</div>
      ${S.modifyOpen ? `
        <textarea id="modify-sql" class="modify-area"
          oninput="S.modifyText = this.value">${esc(S.modifyText)}</textarea>
        <div style="display:flex;gap:6px">
          <button class="btn-approve-lg" onclick="App.decide('${t.id}','modify')">Run Modified</button>
          <button class="btn-outline" onclick="App.toggleModify(false)">Cancel</button>
        </div>` : `
        <div style="display:flex;gap:6px">
          <button class="btn-approve-lg" onclick="App.decide('${t.id}','approve')">Approve</button>
          <button class="btn-outline" onclick="App.decide('${t.id}','deny')">Deny</button>
          <button class="btn-outline" onclick="App.toggleModify(true, ${JSON.stringify(a.query || '').replace(/"/g, '&quot;')})">Modify Query</button>
        </div>`}
    </div>` : ''}

    <div>
      <div class="mini-label" style="margin-bottom:8px">AGENT LOG</div>
      <div class="log-box" id="log-box">
        ${(t.logs || []).map(l => `
          <div class="log-line">
            <span class="log-time">${esc(l.time)}</span>
            <span style="color:${esc(l.color || '#666')}">${esc(l.text)}</span>
          </div>`).join('') || `<div class="log-line"><span style="color:#555">Waiting…</span></div>`}
      </div>
    </div>

    ${(t.blocks || []).length ? `
    <div>
      <div class="mini-label" style="margin-bottom:8px">RESULT</div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${t.blocks.map(b => renderBlock(b, 'agent')).join('')}
      </div>
    </div>` : ''}

    ${(() => {
      const busy = ['queued', 'running', 'approval'].includes(t.status);
      return `
    <div class="ask-bar ${busy ? 'disabled' : ''}">
      <span style="font-size:15px">💬</span>
      <textarea id="ask-input" class="ask-input" rows="1"
        placeholder="${busy ? 'Agent is working — you can reply once it finishes…' : 'Ask about this task or its data… (Shift+Enter for a new line)'}"
        oninput="S.askText = this.value; this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 90) + 'px'"
        onkeydown="if(event.key==='Enter' && !event.shiftKey){ event.preventDefault(); App.askTask('${t.id}'); }"
        ${busy ? 'disabled' : ''}>${esc(S.askText)}</textarea>
      <button class="ask-send-btn" onclick="App.askTask('${t.id}')" ${busy ? 'disabled' : ''} title="Send">Send ➤</button>
    </div>`;
    })()}
  </div>`;
}

/* ---------------- Agents ---------------- */
function renderAgents() {
  if (!S.agents.length) return `<div class="empty-state">Loading…</div>`;
  return `
  <div class="agents-grid">
    ${S.agents.map(agent => {
      const color = AGENT_STATUS_COLOR[agent.status] || '#555';
      return `
      <div class="agent-card">
        <div class="agent-card-top">
          <div style="display:flex;align-items:center;gap:8px">
            <div class="agent-avatar" style="background:${esc(agent.bg)}">${esc(agent.icon)}</div>
            <div>
              <div class="agent-name">${esc(agent.name)}</div>
              <div class="agent-type">${esc(agent.type)}</div>
            </div>
          </div>
          <div class="agent-status" style="color:${color}">
            <div class="dot" style="background:${color}"></div>${agent.status.toUpperCase()}
          </div>
        </div>
        <div class="mini-label" style="margin-bottom:4px">DATA SOURCES</div>
        <div class="chip-row" style="margin-bottom:10px">
          ${(agent.sources || []).map(s => `<span class="src-chip">${esc(s)}</span>`).join('')}
        </div>
        ${agent.current_task_title ? `
        <div class="agent-task-box">
          <div class="agent-task-text">${esc(agent.current_task_title)}</div>
          ${agent.progress ? `
          <div class="progress-track">
            <div class="progress-fill" style="background:${color};width:${agent.progress}%"></div>
          </div>` : ''}
        </div>` : ''}
        <div class="agent-card-foot">
          <span>${agent.tasks_completed} tasks today</span>
          <span>$${(agent.cost || 0).toFixed(2)} spent</span>
        </div>
      </div>`;
    }).join('')}
  </div>`;
}

/* ---------------- Sources ---------------- */
function renderSources() {
  if (!S.sources) return `<div class="empty-state">Loading…</div>`;
  const list = S.sources.sources || [];
  return `
  <div class="sources-wrap">
    <div class="sources-head">
      <div class="mono" style="font-size:10px;color:#555">
        ${list.length} connected sources · cache: ${esc(S.overview?.cache_backend || 'ClickHouse')}
      </div>
      <button class="btn-primary" onclick="App.openAddSource()">+ Add Source</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      ${list.map(s => {
        const f = freshInfo(s.cache);
        const conn = s.connection
          ? Object.entries(s.connection).map(([k, v]) => `${v}`).join(' · ')
          : '';
        const cacheRows = s.cache ? `${s.cache.tables} tables · ${fmtNum(s.cache.rows)} rows cached` : 'nothing cached yet';
        return `
        <div class="source-row">
          <div class="source-icon">${esc(s.icon || '◉')}</div>
          <div style="flex:1">
            <div class="source-name">${esc(s.name)}${s.is_default ? ' <span class="mono" style="font-size:9px;color:#555">DEFAULT</span>' : ''}</div>
            <div class="source-conn">${esc(s.kind)} · ${esc(conn)}${s.connected ? '' : ` · <span style="color:#ef4444">UNREACHABLE</span>`}</div>
          </div>
          <div class="source-cache">
            <div class="source-fresh" style="color:${f.color}">${f.label}${s.cache ? ' ago' : ''}</div>
            <div class="source-tables">${cacheRows}</div>
          </div>
          <div style="display:flex;gap:6px;margin-left:8px">
            <button class="btn-mono" onclick="App.refreshSource('${esc(s.name)}')">Refresh</button>
            <button class="btn-mono" onclick="App.openConfig('${esc(s.name)}')">Config</button>
          </div>
        </div>`;
      }).join('')}
    </div>
  </div>`;
}

/* ---------------- Analysis ---------------- */
function renderAnalysis() {
  const p = S.pivot;
  if (!p) return `<div class="empty-state">Loading pivot from ClickHouse cache…</div>`;
  if (p.error) return `<div class="empty-state">Pivot failed: ${esc(p.error)}</div>`;

  const rows = p.rows.map(r => ({
    account: r.account, q1: r.q1, q2: r.q2, bold: false,
  }));
  // insert Gross Profit after COGS (5100) and Net Income at the end, like the mock
  const gp = p.summary.find(s => s.account === 'Gross Profit');
  const ni = p.summary.find(s => s.account === 'Net Income');
  const cogsIdx = rows.findIndex(r => r.account.startsWith('5100'));
  const display = [...rows];
  if (gp && cogsIdx >= 0) display.splice(cogsIdx + 1, 0, { ...gp, bold: true });
  if (ni) display.push({ ...ni, bold: true });

  const pivotRow = (r) => {
    const variance = r.q2 - r.q1;
    const pct = r.q1 !== 0 ? (variance / Math.abs(r.q1)) * 100 : null;
    const isRevenue = r.account.startsWith('4') || r.account === 'Gross Profit' || r.account === 'Net Income';
    const bad = isRevenue ? variance < 0 : variance > 0;
    const varColor = variance === 0 ? '#555' : (bad ? '#ef4444' : '#22c55e');
    return `
    <tr class="${r.bold ? 'rollup' : ''}">
      <td style="font-weight:${r.bold ? 700 : 400}">${esc(r.account)}</td>
      <td>${fmtNum(r.q1)}</td>
      <td>${fmtNum(r.q2)}</td>
      <td style="color:${varColor};font-weight:${r.bold ? 700 : 400}">${variance > 0 ? '+' : ''}${fmtNum(variance)}</td>
      <td style="color:${varColor};font-weight:${r.bold ? 700 : 400}">${pct == null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`}</td>
    </tr>`;
  };

  const varBars = rows
    .map(r => ({ label: r.account.replace(/^\d+ · /, ''), delta: r.q2 - r.q1, isRev: r.account.startsWith('4') }))
    .filter(b => b.delta !== 0)
    .sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta)).slice(0, 5);
  const maxBar = Math.max(...varBars.map(b => Math.abs(b.delta)), 1);

  const drivers = (p.drivers || []).slice(0, 5);
  const maxDrv = Math.max(...drivers.map(d => Math.abs(d.delta)), 1);

  const cacheNote = p.cache
    ? `${p.cache.table} · ${fmtNum(p.cache.row_count)} rows · ${p.cache.cache_hit ? 'ClickHouse cache' : 'origin → cached'}`
    : '';

  const aiText = buildAiExplanation(p);

  return `
  <div class="analysis-wrap">
    <div class="analysis-toolbar">
      <button class="tb-chip">Rows: Account ▾</button>
      <button class="tb-chip">Cols: Quarter ▾</button>
      <button class="tb-chip">Values: Sum(Amount) ▾</button>
      <button class="tb-chip">Filter ▾</button>
      <div style="flex:1"></div>
      <span class="tb-note">${esc(cacheNote)}</span>
      <button class="tb-chip" onclick="App.exportPivot()">↓ Export</button>
    </div>
    <div class="analysis-body">
      <div class="pivot-scroll">
        <table class="pivot">
          <thead><tr>
            <th>ACCOUNT</th><th>Q1</th><th>Q2</th><th>VARIANCE</th><th>%Δ</th>
          </tr></thead>
          <tbody>${display.map(pivotRow).join('')}</tbody>
        </table>
      </div>
      <div class="chart-col">
        <div class="chart-section">
          <div class="chart-label">VARIANCE BY ACCOUNT</div>
          ${varBars.map(b => {
            const bad = b.isRev ? b.delta < 0 : b.delta > 0;
            const color = bad ? '#ef4444' : '#22c55e';
            return `
            <div class="hbar-row">
              <span class="hbar-name">${esc(b.label.slice(0, 10))}</span>
              <div class="hbar-track" style="justify-content:${b.delta < 0 ? 'flex-end' : 'flex-start'}">
                <div class="hbar-fill" style="width:${Math.abs(b.delta) / maxBar * 100}%;background:${color}"></div>
              </div>
              <span class="hbar-val" style="color:${color}">${abbrev(b.delta)}</span>
            </div>`;
          }).join('')}
        </div>
        <div class="chart-section">
          <div class="chart-label">MONTHLY TREND</div>
          ${renderSparkline('Revenue', p.trends?.revenue)}
          ${renderSparkline('COGS', p.trends?.cogs)}
          ${renderSparkline('Net', p.trends?.net)}
        </div>
        <div class="chart-section" style="border-bottom:none">
          <div class="chart-label">TOP VARIANCE DRIVERS</div>
          ${drivers.map(d => {
            const color = d.delta > 0 ? '#ef4444' : '#22c55e';
            return `
            <div class="hbar-row">
              <span class="hbar-name" style="min-width:70px">${esc(d.vendor.slice(0, 12))}</span>
              <div class="hbar-track">
                <div class="hbar-fill" style="width:${Math.abs(d.delta) / maxDrv * 100}%;background:${color}"></div>
              </div>
              <span class="hbar-val" style="color:${color}">${abbrev(d.delta)}</span>
            </div>`;
          }).join('')}
        </div>
      </div>
    </div>
    <div class="ai-bar">
      <span style="color:#f59e0b;font-size:14px;margin-top:1px">💡</span>
      <div class="ai-text">
        <span style="color:#f59e0b;font-weight:600">Agent:</span> ${esc(aiText)}
        <span class="ai-link" onclick="App.drill('Explain the revenue variance between Q1 and Q2')">Drill into revenue →</span>
        <span class="ai-link" onclick="App.drill('Show me the software spend breakdown by vendor')">Show vendor detail →</span>
      </div>
    </div>
  </div>`;
}
function renderSparkline(name, values) {
  if (!values || values.length < 2) {
    return `<div class="spark-row"><span class="spark-name">${name}</span><span class="mono" style="font-size:9px;color:#444">no data</span></div>`;
  }
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * 140;
    const y = 20 - ((v - min) / range) * 16 + 2;
    return `${x.toFixed(0)},${y.toFixed(1)}`;
  }).join(' ');
  const rising = values[values.length - 1] >= values[0];
  const color = name === 'Revenue' ? (rising ? '#22c55e' : '#ef4444')
    : name === 'COGS' ? (rising ? '#ef4444' : '#22c55e')
    : (rising ? '#22c55e' : '#ef4444');
  return `
  <div class="spark-row">
    <span class="spark-name">${name}</span>
    <svg width="140" height="24" viewBox="0 0 140 24">
      <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"></polyline>
    </svg>
  </div>`;
}
function buildAiExplanation(p) {
  const gp = p.summary.find(s => s.account === 'Gross Profit');
  if (!gp || !gp.q1) return 'Pivot computed from the ClickHouse cache.';
  const pct = ((gp.q2 - gp.q1) / Math.abs(gp.q1) * 100);
  const rev = p.rows.find(r => r.account.startsWith('4100'));
  const cogs = p.rows.find(r => r.account.startsWith('5100'));
  const soft = p.rows.find(r => r.account.startsWith('6300'));
  const parts = [];
  parts.push(`Gross profit ${pct < 0 ? 'declined' : 'grew'} ${Math.abs(pct).toFixed(1)}%`);
  if (rev) parts.push(`revenue ${rev.q2 - rev.q1 < 0 ? 'dropped' : 'rose'} $${Math.abs(Math.round((rev.q2 - rev.q1) / 1000))}K`);
  if (cogs) parts.push(`COGS ${cogs.q2 - cogs.q1 > 0 ? 'rose' : 'fell'} $${Math.abs(Math.round((cogs.q2 - cogs.q1) / 1000))}K`);
  let text = parts[0] + ' — ' + parts.slice(1).join(' while ') + '.';
  if (soft && soft.q2 - soft.q1 > 0) {
    text += ` Software spike of $${Math.round((soft.q2 - soft.q1) / 1000)}K from license renewals.`;
  }
  return text;
}

/* ---------------- Query ---------------- */
function renderQuery() {
  const c = S.chat;
  const defaultSource = S.sources?.default || S.overview?.sources?.find(s => s.is_default)?.name || 'default';
  return `
  <div class="query-wrap">
    <div class="query-toolbar">
      <span style="font-size:10px;color:#555">Dataset:</span>
      <span class="dataset-chip">${esc(defaultSource)} ▾</span>
      <div style="flex:1"></div>
      <button class="mode-chip ${c.mode === 'sql' ? 'active' : ''}" onclick="App.setChatMode('sql')">Structured Query</button>
      <button class="mode-chip ${c.mode === 'chat' ? 'active' : ''}" onclick="App.setChatMode('chat')">Chat</button>
    </div>
    ${c.mode === 'chat' ? renderChatArea() : renderSqlArea()}
  </div>`;
}

function renderChatArea() {
  const c = S.chat;
  return `
    <div class="chat-area" id="chat-area">
      ${c.messages.length ? c.messages.map(m => `
        <div class="msg">
          <div class="msg-avatar ${m.role}">${m.role === 'user' ? esc(initials(S.overview?.user || 'U')) : 'AI'}</div>
          <div class="msg-blocks">
            ${m.blocks.map(b => renderBlock(b, m.role)).join('')}
            ${(m.compacted || m.contextWarning) ? renderContextBanner(m.contextPct, m.compacted) : ''}
          </div>
        </div>`).join('') : `<div class="empty-state">Ask about the finance data — e.g. “Explain variance in the ledgers between Q1 and Q2”</div>`}
      ${c.pending ? `<div class="thinking"><span class="pulse"></span>agent is querying…</div>` : ''}
    </div>
    <div class="input-bar">
      <input id="chat-input" class="chat-input" placeholder="Ask a follow-up question…"
        value="${esc(c.draft || '')}"
        oninput="S.chat.draft = this.value"
        onkeydown="if(event.key==='Enter') App.sendChat()">
      <div style="display:flex;gap:4px" class="mono">
        <button class="btn-mono" onclick="App.go('analysis')">📊 Pivot</button>
        <button class="btn-mono" onclick="App.exportLastTable()">↓ CSV</button>
      </div>
    </div>`;
}

function renderSqlArea() {
  const q = S.sql;
  const sourceNames = (S.sources?.sources || S.overview?.sources || []).map(s => s.name);
  return `
    <div class="chat-area">
      <div style="max-width:820px;display:flex;flex-direction:column;gap:10px">
        <div class="mini-label">SQL (SELECT only — served via the ClickHouse cache)</div>
        <textarea id="sql-input" class="sql-area" rows="4"
          placeholder="SELECT account_name, quarter, SUM(amount) FROM gl_entries GROUP BY 1, 2"
          oninput="S.sql.text = this.value">${esc(q.text)}</textarea>
        <div style="display:flex;gap:8px;align-items:center" class="mono">
          <select id="sql-source" class="field-select" style="width:auto;padding:6px 10px;font-size:11px"
            onchange="S.sql.source = this.value">
            ${sourceNames.map(n => `<option value="${esc(n)}" ${q.source === n ? 'selected' : ''}>${esc(n)}</option>`).join('')}
          </select>
          <label class="toggle-row" style="font-size:11px;color:#888" onclick="App.toggleSqlRefresh(event)">
            <div class="toggle ${q.refresh ? 'on' : 'off'}" style="width:28px;height:16px">
              <div class="toggle-knob" style="width:12px;height:12px"></div>
            </div>
            bypass cache
          </label>
          <button class="btn-primary" onclick="App.runSql()" ${q.running ? 'disabled' : ''}>${q.running ? 'Running…' : 'Run'}</button>
        </div>
        ${q.error ? `<div style="color:#ef4444;font-size:12px" class="mono">${esc(q.error)}</div>` : ''}
        ${q.result ? renderSqlResult(q.result) : ''}
      </div>
    </div>`;
}
function renderSqlResult(r) {
  return `
  <div class="block-table">
    <div class="scroll">
      <table>
        <thead><tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>
          ${r.rows.slice(0, 100).map(row => `<tr>${row.map(v => `<td>${esc(typeof v === 'number' ? fmtNum(v) : v)}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="block-meta">
      <span class="${r.cache_hit ? 'cache-hit-chip' : 'cache-miss-chip'}">
        ${r.cache_hit ? '⚡ served from ClickHouse cache' : `→ pulled from ${esc(r.served_from)} · now cached`}
      </span>
      <span>${fmtNum(r.row_count)} rows total</span>
      ${r.cached_as ? `<span>cached as ${esc(r.cached_as)}</span>` : ''}
    </div>
  </div>`;
}

function renderBlock(b, role) {
  if (b.type === 'text') {
    const body = role === 'agent' ? mdToHtml(b.content) : esc(b.content);
    return `<div class="block-text ${role}">${body}</div>`;
  }
  if (b.type === 'table') {
    const meta = b.meta || {};
    return `
    <div class="block-table">
      <div class="scroll">
        <table>
          <thead><tr>${(b.headers || []).map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
          <tbody>
            ${(b.rows || []).map(r => `<tr>${r.map(v => `<td>${esc(v)}</td>`).join('')}</tr>`).join('')}
          </tbody>
        </table>
      </div>
      ${meta.row_count != null ? `
      <div class="block-meta">
        <span class="${meta.cache_hit ? 'cache-hit-chip' : 'cache-miss-chip'}">
          ${meta.cache_hit ? '⚡ ClickHouse cache' : `→ ${esc(meta.served_from || 'origin')} · cached`}
        </span>
        <span>${fmtNum(meta.row_count)} rows</span>
        ${meta.elapsed_ms != null ? `<span>${meta.elapsed_ms}ms</span>` : ''}
      </div>` : ''}
    </div>`;
  }
  if (b.type === 'chart') {
    return `
    <div class="block-chart">
      <div class="block-chart-title">${esc(b.title || '')}</div>
      ${(b.bars || []).map(bar => `
        <div class="cbar-row">
          <span class="cbar-name">${esc(bar.label)}</span>
          <div class="cbar-track"><div class="hbar-fill" style="width:${esc(bar.width)};background:${esc(bar.color)}"></div></div>
          <span class="cbar-val" style="color:${esc(bar.color)}">${esc(bar.value)}</span>
        </div>`).join('')}
    </div>`;
  }
  return '';
}

/* ---------------- Modals ---------------- */
function renderModal() {
  if (!S.modal) return '';
  if (S.modal === 'newtask') return renderNewTaskModal();
  if (S.modal === 'addsource') return renderAddSourceModal();
  if (S.modal.type === 'config') return renderConfigModal(S.modal.name);
  return '';
}

function renderNewTaskModal() {
  const nt = S.newTask;
  const sourceNames = (S.sources?.sources || S.overview?.sources || []).map(s => s.name);
  return `
  <div class="modal-overlay" onclick="if(event.target===this) App.closeModal()">
    <div class="modal">
      <div class="modal-head">
        <span class="modal-title">New Task</span>
        <div class="modal-close" onclick="App.closeModal()">✕</div>
      </div>
      <div class="modal-body">
        <div>
          <label class="field-label">TASK DESCRIPTION</label>
          <textarea id="nt-desc" class="field-area" placeholder="Explain variance in the ledgers between Q1 and Q2"
            oninput="S.newTask.description = this.value">${esc(nt.description)}</textarea>
        </div>
        <div>
          <label class="field-label">ASSIGN AGENT</label>
          <select id="nt-agent" class="field-select" onchange="S.newTask.agent = this.value">
            ${S.agents.map(a => `<option value="${esc(a.name)}" ${nt.agent === a.name ? 'selected' : ''}>${esc(a.name)} — ${esc(a.type)}</option>`).join('')}
          </select>
        </div>
        <div>
          <label class="field-label">DATA SOURCES</label>
          <div class="chip-row">
            ${sourceNames.map(n => `
              <button class="pick-chip ${nt.sources.includes(n) ? 'on' : ''}"
                onclick="App.toggleTaskSource('${esc(n)}')">${esc(n)}${nt.sources.includes(n) ? ' ✕' : ''}</button>`).join('')}
          </div>
        </div>
        <div>
          <label class="field-label">REASONING EFFORT</label>
          <div class="chip-row">
            ${['low', 'medium', 'high'].map(p => `
              <button class="pick-chip ${nt.reasoningEffort === p ? 'amber' : ''}"
                onclick="S.newTask.reasoningEffort='${p}'; render()">${p[0].toUpperCase() + p.slice(1)}</button>`).join('')}
          </div>
        </div>
        <div class="toggle-row" onclick="S.newTask.approval = !S.newTask.approval; render()">
          <div class="toggle ${nt.approval ? 'on' : 'off'}"><div class="toggle-knob"></div></div>
          <span style="font-size:12px;color:#aaa">Require approval for expensive queries</span>
        </div>
        <div class="modal-actions">
          <button class="btn-cancel" onclick="App.closeModal()">Cancel</button>
          <button class="btn-create" onclick="App.createTask()">Create Task</button>
        </div>
      </div>
    </div>
  </div>`;
}

const KIND_FIELDS = {
  clickhouse: [['host', 'localhost'], ['port', '8123'], ['database', 'default'], ['username', 'default'], ['password', '']],
  postgres: [['dsn', 'postgresql://user:pass@host:5432/db']],
  duckdb: [['path', '/path/to/file.duckdb']],
  trino: [['host', 'localhost'], ['port', '8080'], ['catalog', 'hive'], ['schema', 'default'], ['user', 'finagent']],
};
function renderAddSourceModal() {
  const ns = S.newSource;
  const fields = KIND_FIELDS[ns.kind] || [];
  return `
  <div class="modal-overlay" onclick="if(event.target===this) App.closeModal()">
    <div class="modal">
      <div class="modal-head">
        <span class="modal-title">Add Data Source</span>
        <div class="modal-close" onclick="App.closeModal()">✕</div>
      </div>
      <div class="modal-body">
        <div>
          <label class="field-label">KIND</label>
          <div class="chip-row">
            ${Object.keys(KIND_FIELDS).map(k => `
              <button class="pick-chip ${ns.kind === k ? 'amber' : ''}"
                onclick="S.newSource.kind='${k}'; S.newSource.params={}; render()">${k}</button>`).join('')}
          </div>
        </div>
        <div>
          <label class="field-label">NAME</label>
          <input id="ns-name" class="field-input" placeholder="prod_warehouse" value="${esc(ns.name)}"
            oninput="S.newSource.name = this.value">
        </div>
        ${fields.map(([f, ph]) => `
        <div>
          <label class="field-label">${f.toUpperCase()}</label>
          <input id="ns-${f}" class="field-input" placeholder="${esc(ph)}"
            type="${f === 'password' ? 'password' : 'text'}"
            value="${esc(ns.params[f] || '')}"
            oninput="S.newSource.params['${f}'] = this.value">
        </div>`).join('')}
        ${S.error ? `<div style="color:#ef4444;font-size:12px">${esc(S.error)}</div>` : ''}
        <div class="modal-actions">
          <button class="btn-cancel" onclick="App.closeModal()">Cancel</button>
          <button class="btn-create" onclick="App.addSource()">Connect</button>
        </div>
      </div>
    </div>
  </div>`;
}

function renderConfigModal(name) {
  const s = (S.sources?.sources || []).find(x => x.name === name);
  if (!s) return '';
  return `
  <div class="modal-overlay" onclick="if(event.target===this) App.closeModal()">
    <div class="modal">
      <div class="modal-head">
        <span class="modal-title">${esc(s.icon || '')} ${esc(s.name)}</span>
        <div class="modal-close" onclick="App.closeModal()">✕</div>
      </div>
      <div class="modal-body">
        <div>
          <label class="field-label">KIND</label>
          <div class="mono" style="font-size:12px;color:#ccc">${esc(s.kind)}</div>
        </div>
        <div>
          <label class="field-label">CONNECTION</label>
          <div class="mono" style="font-size:11px;color:#888;white-space:pre-wrap">${esc(JSON.stringify(s.connection || {}, null, 2))}</div>
        </div>
        <div>
          <label class="field-label">STATUS</label>
          <div class="mono" style="font-size:12px;color:${s.connected ? '#22c55e' : '#ef4444'}">
            ${s.connected ? '● CONNECTED' : `● UNREACHABLE ${esc(s.error || '')}`}
          </div>
        </div>
        ${s.tables ? `
        <div>
          <label class="field-label">TABLES (${s.tables.length})</label>
          <div class="mono" style="font-size:11px;color:#888;max-height:180px;overflow-y:auto">
            ${s.tables.map(t => `<div>${esc(t.name)}${t.rows != null ? ` · ${fmtNum(t.rows)} rows` : ''}</div>`).join('')}
          </div>
        </div>` : ''}
        <div class="modal-actions">
          <button class="btn-cancel" onclick="App.closeModal()">Close</button>
        </div>
      </div>
    </div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* Actions                                                            */
/* ------------------------------------------------------------------ */
const App = {
  async copyText(text, flashId) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) { alert('Copy failed: ' + e.message); return; }
    S.copiedFlash = flashId;
    render();
    setTimeout(() => {
      if (S.copiedFlash === flashId) { S.copiedFlash = null; render(); }
    }, 1500);
  },
  copyTaskDescription(taskId) {
    const t = (S.taskDetail && S.taskDetail.id === taskId) ? S.taskDetail : S.tasks.find(x => x.id === taskId);
    if (!t) return;
    this.copyText(t.description, 'task-desc-' + taskId);
  },
  copyTraceId(taskId) {
    const t = (S.taskDetail && S.taskDetail.id === taskId) ? S.taskDetail : S.tasks.find(x => x.id === taskId);
    if (!t || !t.trace_id) return;
    this.copyText(t.trace_id, 'task-trace-' + taskId);
  },
  toggleFocusMode() { S.focusMode = !S.focusMode; render(); },
  setTextScale(delta) {
    S.textScale = delta === 0 ? 1 : Math.min(1.6, Math.max(0.8, +(S.textScale + delta).toFixed(2)));
    localStorage.setItem('finagent_text_scale', S.textScale);
    applyTextScale();
    render();
  },
  go(view) {
    S.view = view;
    render();
    loadView(view);
  },
  async goTask(id, wantModify) {
    S.view = 'tasks';
    S.selectedTaskId = id;
    S.modifyOpen = !!wantModify;
    S.focusMode = true;
    render();
    try {
      S.taskDetail = await api(`/api/tasks/${id}`);
      if (wantModify) S.modifyText = S.taskDetail.approval?.query || '';
    } catch (e) { /* task list will still show */ }
    render();
  },
  setFilter(f) { S.taskFilter = f; render(); },
  setTaskSearch(v) { S.taskSearch = v; render(); },
  toggleModify(open, query) {
    S.modifyOpen = open;
    if (open) S.modifyText = query || '';
    render();
  },
  async decide(taskId, decision) {
    const body = { decision };
    if (decision === 'modify') body.modified_query = S.modifyText;
    try {
      await api(`/api/tasks/${taskId}/approval`, { body });
      S.modifyOpen = false;
    } catch (e) { alert(e.message); }
    refreshTasks();
  },
  openNewTask() {
    S.modal = 'newtask';
    S.newTask = { description: '', agent: S.agents[0]?.name || 'Recon Agent', sources: [], reasoningEffort: 'medium', approval: true };
    render();
    setTimeout(() => document.getElementById('nt-desc')?.focus(), 30);
  },
  toggleTaskSource(name) {
    const list = S.newTask.sources;
    const i = list.indexOf(name);
    if (i >= 0) list.splice(i, 1); else list.push(name);
    render();
  },
  async createTask() {
    if (!S.newTask.description.trim()) { alert('Describe the task first'); return; }
    try {
      const t = await api('/api/tasks', { body: {
        description: S.newTask.description,
        agent: S.newTask.agent,
        sources: S.newTask.sources,
        reasoning_effort: S.newTask.reasoningEffort,
        require_approval: S.newTask.approval,
      }});
      S.modal = null;
      await refreshTasks();
      this.goTask(t.id);
    } catch (e) { alert(e.message); }
  },
  async askTask(id) {
    const text = (S.askText || '').trim();
    if (!text) return;
    S.askText = '';
    try {
      await api(`/api/tasks/${id}/ask`, { body: { message: text } });
    } catch (e) { alert(e.message); }
    this.goTask(id);
  },
  closeModal() { S.modal = null; S.error = null; render(); },
  openAddSource() {
    S.modal = 'addsource';
    S.newSource = { name: '', kind: 'clickhouse', params: {} };
    render();
  },
  async addSource() {
    const ns = S.newSource;
    if (!ns.name.trim()) { S.error = 'Name is required'; render(); return; }
    const params = { ...ns.params };
    if (params.port) params.port = Number(params.port);
    try {
      await api('/api/sources', { body: { name: ns.name.trim(), kind: ns.kind, params } });
      S.modal = null; S.error = null;
      await loadSources();
    } catch (e) { S.error = e.message; }
    render();
  },
  openConfig(name) { S.modal = { type: 'config', name }; render(); },
  async refreshSource(name) {
    try {
      await api(`/api/sources/${encodeURIComponent(name)}/refresh`, { body: {} });
      await Promise.all([loadSources(), loadOverview()]);
    } catch (e) { alert(e.message); }
    render();
  },
  setChatMode(mode) { S.chat.mode = mode; render(); },
  async sendChat() {
    const text = (S.chat.draft || '').trim();
    if (!text || S.chat.pending) return;
    S.chat.draft = '';
    S.chat.messages.push({ role: 'user', blocks: [{ type: 'text', content: text }] });
    S.chat.pending = true;
    render();
    scrollChat();
    try {
      const res = await api('/api/query', { body: { message: text, session_id: S.chat.sessionId } });
      S.chat.sessionId = res.session_id;
      S.chat.messages.push({
        role: 'agent', blocks: res.blocks,
        contextPct: res.context_pct, contextWarning: res.context_warning, compacted: res.compacted,
      });
    } catch (e) {
      S.chat.messages.push({ role: 'agent', blocks: [{ type: 'text', content: `Error: ${e.message}` }] });
    }
    S.chat.pending = false;
    render();
    scrollChat();
    loadOverview();  // cache stats moved
  },
  drill(question) {
    S.view = 'query';
    S.chat.mode = 'chat';
    S.chat.draft = question;
    render();
    document.getElementById('chat-input')?.focus();
  },
  toggleSqlRefresh(ev) {
    ev.preventDefault();
    S.sql.refresh = !S.sql.refresh;
    render();
  },
  async runSql() {
    const q = S.sql;
    if (!q.text.trim() || q.running) return;
    q.running = true; q.error = null;
    render();
    try {
      q.result = await api('/api/sql', { body: {
        sql: q.text, source: q.source || undefined, refresh: q.refresh,
      }});
    } catch (e) {
      q.error = e.message; q.result = null;
    }
    q.running = false;
    render();
    loadOverview();
  },
  exportLastTable() {
    for (let i = S.chat.messages.length - 1; i >= 0; i--) {
      const tbl = S.chat.messages[i].blocks.find(b => b.type === 'table');
      if (tbl) return downloadCsv(tbl.headers, tbl.rows, 'finagent_result.csv');
    }
    alert('No table in the conversation yet');
  },
  exportPivot() {
    if (!S.pivot) return;
    const rows = S.pivot.rows.map(r => [r.account, r.q1, r.q2, r.q2 - r.q1]);
    downloadCsv(['ACCOUNT', 'Q1', 'Q2', 'VARIANCE'], rows, 'finagent_pivot.csv');
  },
};
window.App = App;
window.S = S;

function downloadCsv(headers, rows, filename) {
  const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [headers.map(q).join(','), ...rows.map(r => r.map(q).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  a.download = filename;
  a.click();
}
function scrollChat() {
  const el = document.getElementById('chat-area');
  if (el) el.scrollTop = el.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* Data loading + live updates                                        */
/* ------------------------------------------------------------------ */
async function loadOverview() {
  try {
    S.overview = await api('/api/overview');
    S.feed = S.overview.feed || S.feed;
  } catch (e) { /* server starting */ }
  render();
}
async function refreshTasks() {
  try { S.tasks = (await api('/api/tasks')).tasks; } catch (e) { /* ignore */ }
  if (S.selectedTaskId) {
    try { S.taskDetail = await api(`/api/tasks/${S.selectedTaskId}`); } catch (e) { /* ignore */ }
  }
  render();
}
async function loadSources() {
  try { S.sources = await api('/api/sources'); } catch (e) { /* ignore */ }
  render();
}
async function loadPivot() {
  S.pivot = null;
  render();
  try {
    S.pivot = await api('/api/analysis/pivot');
  } catch (e) {
    S.pivot = { error: e.message };
  }
  render();
}
async function loadAgents() {
  try { S.agents = (await api('/api/agents')).agents; } catch (e) { /* ignore */ }
  render();
}
async function loadProfile() {
  try { S.profile = await api('/api/profile'); }
  catch (e) { S.profile = { profile_text: '', error: e.message }; }
  render();
}
function loadView(view) {
  if (view === 'dashboard') { loadOverview(); refreshTasks(); }
  if (view === 'tasks') refreshTasks();
  if (view === 'agents') loadAgents();
  if (view === 'sources') { loadSources(); loadOverview(); }
  if (view === 'analysis') loadPivot();
  if (view === 'query') { if (!S.sources) loadSources(); }
  if (view === 'profile') loadProfile();
}

let ws = null;
function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'hello') {
      S.feed = msg.data.feed || [];
      S.agents = msg.data.agents || [];
      render();
    } else if (msg.type === 'feed') {
      S.feed.unshift(msg.data);
      S.feed.length = Math.min(S.feed.length, 50);
      if (S.view === 'dashboard') render();
    } else if (msg.type === 'agents') {
      S.agents = msg.data;
      if (['agents', 'dashboard'].includes(S.view)) render();
    } else if (msg.type === 'task' || msg.type === 'approval') {
      const t = msg.type === 'approval' ? msg.data.task : msg.data;
      const i = S.tasks.findIndex(x => x.id === t.id);
      if (i >= 0) S.tasks[i] = { ...S.tasks[i], ...t }; else S.tasks.unshift(t);
      if (S.taskDetail && S.taskDetail.id === t.id) {
        S.taskDetail = { ...S.taskDetail, ...t };
      }
      if (['tasks', 'dashboard'].includes(S.view)) scheduleRender();
      if (S.view === 'dashboard') loadOverview();
    } else if (msg.type === 'log') {
      if (S.taskDetail && S.taskDetail.id === msg.data.task_id) {
        S.taskDetail.logs = [...(S.taskDetail.logs || []), msg.data.entry];
        if (S.view === 'tasks') {
          scheduleRender(() => {
            const box = document.getElementById('log-box');
            if (box) box.scrollTop = box.scrollHeight;
          });
        }
      }
    }
  };
  ws.onclose = () => setTimeout(connectWs, 2000);
}

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    App.openNewTask();
  }
  if (e.key === 'Escape' && S.modal) App.closeModal();
});

/* boot */
applyTextScale();
loadOverview();
refreshTasks();
loadAgents();
connectWs();
setInterval(() => { if (S.view === 'dashboard') loadOverview(); }, 15000);

/* Waiting-animation ticker: cycles the status word and updates the
   elapsed-time readout in place, without a full re-render — a full
   render() every second would tear down and recreate the whole task
   panel, restarting its CSS animations and reading as a "blink". */
let _waitTick = 0;
setInterval(() => {
  _waitTick++;
  const t = S.taskDetail;
  if (!t || !['queued', 'running'].includes(t.status)) return;
  const wordEl = document.getElementById('wa-word');
  if (wordEl) wordEl.textContent = WAITING_WORDS[Math.floor(_waitTick / 2.4) % WAITING_WORDS.length];
  const durEl = document.getElementById('stat-duration');
  if (durEl && durEl.dataset.live === '1') {
    durEl.textContent = fmtDuration(Date.now() - Number(durEl.dataset.started) * 1000);
  }
}, 1000);

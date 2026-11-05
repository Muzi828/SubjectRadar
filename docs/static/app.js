'use strict';
/* SafeLens Web · 静态多用户版
 * 数据全部保存在访问者本机浏览器（IndexedDB）：论文底库共享，个人状态按用户隔离。 */

// ---------------- 工具 ----------------
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeRegExp(v) { return String(v).replace(/[.*+?^$()|[\]\\]/g, '\\$&'); }
function debounce(fn, wait) {
  let t = null;
  return function () { const a = arguments, s = this; clearTimeout(t); t = setTimeout(() => fn.apply(s, a), wait); };
}
function fmtDate(value, precision) {
  if (!value) return '日期待定';
  const parts = String(value).split('-');
  if (parts.length < 2) return value;
  if (precision === 'month') return parts[0] + '年' + Number(parts[1]) + '月';
  if (precision === 'year') return parts[0] + '年';
  return parts[0] + '-' + parts[1] + '-' + (parts[2] || '01');
}
function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function nowIso() {
  const d = new Date();
  return todayStr() + 'T' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') + ':' + String(d.getSeconds()).padStart(2, '0');
}
function icon(name) { return '<svg><use href="#i-' + name + '"></use></svg>'; }
function toast(message, type) {
  const stack = $('#toast-stack');
  const node = document.createElement('div');
  node.className = 'toast ' + (type || '');
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => { node.classList.add('leaving'); setTimeout(() => node.remove(), 240); }, type === 'error' ? 5200 : 3200);
}
function setText(sel, value) { const n = $(sel); if (n) n.textContent = value; }
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast('已复制到剪贴板')).catch(() => fallbackCopy(text));
  } else fallbackCopy(text);
}
function fallbackCopy(text) {
  const area = document.createElement('textarea');
  area.value = text; area.style.position = 'fixed'; area.style.opacity = '0';
  document.body.appendChild(area); area.select();
  try { document.execCommand('copy'); toast('已复制到剪贴板'); } catch (e) { toast('复制失败', 'error'); }
  area.remove();
}
function download(name, content, type) {
  const blob = new Blob([content], { type: type || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name;
  document.body.appendChild(link); link.click(); link.remove();
  URL.revokeObjectURL(url);
}

// ---------------- IndexedDB ----------------
const IDB_NAME = 'safelens-web';
let idb = null;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 1);
    req.onupgradeneeded = () => {
      const d = req.result;
      if (!d.objectStoreNames.contains('papers')) d.createObjectStore('papers', { keyPath: 'key' });
      if (!d.objectStoreNames.contains('profiles')) d.createObjectStore('profiles', { keyPath: 'name' });
      if (!d.objectStoreNames.contains('states')) d.createObjectStore('states', { keyPath: ['u', 'p'] });
      if (!d.objectStoreNames.contains('settings')) d.createObjectStore('settings', { keyPath: ['u', 'k'] });
      if (!d.objectStoreNames.contains('meta')) d.createObjectStore('meta', { keyPath: 'k' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function tx(store, mode) { return idb.transaction(store, mode || 'readonly').objectStore(store); }
function reqP(request) {
  return new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
}
async function idbAll(store) { return reqP(tx(store).getAll()); }
async function idbGet(store, key) { return reqP(tx(store).get(key)); }
async function idbPut(store, value) { return reqP(tx(store, 'readwrite').put(value)); }
async function idbDel(store, key) { return reqP(tx(store, 'readwrite').delete(key)); }
async function idbClear(store) { return reqP(tx(store, 'readwrite').clear()); }

// ---------------- 论文身份与合并 ----------------
function paperKey(p) {
  if (p.key) return p.key;
  if (p.fingerprint) return 'fp:' + p.fingerprint;
  if (p.doi) return 'doi:' + String(p.doi).toLowerCase();
  if (p.pii) return 'pii:' + p.pii;
  return 'jt:' + (p.journal_name || '') + '|' + (p.title || '').toLowerCase().trim();
}
const MERGE_FIELDS = ['doi', 'pii', 'title_en', 'authors_en', 'abstract', 'abstract_en', 'affiliations', 'funding',
  'keywords', 'keywords_en', 'topics', 'volume', 'issue', 'pages', 'published_date', 'online_date',
  'url', 'doi_url', 'pdf_url', 'preview_url', 'citation_text', 'citation_text_en', 'citation_count', 'journal_color'];
function mergePaper(existing, incoming) {
  if (!existing) return incoming;
  const out = existing;
  for (const f of MERGE_FIELDS) {
    const v = incoming[f];
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) continue;
    if (f === 'citation_count') { out[f] = Math.max(Number(out[f]) || 0, Number(v) || 0); continue; }
    const cur = out[f];
    if (cur === undefined || cur === null || cur === '' || (Array.isArray(cur) && !cur.length)) out[f] = v;
  }
  if (incoming.first_seen_at && (!out.first_seen_at || incoming.first_seen_at < out.first_seen_at)) {
    out.first_seen_at = incoming.first_seen_at;
  }
  return out;
}

// ---------------- 账号（纯本地） ----------------
async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.prototype.map.call(new Uint8Array(buf), b => b.toString(16).padStart(2, '0')).join('');
}
function newSalt() {
  const arr = new Uint8Array(16);
  crypto.getRandomValues(arr);
  return Array.prototype.map.call(arr, b => b.toString(16).padStart(2, '0')).join('');
}
async function registerUser(name, password) {
  const profile = await idbGet('profiles', name);
  if (profile) throw new Error('用户名已存在，请直接登录');
  const salt = newSalt();
  const record = { name, salt, hash: await sha256(salt + password), created_at: nowIso() };
  await idbPut('profiles', record);
  return record;
}
async function loginUser(name, password) {
  const profile = await idbGet('profiles', name);
  if (!profile) throw new Error('用户不存在，请切换到“注册”');
  if (profile.hash !== await sha256(profile.salt + password)) throw new Error('密码不正确');
  return profile;
}

// ---------------- 全局状态 ----------------
const state = {
  user: null,
  papers: [],            // 全部论文（含他人导入的底库，共享）
  journals: [],
  states: new Map(),     // key -> 个人状态
  settings: null,
  route: { name: 'home', journalId: null },
  q: '', searchField: 'all', topic: '', journalFilter: '', range: 'all', sort: 'interest',
  page: 1, pageSize: 20,
  filtered: [], selected: new Set(),
  drawerKey: null,
  snapshotInfo: null
};
const SETTINGS_DEFAULTS = {
  interests: [],
  watched_authors: [],
  watch_keywords: ['risk', 'resilience', 'human factors', 'fire', 'evacuation', 'construction', '事故', '安全管理', '安全文化'],
  hidden_journals: [],
  theme: 'light',
  snapshot_at: ''
};

const VIEW_META = {
  home: { title: '总览', eyebrow: 'SAFE SCIENCE · LITERATURE RADAR' },
  today: { title: '今日新增', eyebrow: 'NEW ARRIVALS · TODAY', desc: '底库更新当天收录的论文。' },
  inbox: { title: '待读', eyebrow: 'INBOX · UNREAD', desc: '还没有阅读的论文。' },
  starred: { title: '收藏', eyebrow: 'STARRED · FOCUS', desc: '你标记为重点的论文。' },
  later: { title: '稍后读', eyebrow: 'READ LATER', desc: '先收起来，等有空再读。' },
  watch: { title: '作者关注', eyebrow: 'WATCHED AUTHORS', desc: '命中你追踪名单的作者/机构的论文。' },
  all: { title: '全部论文', eyebrow: 'LITERATURE LIBRARY', desc: '按入库时间排序，最新收录在最上方。' },
  archive: { title: '归档', eyebrow: 'ARCHIVE', desc: '已隐藏的论文，仍可搜索和恢复。' }
};

// ---------------- 用户设置 ----------------
async function loadSettings() {
  const rows = await idbAll('settings');
  const settings = Object.assign({}, SETTINGS_DEFAULTS);
  rows.filter(r => r.u === state.user).forEach(r => { settings[r.k] = r.v; });
  state.settings = settings;
}
async function saveSetting(key, value) {
  state.settings[key] = value;
  await idbPut('settings', { u: state.user, k: key, v: value });
}

// ---------------- 个人状态 ----------------
function defaultState(key) {
  return { u: state.user, p: key, starred: false, read_later: false, read_at: null, archived: false, notes: '', user_tags: [] };
}
function getState(key) {
  return state.states.get(key) || defaultState(key);
}
async function putState(s) {
  state.states.set(s.p, s);
  await idbPut('states', s);
}
async function loadStates() {
  const rows = await idbAll('states');
  state.states = new Map(rows.filter(r => r.u === state.user).map(r => [r.p, r]));
}
function paperWithState(p) {
  const s = getState(p.key);
  return Object.assign({}, p, {
    starred: !!s.starred, read_later: !!s.read_later, archived: !!s.archived,
    read_at: s.read_at || null, notes: s.notes || '', user_tags: s.user_tags || []
  });
}

// ---------------- 匹配逻辑 ----------------
function interestMatch(p) {
  const terms = state.settings.interests || [];
  if (!terms.length) return false;
  const hay = [p.title, p.abstract, (p.keywords || []).join(' '), (p.topics || []).join(' ')].join(' ').toLowerCase();
  return terms.some(t => t && hay.indexOf(String(t).toLowerCase()) >= 0);
}
function watchMatch(p) {
  const watched = state.settings.watched_authors || [];
  if (!watched.length) return [];
  const authors = (p.authors || []).concat(p.authors_en || []).map(a => String(a).toLowerCase());
  return watched.filter(w => authors.some(a => a.indexOf(String(w).toLowerCase()) >= 0));
}
function isFresh(p) {
  if (!p.first_seen_at || p.read_at) return false;
  const seen = new Date(String(p.first_seen_at).replace(' ', 'T'));
  return (Date.now() - seen.getTime()) < 3 * 86400000;
}
function isBackfill(p) {
  if (!p.published_date || !p.first_seen_at) return false;
  return (new Date(p.first_seen_at) - new Date(p.published_date + 'T00:00:00')) > 45 * 86400000;
}
function highlight(text, keywords) {
  const safe = esc(text || '');
  const words = (keywords || []).map(k => String(k || '').trim()).filter(k => k.length > 1).map(k => escapeRegExp(esc(k)));
  if (!words.length) return safe;
  try { return safe.replace(new RegExp('(' + words.join('|') + ')', 'gi'), '<mark>$1</mark>'); }
  catch (e) { return safe; }
}
function shortAuthors(authors, max) {
  const list = authors || [];
  if (!list.length) return '作者信息待补全';
  const limit = max || 3;
  if (list.length <= limit) return list.join(', ');
  return list.slice(0, limit).join(', ') + ' 等 ' + list.length + ' 位作者';
}
function topicColor(name) {
  const palette = { '事故与伤害': '#B24B2A', '安全管理': '#8A6A2F', '安全文化': '#A16A3A', '地质灾害与防灾': '#6B6A4F', '风险评估与管控': '#256C7A', '应急与救援': '#4B6E8A', '火灾与消防': '#C25A2E', '职业健康': '#4F7A62', '矿山安全': '#5A6B3B', '化工与过程安全': '#8A4B57', '建筑施工安全': '#B8653A', '交通安全': '#3F5F8A', '公共安全与城市安全': '#6B6A8A', '安全智能化': '#4F6D8C', '安全人因工程': '#7A5C8E', '安全法治与政策': '#6F5B4A', '安全教育与培训': '#8A7A3A', '韧性': '#3C6E71', '安全科学综合': '#8A8578' };
  return palette[name] || '#9b9a88';
}

// ---------------- 底库加载与合并 ----------------
async function loadBase() {
  const rows = await idbAll('papers');
  state.papers = rows;
  const journalMap = new Map();
  rows.forEach(p => {
    if (!journalMap.has(p.journal_id)) {
      journalMap.set(p.journal_id, { id: p.journal_id, name: p.journal_name, short_name: p.journal_short, color: p.journal_color, publisher: p.journal_publisher });
    }
  });
  state.journals = Array.from(journalMap.values()).sort((a, b) => a.name.localeCompare(b.name, 'zh'));
}
async function mergeSnapshot(papers, meta) {
  const byKey = new Map(state.papers.map(p => [p.key, p]));
  let added = 0, updated = 0;
  const toPut = [];
  for (const raw of papers) {
    const key = paperKey(raw);
    const incoming = Object.assign({}, raw, { key });
    const existing = byKey.get(key);
    if (!existing) { byKey.set(key, incoming); toPut.push(incoming); added += 1; }
    else {
      const merged = mergePaper(existing, incoming);
      if (merged !== existing || JSON.stringify(merged) !== JSON.stringify(existing)) { byKey.set(key, merged); toPut.push(merged); updated += 1; }
    }
  }
  if (toPut.length) {
    const store = tx('papers', 'readwrite');
    toPut.forEach(p => store.put(p));
    await reqP(store.transaction);
    state.papers = Array.from(byKey.values());
  }
  if (meta && meta.exported_at) await idbPut('meta', { k: 'snapshot', v: meta });
  return { added, updated };
}
async function syncSnapshot() {
  try {
    const res = await fetch('data/snapshot.json', { cache: 'no-store' });
    if (!res.ok) return;
    const payload = await res.json();
    const stored = await idbGet('meta', 'snapshot');
    if (stored && stored.v && stored.v.exported_at === payload.exported_at) return;
    const r = await mergeSnapshot(payload.papers || [], { exported_at: payload.exported_at, count: (payload.papers || []).length });
    await saveSetting('snapshot_at', payload.exported_at);
    state.snapshotInfo = payload;
    if (r.added || r.updated) {
      refreshJournalMap();
      if (state.user) { renderAll(); toast('底库已更新：新增 ' + r.added + ' 篇，刷新 ' + r.updated + ' 篇', 'info'); }
    }
  } catch (e) { console.warn('snapshot sync failed', e); }
}
function refreshJournalMap() {
  const journalMap = new Map();
  state.papers.forEach(p => {
    if (!journalMap.has(p.journal_id)) {
      journalMap.set(p.journal_id, { id: p.journal_id, name: p.journal_name, short_name: p.journal_short, color: p.journal_color, publisher: p.journal_publisher });
    }
  });
  state.journals = Array.from(journalMap.values()).sort((a, b) => a.name.localeCompare(b.name, 'zh'));
}

// ---------------- 查询引擎 ----------------
function visibleJournals() {
  const hidden = state.settings.hidden_journals || [];
  return new Set(state.journals.filter(j => hidden.indexOf(j.id) < 0).map(j => j.id));
}
function computeFiltered() {
  const view = state.route.name === 'journal' ? 'all' : state.route.name;
  const today = todayStr();
  const vis = visibleJournals();
  let items = state.papers.filter(p => vis.has(p.journal_id));
  const st = p => getState(p.key);
  if (view === 'today') items = items.filter(p => String(p.first_seen_at || '').slice(0, 10) === today);
  else if (view === 'inbox') items = items.filter(p => { const s = st(p); return !s.read_at && !s.archived; });
  else if (view === 'starred') items = items.filter(p => st(p).starred && !st(p).archived);
  else if (view === 'later') items = items.filter(p => st(p).read_later && !st(p).archived);
  else if (view === 'watch') items = items.filter(p => watchMatch(p).length && !st(p).archived);
  else if (view === 'archive') items = items.filter(p => st(p).archived);
  else items = items.filter(p => !st(p).archived);
  if (state.route.name === 'journal' && state.route.journalId) {
    items = items.filter(p => p.journal_id === Number(state.route.journalId));
  }
  if (state.searchField === 'interests') {
    items = items.filter(p => interestMatch(p) || watchMatch(p).length);
  } else if (state.q) {
    const terms = state.q.trim().split(/\s+/).filter(Boolean).slice(0, 8);
    items = items.filter(p => {
      const fields = {
        all: [p.title, p.abstract, (p.authors || []).join(' '), p.doi, p.journal_name].join(' ').toLowerCase(),
        title: String(p.title || '').toLowerCase(),
        author: (p.authors || []).join(' ').toLowerCase() + ' ' + (p.authors_en || []).join(' ').toLowerCase(),
        abstract: String(p.abstract || '').toLowerCase() + ' ' + String(p.abstract_en || '').toLowerCase(),
        doi: String(p.doi || '').toLowerCase(),
        journal: String(p.journal_name || '').toLowerCase()
      };
      const hay = fields[state.searchField] || fields.all;
      return terms.every(t => hay.indexOf(t.toLowerCase()) >= 0);
    });
  }
  if (state.topic) items = items.filter(p => (p.topics || []).indexOf(state.topic) >= 0);
  if (state.journalFilter && state.route.name !== 'journal') items = items.filter(p => p.journal_id === Number(state.journalFilter));
  if (state.range !== 'all') {
    const from = new Date(); from.setDate(from.getDate() - Number(state.range));
    const fromStr = from.toISOString().slice(0, 10);
    items = items.filter(p => String(p.first_seen_at || '').slice(0, 10) >= fromStr);
  }
  const sorts = {
    interest: (a, b) => (Number(interestMatch(b) || watchMatch(b).length) - Number(interestMatch(a) || watchMatch(a).length)) || String(b.first_seen_at).localeCompare(String(a.first_seen_at)),
    newest: (a, b) => String(b.first_seen_at).localeCompare(String(a.first_seen_at)),
    published: (a, b) => String(b.published_date || '').localeCompare(String(a.published_date || '')) || String(b.first_seen_at).localeCompare(String(a.first_seen_at)),
    cited: (a, b) => (Number(b.citation_count) || 0) - (Number(a.citation_count) || 0),
    title: (a, b) => String(a.title).localeCompare(String(b.title), 'en')
  };
  items.sort(sorts[state.sort] || sorts.newest);
  state.filtered = items;
  state.selected.clear();
  return items;
}

// ---------------- 渲染 ----------------
function emptyState(iconName, title, desc, action) {
  return '<div class="empty-state"><div class="empty-icon">' + icon(iconName) + '</div><h3>' + esc(title) + '</h3><p>' + esc(desc || '') + '</p>' + (action || '') + '</div>';
}
function paperCard(p, compact) {
  const paper = paperWithState(p);
  const keywords = state.settings.watch_keywords || [];
  const color = paper.journal_color || '#c2552d';
  const abstract = paper.abstract || paper.abstract_en || '';
  const topics = (paper.topics || []).slice(0, compact ? 2 : 3);
  const date = fmtDate(paper.published_date, paper.published_precision);
  const authors = shortAuthors(paper.authors, compact ? 2 : 3);
  const newBadge = isFresh(paper) ? '<span class="badge new">NEW</span>' : '';
  const oaBadge = paper.open_access ? '<span class="badge oa">OA</span>' : '';
  const interestBadge = interestMatch(paper) ? '<span class="badge interest">关注</span>' : '';
  const watchBadge = watchMatch(paper).length ? '<span class="badge watch">关注作者</span>' : '';
  const citationBadge = Number(paper.citation_count) > 0 ? '<span class="paper-citation">被引 ' + Number(paper.citation_count) + '</span>' : '';
  const backfillBadge = isBackfill(paper) ? '<span class="badge backfill" title="收录时已出版超过 45 天">补录</span>' : '';
  const tags = topics.map(t => '<span class="mini-tag"><i style="--tc:' + topicColor(t) + '"></i>' + esc(t) + '</span>').join('');
  const excerpt = abstract ? '<p class="paper-excerpt">' + highlight(abstract.slice(0, compact ? 180 : 260), keywords) + '</p>' : '';
  const sourceUrl = paper.url || paper.doi_url || (paper.doi ? 'https://doi.org/' + paper.doi : '');
  const sourceLink = sourceUrl && !compact ? '<a class="paper-source-link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener" data-open-external="' + esc(paper.key) + '" title="' + esc(sourceUrl) + '">原文链接 <span>' + esc(String(sourceUrl).replace(/^https?:\/\//, '').replace(/^www\./, '')) + '</span>' + icon('external') + '</a>' : '';
  const pdfLink = paper.pdf_url && !compact ? '<a class="paper-pdf-link" href="' + esc(paper.pdf_url) + '" target="_blank" rel="noopener" title="跳转出版商 PDF">PDF（官网）</a>' : '';
  const isRead = !!paper.read_at;
  const isSelected = state.selected.has(paper.key);
  return '<article class="paper-card ' + (isRead ? 'is-read ' : '') + (isSelected ? 'selected ' : '') + (compact ? 'compact ' : '') + '" data-key="' + esc(paper.key) + '" style="--journal-color:' + esc(color) + '">' +
    (compact ? '' : '<label class="paper-check"><input type="checkbox" data-select="' + esc(paper.key) + '"' + (isSelected ? ' checked' : '') + '></label>') +
    '<div class="paper-main">' +
      '<div class="paper-meta">' +
        '<span class="journal-swatch" style="--jc:' + esc(color) + '"></span>' +
        '<span class="journal-name">' + esc(paper.journal_name || '') + '</span>' +
        '<span class="sep">·</span><span>' + esc(date) + '</span>' +
        newBadge + oaBadge + interestBadge + watchBadge + citationBadge + backfillBadge +
      '</div>' +
      '<h3 class="paper-title" data-open="' + esc(paper.key) + '">' + highlight(paper.title, keywords) + '</h3>' +
      '<p class="paper-authors">' + esc(authors) + '</p>' +
      excerpt +
      (tags ? '<div class="paper-tags">' + tags + '</div>' : '') +
      pdfLink + sourceLink +
    '</div>' +
    '<div class="paper-actions">' +
      '<button class="paper-action ' + (paper.starred ? 'is-on' : '') + '" data-action="star" data-key="' + esc(paper.key) + '" title="收藏">' + icon('star') + '</button>' +
      '<button class="paper-action ' + (paper.read_later ? 'is-on' : '') + '" data-action="later" data-key="' + esc(paper.key) + '" title="稍后读">' + icon('clock') + '</button>' +
      '<button class="paper-action" data-action="read" data-key="' + esc(paper.key) + '" title="' + (isRead ? '标为未读' : '标为已读') + '">' + icon(isRead ? 'check' : 'inbox') + '</button>' +
      (sourceUrl ? '<a class="paper-action" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener" data-open-external="' + esc(paper.key) + '" title="打开原文">' + icon('external') + '</a>' : '') +
    '</div>' +
  '</article>';
}
function renderPaperList() {
  const node = $('#paper-list');
  if (!node) return;
  const items = state.filtered;
  const start = (state.page - 1) * state.pageSize;
  const pageItems = items.slice(start, start + state.pageSize);
  if (!pageItems.length) {
    const route = state.route.name;
    let title = '这里还没有论文', desc = '导入快照或等待底库更新。';
    if (state.q) { title = '没有找到匹配的论文'; desc = '试试更短的关键词，或清除筛选条件。'; }
    else if (state.searchField === 'interests') { title = '关注点暂无匹配论文'; desc = '可以在设置 → 研究偏好中调整关注领域。'; }
    else if (route === 'starred') { title = '还没有收藏'; desc = '在论文卡片右侧点击星标。'; }
    else if (route === 'later') { title = '稍后读是空的'; desc = '看到感兴趣但暂时没空读的论文，可以先放进稍后读。'; }
    else if (route === 'watch') { title = '暂无追踪作者的论文'; desc = '在设置 → 作者追踪中添加你关注的作者或机构。'; }
    else if (route === 'archive') { title = '归档是空的'; desc = '归档的论文会从主列表隐藏。'; }
    node.innerHTML = emptyState('inbox', title, desc);
    return;
  }
  node.innerHTML = pageItems.map(p => paperCard(p, false)).join('');
  const selectAll = $('#select-all');
  if (selectAll) {
    const onPage = pageItems.filter(p => state.selected.has(p.key)).length;
    selectAll.checked = onPage === pageItems.length && pageItems.length > 0;
    selectAll.indeterminate = onPage > 0 && onPage < pageItems.length;
  }
}
function renderPagination() {
  const node = $('#pagination');
  if (!node) return;
  const total = state.filtered.length;
  const pages = Math.max(1, Math.ceil(total / state.pageSize));
  if (pages <= 1) { node.innerHTML = '<span class="page-info">共 ' + total + ' 篇</span>'; return; }
  node.innerHTML = '<button data-page="prev"' + (state.page <= 1 ? ' disabled' : '') + '>' + icon('chevron-left') + ' 上一页</button>' +
    '<span class="page-info">第 ' + state.page + ' / ' + pages + ' 页 · 共 ' + total + ' 篇</span>' +
    '<button data-page="next"' + (state.page >= pages ? ' disabled' : '') + '>下一页 ' + icon('chevron-right') + '</button>';
  node.querySelector('[data-page="prev"]').addEventListener('click', () => { if (state.page > 1) { state.page -= 1; renderPaperList(); renderPagination(); } });
  node.querySelector('[data-page="next"]').addEventListener('click', () => { if (state.page < pages) { state.page += 1; renderPaperList(); renderPagination(); } });
}
function renderSelectionBar() {
  const count = state.selected.size;
  $('#selection-mode').classList.toggle('hidden', count === 0);
  setText('#selection-count', '已选 ' + count + ' 篇 / 共 ' + state.filtered.length + ' 篇');
  const total = state.filtered.length;
  const allButton = $('#select-all-results');
  if (allButton) {
    const pageCount = Math.min(state.pageSize, total);
    allButton.classList.toggle('hidden', total <= pageCount || total === 0);
    allButton.disabled = count >= total;
    allButton.textContent = count >= total ? '已全选 ' + total + ' 篇' : '全选全部 ' + total + ' 篇';
  }
}
function renderFilters() {
  const vis = visibleJournals();
  $('#filter-journal').innerHTML = '<option value="">全部期刊</option>' + state.journals.filter(j => vis.has(j.id)).map(j =>
    '<option value="' + j.id + '"' + (Number(state.journalFilter) === Number(j.id) ? ' selected' : '') + '>' + esc(j.name) + '</option>').join('');
  const counts = {};
  state.papers.filter(p => vis.has(p.journal_id)).forEach(p => (p.topics || []).forEach(t => { counts[t] = (counts[t] || 0) + 1; }));
  const topics = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 40);
  $('#filter-topic').innerHTML = '<option value="">全部主题</option>' + topics.map(t =>
    '<option value="' + esc(t) + '"' + (state.topic === t ? ' selected' : '') + '>' + esc(t) + '（' + counts[t] + '）</option>').join('');
  $('#filter-range').value = state.range;
  $('#filter-sort').value = state.sort;
  $('#search-field').value = state.searchField;
  $('#search-input').value = state.q;
}
function renderSidebar() {
  const today = todayStr();
  const vis = visibleJournals();
  const stats = { today: 0, unread: 0, starred: 0, later: 0, total: 0, watch: 0 };
  state.papers.forEach(p => {
    if (!vis.has(p.journal_id)) return;
    const s = getState(p.key);
    stats.total += 1;
    if (String(p.first_seen_at || '').slice(0, 10) === today) stats.today += 1;
    if (!s.read_at && !s.archived) stats.unread += 1;
    if (s.starred && !s.archived) stats.starred += 1;
    if (s.read_later && !s.archived) stats.later += 1;
    if (!s.archived && watchMatch(p).length) stats.watch += 1;
  });
  [['#count-today', stats.today], ['#count-inbox', stats.unread], ['#count-starred', stats.starred],
   ['#count-later', stats.later], ['#count-watch', stats.watch], ['#count-all', stats.total]].forEach(pair => {
    const node = $(pair[0]);
    if (node) { node.textContent = pair[1]; node.classList.toggle('zero', Number(pair[1]) === 0); }
  });
  const nav = $('#journal-nav');
  const hidden = state.settings.hidden_journals || [];
  nav.innerHTML = state.journals.filter(j => hidden.indexOf(j.id) < 0).map(j =>
    '<a class="journal-nav-item" href="#/journal/' + j.id + '" data-journal="' + j.id + '" title="' + esc(j.name) + '">' +
    '<i class="journal-swatch" style="--jc:' + esc(j.color) + '"></i>' +
    '<span class="journal-nav-name">' + esc(j.name) + '</span></a>').join('');
  setText('#sidebar-user', state.user);
  setActiveNav(state.route.name);
}
function setActiveNav(name) {
  $$('.nav-item[data-nav]').forEach(node => node.classList.toggle('active', node.getAttribute('data-nav') === name));
  $$('.journal-nav-item').forEach(node => node.classList.toggle('active', name === 'journal' && Number(node.getAttribute('data-journal')) === Number(state.route.journalId)));
}
function renderHome() {
  const today = todayStr();
  const vis = visibleJournals();
  let todayCount = 0;
  const counts = {};
  state.papers.forEach(p => {
    if (!vis.has(p.journal_id)) return;
    if (String(p.first_seen_at || '').slice(0, 10) === today) todayCount += 1;
    (p.topics || []).forEach(t => { counts[t] = (counts[t] || 0) + 1; });
  });
  const d = new Date();
  const hour = d.getHours();
  const greeting = hour < 5 ? '夜深了' : hour < 11 ? '早上好' : hour < 13 ? '中午好' : hour < 18 ? '下午好' : '晚上好';
  $('#hero-kicker').textContent = d.getFullYear() + '年' + (d.getMonth() + 1) + '月' + d.getDate() + '日';
  $('#hero-title').innerHTML = greeting + '，<em>' + esc(state.user) + '</em>，库中有 ' + state.papers.length + ' 篇安全科学论文';
  setText('#hero-count', todayCount);
  setText('#stat-today', todayCount);
  setText('#stat-today-sub', todayCount > 0 ? '今天收录的论文' : '等待底库更新');
  setText('#stat-unread', (state.states.size ? Array.from(state.states.values()).filter(s => !s.read_at && !s.archived).length : 0));
  setText('#stat-starred', Array.from(state.states.values()).filter(s => s.starred && !s.archived).length);
  setText('#stat-journals', state.journals.filter(j => vis.has(j.id)).length);
  setText('#radar-caption', 'BASE · ' + (state.snapshotInfo ? state.snapshotInfo.exported_at.slice(0, 10) : '—'));
  const latest = state.papers.filter(p => vis.has(p.journal_id) && !getState(p.key).archived)
    .sort((a, b) => (Number(interestMatch(b)) - Number(interestMatch(a))) || String(b.first_seen_at).localeCompare(String(a.first_seen_at)))
    .slice(0, 8);
  $('#latest-list').innerHTML = latest.length ? latest.map(p => paperCard(p, true)).join('')
    : emptyState('book', '还没有论文', '请先在设置 → 数据中导入快照。');
  const topics = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 18);
  $('#topic-cloud').innerHTML = topics.map(t => '<button class="topic-chip" data-topic="' + esc(t) + '"><i style="--tc:' + topicColor(t) + '"></i>' + esc(t) + '<span>' + counts[t] + '</span></button>').join('')
    || '<p class="settings-meta">暂无主题数据。</p>';
  $$('#topic-cloud .topic-chip').forEach(chip => chip.addEventListener('click', () => {
    state.topic = chip.getAttribute('data-topic'); state.page = 1; navigate('#/all');
  }));
}
function renderListMeta() {
  const meta = VIEW_META[state.route.name] || VIEW_META.all;
  setText('#list-title', meta.title || '全部论文');
  setText('#list-eyebrow', meta.eyebrow || 'LITERATURE LIBRARY');
  if (state.route.name === 'journal' && state.route.journalId) {
    const j = state.journals.find(x => Number(x.id) === Number(state.route.journalId));
    if (j) { setText('#list-title', j.name); setText('#list-eyebrow', (j.publisher || 'JOURNAL')); }
  }
}
function renderAll() {
  renderSidebar();
  renderFilters();
  computeFiltered();
  renderListMeta();
  renderPaperList();
  renderPagination();
  renderSelectionBar();
  if (state.route.name === 'home') renderHome();
}

// ---------------- 详情抽屉 ----------------
function findPaper(key) { return state.papers.find(p => p.key === key); }
function formatCitation(paper) {
  if (paper.citation_text) return paper.citation_text;
  const authors = (paper.authors || []).join(', ') || '佚名';
  const year = String(paper.published_date || '').slice(0, 4) || 'n.d.';
  let text = authors + '. ' + (paper.title || '') + '[J]. ' + (paper.journal_name || '') + ', ' + year;
  if (paper.volume) text += ', ' + paper.volume;
  if (paper.issue) text += '(' + paper.issue + ')';
  if (paper.pages) text += ': ' + paper.pages;
  text += '.';
  if (paper.doi) text += ' DOI: ' + paper.doi + '.';
  return text;
}
function relatedPapers(paper, limit) {
  const authors = new Set((paper.authors || []).concat(paper.authors_en || []).map(a => String(a).toLowerCase()));
  const topics = new Set(paper.topics || []);
  const keywords = new Set((paper.keywords || []).concat(paper.keywords_en || []));
  return state.papers
    .filter(p => p.key !== paper.key && p.journal_id === paper.journal_id || (p.key !== paper.key && (p.topics || []).some(t => topics.has(t))))
    .map(p => {
      let score = 0; const reasons = [];
      const shared = (p.authors || []).concat(p.authors_en || []).filter(a => authors.has(String(a).toLowerCase()));
      if (shared.length) { score += 4; reasons.push('同作者'); }
      const st = (p.topics || []).filter(t => topics.has(t));
      if (st.length) { score += 2 + Math.min(st.length, 3); reasons.push('同主题'); }
      const sk = (p.keywords || []).concat(p.keywords_en || []).filter(k => keywords.has(k));
      if (sk.length) { score += 1 + Math.min(sk.length, 2); reasons.push('关键词相近'); }
      if (p.journal_id === paper.journal_id) { score += 1; reasons.push('同期刊'); }
      return { p, score, reasons };
    })
    .filter(r => r.score > 0)
    .sort((a, b) => b.score - a.score || String(b.p.first_seen_at).localeCompare(String(a.p.first_seen_at)))
    .slice(0, limit || 6);
}
function renderDrawer(key) {
  const raw = findPaper(key);
  if (!raw) return;
  const paper = paperWithState(raw);
  state.drawerKey = key;
  const color = paper.journal_color || '#c2552d';
  $('#drawer-journal').innerHTML = '<i style="--jc:' + esc(color) + '"></i>' + esc(paper.journal_name || '');
  const keywords = state.settings.watch_keywords || [];
  const meta = [
    ['期刊', paper.journal_name || '—'],
    ['发表', fmtDate(paper.published_date, paper.published_precision)],
    ['卷 / 期', [paper.volume, paper.issue].filter(Boolean).join(' / ') || '—'],
    ['页码', paper.pages || '—'],
    ['DOI', paper.doi || '—']
  ];
  const sourceUrl = paper.url || paper.doi_url || (paper.doi ? 'https://doi.org/' + paper.doi : '');
  const previewUrl = paper.preview_url || sourceUrl;
  const matchedWatch = watchMatch(paper);
  const abstract = paper.abstract || paper.abstract_en || '';
  const body =
    '<div class="drawer-meta-row">' +
      '<span class="badge ' + (isFresh(paper) ? 'new' : 'neutral') + '">' + (isFresh(paper) ? 'NEW' : 'IN LIBRARY') + '</span>' +
      (interestMatch(paper) ? '<span class="badge interest">关注</span>' : '') +
      (matchedWatch.length ? '<span class="badge watch">关注作者 · ' + esc(matchedWatch.slice(0, 2).join('、')) + '</span>' : '') +
      (paper.open_access ? '<span class="badge oa">OA</span>' : '') +
      '<span class="settings-meta">入库 ' + String(paper.first_seen_at || '').replace('T', ' ').slice(0, 16) + ' · 引用 ' + (Number(paper.citation_count) || 0) + '</span>' +
    '</div>' +
    '<h2 class="drawer-title">' + highlight(paper.title, keywords) + '</h2>' +
    (paper.title_en ? '<p class="drawer-title-en">' + esc(paper.title_en) + '</p>' : '') +
    '<p class="drawer-authors"><strong>' + esc(shortAuthors(paper.authors, 99)) + '</strong></p>' +
    (paper.authors_en && paper.authors_en.length ? '<p class="drawer-authors-en">' + esc(paper.authors_en.join(', ')) + '</p>' : '') +
    '<div class="drawer-meta">' + meta.map(pair => '<div class="meta-item"><span>' + esc(pair[0]) + '</span><strong>' + esc(pair[1]) + '</strong></div>').join('') + '</div>' +
    '<div class="drawer-link-row">' +
      (previewUrl ? '<a class="drawer-link" href="' + esc(previewUrl) + '" target="_blank" rel="noopener">在线浏览 ' + icon('external') + '</a>' : '') +
      (paper.pdf_url ? '<a class="drawer-link" href="' + esc(paper.pdf_url) + '" target="_blank" rel="noopener">PDF（官网）</a>' : '') +
      (sourceUrl ? '<a class="drawer-link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener" data-open-external="' + esc(paper.key) + '">原文链接 ' + icon('external') + '</a>' : '') +
    '</div>' +
    '<div class="drawer-citation"><div class="drawer-citation-head"><span>引用格式</span></div>' +
      '<div class="drawer-citation-row"><span class="drawer-citation-label">引用</span><p>' + esc(formatCitation(paper)) + '</p>' +
      '<button class="citation-copy" title="复制引用" aria-label="复制引用">' + icon('copy') + '</button></div></div>' +
    '<div class="drawer-section-title"><span>摘要</span>' + (paper.open_access ? '<span class="badge oa">OPEN ACCESS</span>' : '') + '</div>' +
    (abstract ? '<p class="drawer-abstract" id="drawer-abstract">' + highlight(abstract, keywords) + '</p>' : '<p class="drawer-abstract">摘要暂未收录。</p>') +
    ((paper.topics || []).length ? '<div class="drawer-section-title"><span>主题</span></div><div class="drawer-tags">' + paper.topics.map(t => '<span class="mini-tag"><i style="--tc:' + topicColor(t) + '"></i>' + esc(t) + '</span>').join('') + '</div>' : '') +
    ((paper.keywords || []).length ? '<div class="drawer-section-title"><span>作者关键词</span></div><div class="drawer-tags">' + paper.keywords.slice(0, 12).map(k => '<span class="keyword-tag">' + esc(k) + '</span>').join('') + '</div>' : '') +
    (paper.affiliations ? '<div class="drawer-section-title"><span>作者机构</span></div><p class="drawer-extra">' + esc(paper.affiliations) + '</p>' : '') +
    (paper.funding ? '<div class="drawer-section-title"><span>基金信息</span></div><p class="drawer-extra">' + esc(paper.funding) + '</p>' : '') +
    '<div class="drawer-section-title"><span>我的标签</span></div>' +
    '<div class="drawer-tags" id="user-tags">' + ((paper.user_tags || []).length ? paper.user_tags.map(t => '<span class="user-tag">' + esc(t) + '<button data-remove-tag="' + esc(t) + '" title="移除">×</button></span>').join('') : '<span class="settings-meta">还没有自定义标签</span>') + '</div>' +
    '<div class="tag-input-row"><input id="tag-input" placeholder="输入标签后回车，例如：待组会"><button id="add-tag">添加</button></div>' +
    '<div class="drawer-section-title"><span>阅读笔记</span><span id="note-status"></span></div>' +
    '<textarea class="drawer-notes" id="drawer-notes" placeholder="记录这篇文章对你的价值、方法或可复用的观点…">' + esc(paper.notes || '') + '</textarea>' +
    '<div class="drawer-section-title"><span>操作</span></div>' +
    '<div class="drawer-action-grid">' +
      '<button class="drawer-action ' + (paper.starred ? 'is-on' : '') + '" data-drawer-action="star">' + icon('star') + (paper.starred ? '已收藏' : '收藏') + '</button>' +
      '<button class="drawer-action ' + (paper.read_later ? 'is-on' : '') + '" data-drawer-action="later">' + icon('clock') + (paper.read_later ? '已在稍后读' : '稍后读') + '</button>' +
      '<button class="drawer-action" data-drawer-action="read">' + icon('check') + (paper.read_at ? '标为未读' : '标为已读') + '</button>' +
      '<button class="drawer-action" data-drawer-action="archive">' + icon('archive') + (paper.archived ? '取消归档' : '归档') + '</button>' +
    '</div>' +
    '<div class="drawer-section-title"><span>相关论文</span></div>' +
    '<p class="drawer-related-note">基于同作者、同主题、关键词与期刊在本地计算。</p>' +
    '<div id="drawer-related"></div>';
  $('#drawer-body').innerHTML = body;
  $('#drawer-layer').classList.remove('hidden');
  $('#drawer-layer').setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  const related = relatedPapers(raw, 6);
  $('#drawer-related').innerHTML = related.length ? related.map(r =>
    '<button class="related-item" data-open="' + esc(r.p.key) + '">' +
    '<span class="related-title">' + esc(r.p.title) + '</span>' +
    '<span class="related-meta">' + esc(r.p.journal_name || '') + ' · ' + esc(r.reasons.join('、')) + '</span></button>').join('')
    : '<p class="settings-meta">暂未找到足够相似的论文。</p>';
  $$('.citation-copy').forEach(btn => btn.addEventListener('click', () => copyText(formatCitation(paper))));
  const notes = $('#drawer-notes');
  const saveNotes = debounce(async () => {
    const s = getState(paper.key);
    s.notes = notes.value;
    await putState(s);
    setText('#note-status', '已保存');
    setTimeout(() => setText('#note-status', ''), 1800);
  }, 700);
  notes.addEventListener('input', saveNotes);
  const addTag = async () => {
    const input = $('#tag-input');
    const value = input.value.trim();
    if (!value) return;
    const s = getState(paper.key);
    s.user_tags = (s.user_tags || []).slice();
    if (s.user_tags.indexOf(value) < 0) s.user_tags.push(value);
    await putState(s);
    renderDrawer(paper.key);
  };
  $('#add-tag').addEventListener('click', addTag);
  $('#tag-input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } });
}
function closeDrawer() {
  state.drawerKey = null;
  $('#drawer-layer').classList.add('hidden');
  $('#drawer-layer').setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}
function moveDrawer(delta) {
  if (!state.drawerKey) return;
  const idx = state.filtered.findIndex(p => p.key === state.drawerKey);
  if (idx < 0) return;
  const next = state.filtered[idx + delta];
  if (next) renderDrawer(next.key);
}

// ---------------- 操作 ----------------
async function toggleAction(key, action) {
  const s = getState(key);
  if (action === 'star') s.starred = !s.starred;
  else if (action === 'later') s.read_later = !s.read_later;
  else if (action === 'read') s.read_at = s.read_at ? null : nowIso();
  else if (action === 'archive') s.archived = !s.archived;
  await putState(s);
  renderAll();
  if (state.drawerKey === key) renderDrawer(key);
}
async function bulkAction(action) {
  const keys = Array.from(state.selected);
  if (!keys.length) return;
  if (action === 'export') { exportPapers(keys.map(findPaper).filter(Boolean), 'csv'); return; }
  for (const key of keys) {
    const s = getState(key);
    if (action === 'read') s.read_at = s.read_at || nowIso();
    else if (action === 'unread') s.read_at = null;
    else if (action === 'star') s.starred = true;
    else if (action === 'later') s.read_later = true;
    else if (action === 'archive') s.archived = true;
    else if (action === 'unarchive') s.archived = false;
    await putState(s);
  }
  state.selected.clear();
  renderAll();
  toast('已更新 ' + keys.length + ' 篇论文');
}

// ---------------- 导出 ----------------
function exportPapers(items, fmt) {
  if (!items.length) { toast('没有可导出的论文', 'error'); return; }
  if (fmt === 'bibtex') {
    const entries = items.map(it => {
      const first = (it.authors || ['SafeLens'])[0];
      const last = String(first).split(/[\s,]+/).filter(Boolean).pop() || 'item';
      const year = String(it.published_date || '').slice(0, 4) || 'n.d.';
      const fields = {
        title: it.title || '',
        author: (it.authors || []).join(' and '),
        journal: it.journal_name || '',
        year: year,
        volume: it.volume || '',
        number: it.issue || '',
        pages: it.pages || '',
        doi: it.doi || '',
        url: it.url || ''
      };
      const body = Object.keys(fields).filter(k => fields[k]).map(k => '  ' + k + ' = {' + String(fields[k]).replace(/[{}]/g, '') + '}').join(',\n');
      return '@article{' + last + year + it.key.slice(-6) + ',\n' + body + '\n}';
    });
    download('safelens-web-' + todayStr() + '.bib', entries.join('\n\n') + '\n', 'application/x-bibtex');
    return;
  }
  const header = ['标题', '作者', '期刊', '发表日期', '卷', '期', '页码', 'DOI', '原文链接', '主题', '关键词', '被引', '入库时间'];
  const rows = items.map(it => [
    it.title || '', (it.authors || []).join('; '), it.journal_name || '',
    it.published_date || '', it.volume || '', it.issue || '', it.pages || '',
    it.doi || '', it.url || '', (it.topics || []).join(' / '), (it.keywords || []).join(' / '),
    Number(it.citation_count) || 0, it.first_seen_at || ''
  ]);
  const csv = [header].concat(rows).map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\n');
  download('safelens-web-' + todayStr() + '.csv', '﻿' + csv, 'text/csv');
}

// ---------------- 模态框 ----------------
function openModal(cfg) {
  $('#modal-kicker').textContent = cfg.kicker || 'SAFELENS';
  $('#modal-title').textContent = cfg.title || '';
  $('#modal-body').innerHTML = cfg.body || '';
  $('#modal-foot').innerHTML = cfg.foot || '';
  $('#modal-foot').classList.toggle('hidden', !cfg.foot);
  $('#modal-layer').classList.remove('hidden');
  $('#modal-layer').setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  if (cfg.onMount) cfg.onMount();
}
function closeModal() {
  $('#modal-layer').classList.add('hidden');
  $('#modal-layer').setAttribute('aria-hidden', 'true');
  if (!state.drawerKey) document.body.style.overflow = '';
}
function parseKeywords(value) {
  return String(value || '').split(/[,，;；]/).map(s => s.trim()).filter(Boolean).slice(0, 30);
}

// ---------------- 设置 ----------------
function openSettings(section) {
  const nav = [
    ['interests', '研究偏好'], ['watch', '作者追踪'], ['keywords', '关键词高亮'],
    ['journals', '期刊显隐'], ['data', '数据'], ['about', '关于']
  ];
  openModal({
    kicker: 'PREFERENCES',
    title: '设置',
    body: '<div class="settings-dialog"><nav class="settings-dialog-nav" id="settings-nav">' +
      nav.map(n => '<button class="settings-nav-item" data-sec="' + n[0] + '"><span>' + n[1] + '</span></button>').join('') +
      '</nav><div class="settings-dialog-pane" id="settings-pane"></div></div>',
    onMount: () => {
      $('#settings-nav').onclick = e => {
        const btn = e.target.closest('[data-sec]');
        if (btn) renderSettingsPane(btn.getAttribute('data-sec'));
      };
      renderSettingsPane(section || 'interests');
    }
  });
}
function renderSettingsPane(section) {
  $$('#settings-nav [data-sec]').forEach(b => b.classList.toggle('active', b.getAttribute('data-sec') === section));
  const pane = $('#settings-pane');
  if (section === 'interests') {
    const presets = ['大模型', '数据驱动', '智能体', '算法', '机器学习', '人工智能', '知识图谱', '数字孪生', '优化算法', '计算机视觉', '自然语言处理', '风险评估', '安全文化', '应急管理等'];
    const selected = state.settings.interests || [];
    pane.innerHTML = '<div class="settings-pane-title">研究偏好</div>' +
      '<p class="settings-section-desc">选择你在“关注优先”排序和关注点检索中优先看到的领域。</p>' +
      '<div class="interest-chips" id="interest-chips">' + presets.map(v =>
        '<button class="interest-chip ' + (selected.indexOf(v) >= 0 ? 'is-on' : '') + '" data-interest="' + esc(v) + '">' + esc(v) + '</button>').join('') + '</div>' +
      '<label class="field" style="margin-top:14px"><span>自定义关注词（逗号分隔）</span><input id="custom-interests" value="' + esc(selected.filter(v => presets.indexOf(v) < 0).join(', ')) + '"></label>' +
      '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-interests">保存偏好</button></div>';
    pane.onclick = e => { const chip = e.target.closest('[data-interest]'); if (chip) chip.classList.toggle('is-on'); };
    $('#save-interests').onclick = async () => {
      const chosen = $$('#interest-chips .is-on').map(c => c.getAttribute('data-interest'));
      const merged = Array.from(new Set(chosen.concat(parseKeywords($('#custom-interests').value)))).slice(0, 30);
      await saveSetting('interests', merged);
      toast('研究偏好已保存'); renderAll();
    };
  } else if (section === 'watch') {
    const authors = state.settings.watched_authors || [];
    pane.innerHTML = '<div class="settings-pane-title">作者追踪</div>' +
      '<p class="settings-section-desc">命中名单作者/机构的论文会进入“作者关注”，并在卡片上标注。</p>' +
      '<label class="field"><span>追踪名单（逗号分隔）</span><input id="watch-authors" value="' + esc(authors.join(', ')) + '" placeholder="例如：Reason, 傅贵, 某大学"></label>' +
      '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-watch">保存名单</button></div>';
    $('#save-watch').onclick = async () => {
      await saveSetting('watched_authors', parseKeywords($('#watch-authors').value));
      toast('作者追踪名单已保存'); renderAll();
    };
  } else if (section === 'keywords') {
    pane.innerHTML = '<div class="settings-pane-title">关键词高亮</div>' +
      '<p class="settings-section-desc">标题与摘要中的这些词会高亮显示，方便扫读。</p>' +
      '<label class="field"><span>高亮关键词（逗号分隔）</span><input id="hl-keywords" value="' + esc((state.settings.watch_keywords || []).join(', ')) + '"></label>' +
      '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-hl">保存</button></div>';
    $('#save-hl').onclick = async () => {
      await saveSetting('watch_keywords', parseKeywords($('#hl-keywords').value));
      toast('高亮关键词已保存'); renderAll();
    };
  } else if (section === 'journals') {
    const hidden = state.settings.hidden_journals || [];
    pane.innerHTML = '<div class="settings-pane-title">期刊显隐</div>' +
      '<p class="settings-section-desc">取消勾选的期刊会从你的界面隐藏（不影响其他用户，也不删除数据）。</p>' +
      state.journals.map(j =>
        '<label class="journal-toggle-row"><input type="checkbox" data-jt="' + j.id + '"' + (hidden.indexOf(j.id) < 0 ? ' checked' : '') + '>' +
        '<span class="journal-swatch" style="--jc:' + esc(j.color) + '"></span><span>' + esc(j.name) + '</span></label>').join('');
    pane.onchange = async e => {
      const box = e.target.closest('[data-jt]');
      if (!box) return;
      let list = (state.settings.hidden_journals || []).slice();
      const id = Number(box.getAttribute('data-jt'));
      if (box.checked) list = list.filter(x => x !== id);
      else if (list.indexOf(id) < 0) list.push(id);
      await saveSetting('hidden_journals', list);
      toast('期刊显隐已更新'); renderAll();
    };
  } else if (section === 'data') {
    pane.innerHTML = '<div class="settings-pane-title">数据</div>' +
      '<p class="settings-section-desc">底库快照更新于 ' + esc(state.settings.snapshot_at || '—') + '。你的收藏、笔记与设置只存在本机浏览器。</p>' +
      '<div class="settings-list">' +
      '<button class="btn btn-quiet" id="btn-import-snapshot">' + icon('upload') + ' 导入本地 SafeLens 导出的快照（JSON）</button>' +
      '<button class="btn btn-quiet" id="btn-export-my">' + icon('download') + ' 导出我的数据（收藏/笔记/设置）</button>' +
      '<button class="btn btn-quiet" id="btn-import-my">' + icon('upload') + ' 导入我的数据</button>' +
      '</div>' +
      '<div class="danger-zone"><p class="settings-section-desc">清空当前用户的全部个人数据（收藏、笔记、标签、设置），底库不受影响。</p>' +
      '<div class="settings-pane-actions"><button class="btn btn-danger" id="btn-reset-my">清空我的数据</button></div></div>';
    $('#btn-import-snapshot').onclick = () => { state.importMode = 'snapshot'; $('#import-file').click(); };
    $('#btn-export-my').onclick = exportMyData;
    $('#btn-import-my').onclick = () => { state.importMode = 'mine'; $('#import-file').click(); };
    $('#btn-reset-my').onclick = async () => {
      if (!window.confirm('确定清空用户 ' + state.user + ' 的全部个人数据吗？此操作不可撤销。')) return;
      const rows = await idbAll('states');
      for (const r of rows.filter(r => r.u === state.user)) await idbDel('states', [r.u, r.p]);
      const sets = await idbAll('settings');
      for (const r in sets.filter ? sets.filter(r => r.u === state.user) : []) await idbDel('settings', [r.u, r.k]);
      await loadSettings(); await loadStates(); renderAll(); closeModal();
      toast('个人数据已清空');
    };
  } else {
    pane.innerHTML = '<div class="settings-pane-title">关于</div>' +
      '<div class="about-card about-card-modal"><div class="about-mark">S</div><div>' +
      '<h3>SafeLens Web · 安全科学论文雷达（在线版）</h3>' +
      '<p>论文底库由公开期刊元数据组成；你的账号、阅读状态与笔记全部保存在本机浏览器，不会上传。</p>' +
      '<p class="about-meta">底库更新时间：' + esc(state.settings.snapshot_at || '—') + ' · 论文总数：' + state.papers.length + '</p>' +
      '</div></div>';
  }
}
async function exportMyData() {
  const rows = await idbAll('states');
  const sets = await idbAll('settings');
  const payload = {
    app: 'safelens-web', kind: 'user-data', version: 1, user: state.user, exported_at: nowIso(),
    settings: sets.filter(r => r.u === state.user).map(r => ({ k: r.k, v: r.v })),
    states: rows.filter(r => r.u === state.user).map(r => {
      const s = Object.assign({}, r); delete s.u; return s;
    })
  };
  download('safelens-web-mydata-' + state.user + '-' + todayStr() + '.json', JSON.stringify(payload, null, 1), 'application/json');
  toast('我的数据已导出');
}
async function handleImportFile(file) {
  try {
    const payload = JSON.parse(await file.text());
    if (state.importMode === 'snapshot') {
      const papers = Array.isArray(payload) ? payload : (payload.papers || []);
      if (!papers.length) throw new Error('文件中没有论文数据');
      const r = await mergeSnapshot(papers, null);
      refreshJournalMap(); renderAll();
      toast('快照导入完成：新增 ' + r.added + ' 篇，合并 ' + r.updated + ' 篇');
    } else {
      if (payload.kind !== 'user-data') throw new Error('这不是“我的数据”导出文件');
      const keyMap = new Map(state.papers.map(p => [p.key, p.key]));
      let n = 0;
      for (const s of payload.states || []) {
        if (!keyMap.has(s.p)) continue;
        await putState(Object.assign({}, s, { u: state.user }));
        n += 1;
      }
      for (const row of payload.settings || []) {
        await saveSetting(row.k, row.v);
      }
      await loadSettings(); renderAll();
      toast('我的数据已导入：恢复 ' + n + ' 条状态');
    }
  } catch (e) {
    toast('导入失败：' + e.message, 'error');
  } finally {
    state.importMode = null;
    $('#import-file').value = '';
  }
}

// ---------------- 用户菜单 ----------------
function openUserMenu() {
  openModal({
    kicker: 'ACCOUNT',
    title: '账户与数据',
    body: '<p class="modal-intro">当前用户：<strong>' + esc(state.user) + '</strong>。数据保存在本机浏览器，换设备请先“导出我的数据”。</p>' +
      '<div class="settings-list">' +
      '<button class="btn btn-quiet" id="um-export">' + icon('download') + ' 导出我的数据</button>' +
      '<button class="btn btn-quiet" id="um-import">' + icon('upload') + ' 导入我的数据</button>' +
      '<button class="btn btn-quiet" id="um-switch">切换用户</button>' +
      '<button class="btn btn-danger" id="um-logout">退出登录</button></div>',
    onMount: () => {
      $('#um-export').onclick = exportMyData;
      $('#um-import').onclick = () => { closeModal(); state.importMode = 'mine'; $('#import-file').click(); };
      $('#um-switch').onclick = () => { closeModal(); logout(false); };
      $('#um-logout').onclick = () => { closeModal(); logout(true); };
    }
  });
}
function logout(clearSession) {
  if (clearSession) { try { localStorage.removeItem('slw.session'); } catch (e) {} }
  state.user = null; state.states = new Map(); state.settings = null;
  $('#app-shell').classList.add('hidden');
  $('#auth-view').classList.remove('hidden');
  setAuthMode('login');
}

// ---------------- 路由 ----------------
function parseRoute() {
  const raw = (location.hash || '#/home').replace(/^#\/?/, '');
  const parts = raw.split('/').filter(Boolean);
  if (parts[0] === 'journal' && parts[1]) return { name: 'journal', journalId: Number(parts[1]) };
  if (['home', 'today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive'].indexOf(parts[0]) >= 0) return { name: parts[0], journalId: null };
  return { name: 'home', journalId: null };
}
function navigate(hash) { if (location.hash === hash) handleRoute(); else location.hash = hash; }
function handleRoute() {
  const route = parseRoute();
  const changed = state.route.name !== route.name || Number(state.route.journalId) !== Number(route.journalId);
  state.route = route;
  if (changed) state.page = 1;
  $$('.view').forEach(n => n.classList.add('hidden'));
  if (route.name === 'home') {
    $('#view-home').classList.remove('hidden');
    setText('#page-title', '总览');
    renderAll();
  } else {
    $('#view-papers').classList.remove('hidden');
    renderAll();
  }
  window.scrollTo({ top: 0 });
}

// ---------------- 事件绑定 ----------------
function bindEvents() {
  window.addEventListener('hashchange', handleRoute);
  $('#theme-toggle').addEventListener('click', async () => {
    const dark = state.settings.theme !== 'dark';
    await saveSetting('theme', dark ? 'dark' : 'light');
    applyTheme();
  });
  $('#sidebar-settings').addEventListener('click', () => openSettings('interests'));
  $('#user-chip').addEventListener('click', openUserMenu);
  $('#mobile-menu').addEventListener('click', () => document.body.classList.add('nav-open'));
  $('#mobile-scrim').addEventListener('click', () => document.body.classList.remove('nav-open'));
  $('#search-field').addEventListener('change', e => { state.searchField = e.target.value; state.page = 1; renderAll(); });
  $('#search-submit').addEventListener('click', () => { state.q = $('#search-input').value.trim(); state.page = 1; renderAll(); });
  $('#search-input').addEventListener('keydown', e => { if (e.key === 'Enter') { state.q = e.target.value.trim(); state.page = 1; renderAll(); } });
  $('#search-input').addEventListener('input', debounce(e => { state.q = e.target.value.trim(); state.page = 1; renderAll(); }, 350));
  $('#clear-search').addEventListener('click', () => {
    state.q = ''; state.topic = ''; state.journalFilter = ''; state.range = 'all'; state.searchField = 'all';
    state.page = 1; renderAll();
  });
  $('#filter-journal').addEventListener('change', e => { state.journalFilter = e.target.value; state.page = 1; renderAll(); });
  $('#filter-topic').addEventListener('change', e => { state.topic = e.target.value; state.page = 1; renderAll(); });
  $('#filter-range').addEventListener('change', e => { state.range = e.target.value; state.page = 1; renderAll(); });
  $('#filter-sort').addEventListener('change', e => { state.sort = e.target.value; state.page = 1; renderAll(); });
  $('#select-all').addEventListener('change', e => {
    const items = state.filtered.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);
    items.forEach(p => { if (e.target.checked) state.selected.add(p.key); else state.selected.delete(p.key); });
    renderPaperList(); renderSelectionBar();
  });
  $('#select-all-results').addEventListener('click', () => {
    state.filtered.forEach(p => state.selected.add(p.key));
    renderPaperList(); renderSelectionBar();
    toast('已选择全部 ' + state.selected.size + ' 篇');
  });
  $('#selection-clear').addEventListener('click', () => { state.selected.clear(); renderPaperList(); renderSelectionBar(); });
  $$('#selection-mode [data-bulk]').forEach(b => b.addEventListener('click', () => bulkAction(b.getAttribute('data-bulk'))));
  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#drawer-scrim').addEventListener('click', closeDrawer);
  $('#drawer-prev').addEventListener('click', () => moveDrawer(-1));
  $('#drawer-next').addEventListener('click', () => moveDrawer(1));
  $('#drawer-body').addEventListener('click', e => {
    const action = e.target.closest('[data-drawer-action]');
    if (action) { toggleAction(state.drawerKey, action.getAttribute('data-drawer-action')); return; }
    const remove = e.target.closest('[data-remove-tag]');
    if (remove) {
      const s = getState(state.drawerKey);
      s.user_tags = (s.user_tags || []).filter(t => t !== remove.getAttribute('data-remove-tag'));
      putState(s).then(() => renderDrawer(state.drawerKey));
    }
  });
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-scrim').addEventListener('click', closeModal);
  $('#import-file').addEventListener('change', e => {
    const file = e.target.files && e.target.files[0];
    if (file) handleImportFile(file);
  });
  document.addEventListener('click', e => {
    const open = e.target.closest('[data-open]');
    if (open) { renderDrawer(open.getAttribute('data-open')); return; }
    const card = e.target.closest('.paper-card');
    if (card && !e.target.closest('input, button, a, label')) {
      renderDrawer(card.getAttribute('data-key')); return;
    }
    const action = e.target.closest('[data-action]');
    if (action) { e.preventDefault(); toggleAction(action.getAttribute('data-key'), action.getAttribute('data-action')); return; }
    const external = e.target.closest('[data-open-external]');
    if (external) {
      const key = external.getAttribute('data-open-external');
      const s = getState(key);
      if (!s.read_at) { s.read_at = nowIso(); putState(s).then(renderAll); }
      return;
    }
    const select = e.target.closest('[data-select]');
    if (select) {
      const key = select.getAttribute('data-select');
      if (select.checked) state.selected.add(key); else state.selected.delete(key);
      const c = select.closest('.paper-card');
      if (c) c.classList.toggle('selected', select.checked);
      renderSelectionBar();
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeModal(); closeDrawer(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      navigate('#/all');
      setTimeout(() => $('#search-input') && $('#search-input').focus(), 60);
      return;
    }
    if (state.drawerKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      if (e.key === 'ArrowLeft') moveDrawer(-1);
      if (e.key === 'ArrowRight') moveDrawer(1);
    }
  });
}
function applyTheme() {
  const dark = state.settings && state.settings.theme === 'dark';
  if (dark) document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem('slw.theme', dark ? 'dark' : 'light'); } catch (e) {}
}

// ---------------- 登录界面 ----------------
let authMode = 'login';
function setAuthMode(mode) {
  authMode = mode;
  $('#tab-login').classList.toggle('active', mode === 'login');
  $('#tab-register').classList.toggle('active', mode === 'register');
  $('#auth-submit').textContent = mode === 'login' ? '登录' : '注册并进入';
  $('#auth-note').textContent = mode === 'login'
    ? '纯本地账号：档案与数据只存在这台电脑的浏览器里，不会上传到任何服务器。'
    : '注册即在本机创建档案；密码经散列后保存，忘记密码无法找回（可新建账户后导入备份数据）。';
}
function bindAuth() {
  $('#tab-login').addEventListener('click', () => setAuthMode('login'));
  $('#tab-register').addEventListener('click', () => setAuthMode('register'));
  const submit = async () => {
    const name = $('#auth-name').value.trim();
    const pass = $('#auth-pass').value;
    if (!name) { toast('请输入用户名', 'error'); return; }
    if (pass.length < 4) { toast('密码至少 4 位', 'error'); return; }
    const btn = $('#auth-submit');
    btn.disabled = true;
    try {
      if (authMode === 'register') await registerUser(name, pass);
      else await loginUser(name, pass);
      try { localStorage.setItem('slw.session', name); } catch (e) {}
      await enterApp(name);
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  };
  $('#auth-submit').addEventListener('click', submit);
  $('#auth-pass').addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  $('#auth-name').addEventListener('keydown', e => { if (e.key === 'Enter') $('#auth-pass').focus(); });
}

// ---------------- 启动 ----------------
async function enterApp(name) {
  state.user = name;
  await loadSettings();
  await loadStates();
  applyTheme();
  $('#auth-view').classList.add('hidden');
  $('#app-shell').classList.remove('hidden');
  if (!location.hash) location.hash = '#/home';
  handleRoute();
  syncSnapshot().then(() => { if (state.user) renderAll(); });
}
async function boot() {
  idb = await openDb();
  bindAuth();
  bindEvents();
  const stored = await idbGet('meta', 'snapshot');
  if (stored && stored.v) state.snapshotInfo = stored.v;
  await loadBase();
  let session = null;
  try { session = localStorage.getItem('slw.session'); } catch (e) {}
  if (session) {
    const profile = await idbGet('profiles', session);
    if (profile) { await enterApp(session); return; }
  }
  $('#auth-view').classList.remove('hidden');
}
document.addEventListener('DOMContentLoaded', () => { boot().catch(e => toast('启动失败：' + e.message, 'error')); });

'use strict';

const $ = function (selector, root) { return (root || document).querySelector(selector); };
const $$ = function (selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); };

const state = {
  ready: false,
  summary: null,
  journals: [],
  topics: [],
  settings: {},
  scheduler: null,
  syncState: null,
  route: { name: 'home', journalId: null },
  q: '',
  topic: '',
  journalFilter: '',
  range: 'all',
  sort: 'interest',
  searchField: 'all',
  view: 'all',
  page: 1,
  pageSize: 20,
  data: { items: [], total: 0, pages: 1, page: 1, page_size: 20 },
  selected: new Set(),
  drawerPaper: null,
  drawerOpen: false,
  digest: null,
  digestScope: 'today',
  digestFocus: '',
  poll: null,
  loading: false,
  currentModal: null,
  journalGroups: { domestic: true, international: true },
  syncConfirm: false
};

const VIEW_META = {
  home: { title: '总览', eyebrow: 'SAFE SCIENCE · LITERATURE RADAR' },
  today: { title: '今日新增', eyebrow: 'NEW ARRIVALS · TODAY', desc: '今天首次发现并收录的论文。每日 08:00 自动检查后更新。' },
  inbox: { title: '待读', eyebrow: 'INBOX · UNREAD', desc: '还没有阅读的论文。打开原文时会自动标记为已读。' },
  starred: { title: '收藏', eyebrow: 'STARRED · FOCUS', desc: '你标记为重点的论文，适合反复回看与引用。' },
  later: { title: '稍后读', eyebrow: 'READ LATER', desc: '先收起来，等有空再读的论文。' },
  watch: { title: '作者关注', eyebrow: 'WATCHED AUTHORS', desc: '作者或机构命中你追踪名单的论文，入库时自动汇总到这里。' },
  all: { title: '全部论文', eyebrow: 'LITERATURE LIBRARY', desc: '按入库时间排序，最新发现的论文在最上方。' },
  archive: { title: '归档', eyebrow: 'ARCHIVE', desc: '已从主列表隐藏的论文，仍可搜索和恢复。' },
  journals: { title: '期刊监控', eyebrow: 'SOURCE LIBRARY', desc: '' },
  settings: { title: '设置与数据', eyebrow: 'PREFERENCES & DATA', desc: '' }
};

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^$()|[\]\\]/g, '\\$&');
}

function highlight(text, keywords) {
  const safe = esc(text || '');
  const words = (keywords || []).map(function (k) { return String(k || '').trim(); })
    .filter(function (k) { return k.length > 1; })
    .map(function (k) { return escapeRegExp(esc(k)); });
  if (!words.length) return safe;
  try {
    return safe.replace(new RegExp('(' + words.join('|') + ')', 'gi'), '<mark>$1</mark>');
  } catch (err) {
    return safe;
  }
}

function parseLocal(value) {
  if (!value) return null;
  const date = new Date(String(value).replace(' ', 'T'));
  return isNaN(date.getTime()) ? null : date;
}

function fmtDate(value, precision) {
  if (!value) return '日期待定';
  const parts = String(value).split('-');
  if (parts.length < 2) return value;
  if (precision === 'month') return parts[0] + '年' + Number(parts[1]) + '月';
  if (precision === 'year') return parts[0] + '年';
  return parts[0] + '-' + parts[1] + '-' + (parts[2] || '01');
}

function fmtCnDate(value) {
  if (!value) return '';
  const parts = String(value).slice(0, 10).split('-');
  if (parts.length < 3) return String(value).slice(0, 10);
  return parts[0] + '年' + parts[1] + '月' + parts[2] + '日';
}

function fmtDateTime(value) {
  if (!value) return '—';
  return String(value).replace('T', ' ').slice(0, 16);
}

function relativeTime(value) {
  const date = parseLocal(value);
  if (!date) return '—';
  const diff = Date.now() - date.getTime();
  if (diff < 0) return fmtDateTime(value);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return minutes + ' 分钟前';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' 小时前';
  const days = Math.floor(hours / 24);
  if (days === 1) return '昨天';
  if (days < 7) return days + ' 天前';
  return fmtDate(String(value).slice(0, 10));
}

function weekdayCn(date) {
  return ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'][date.getDay()];
}

function todayCn() {
  const date = new Date();
  return date.getFullYear() + '年' + (date.getMonth() + 1) + '月' + date.getDate() + '日';
}

function greetingCn(date) {
  const hour = (date || new Date()).getHours();
  if (hour < 5) return '夜深了';
  if (hour < 11) return '早上好';
  if (hour < 13) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

function isBackfill(paper) {
  if (!paper || !paper.is_new || !paper.published_date) return false;
  const published = new Date(paper.published_date + 'T00:00:00');
  const seen = paper.first_seen_at ? new Date(paper.first_seen_at) : new Date();
  return (seen.getTime() - published.getTime()) > 45 * 86400000;
}

function isFresh(paper) {
  if (!paper || !paper.is_new || paper.read_at) return false;
  const seen = parseLocal(paper.first_seen_at);
  return !!seen && (Date.now() - seen.getTime()) < 3 * 86400000;
}

function shortAuthors(authors, max) {
  const list = authors || [];
  if (!list.length) return '作者信息待补全';
  const limit = max || 3;
  if (list.length <= limit) return list.join(', ');
  return list.slice(0, limit).join(', ') + ' 等 ' + list.length + ' 位作者';
}

function isDomesticJournal(journal) {
  if (!journal) return false;
  if (['cssjj', 'aqsckj', 'cast', 'cnki'].indexOf(journal.feed_type) >= 0) return true;
  return /[\u4e00-\u9fa5]/.test(journal.name || '');
}

function journalById(id) {
  return state.journals.find(function (j) { return Number(j.id) === Number(id); }) || null;
}

function topicColor(name) {
  const topic = (state.topics || []).find(function (t) { return t.name === name; });
  return topic ? topic.color : '#9b9a88';
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = value;
}

function icon(name) {
  return '<svg><use href="#i-' + name + '"></use></svg>';
}

function emptyState(iconName, title, desc, action) {
  return '<div class="empty-state">' +
    '<div class="empty-icon">' + icon(iconName) + '</div>' +
    '<h3>' + esc(title) + '</h3>' +
    '<p>' + esc(desc || '') + '</p>' +
    (action || '') +
    '</div>';
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body && typeof opts.body !== 'string') {
    opts.body = JSON.stringify(opts.body);
    opts.headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, opts);
  let payload = null;
  const type = response.headers.get('Content-Type') || '';
  if (type.indexOf('application/json') >= 0) {
    try { payload = await response.json(); } catch (err) { payload = null; }
  } else {
    payload = await response.text();
  }
  if (!response.ok) {
    const message = payload && payload.error ? payload.error : ('请求失败（' + response.status + '）');
    throw new Error(message);
  }
  return payload;
}

function toast(message, type) {
  const stack = $('#toast-stack');
  const node = document.createElement('div');
  node.className = 'toast ' + (type || '');
  node.textContent = message;
  stack.appendChild(node);
  window.setTimeout(function () {
    node.classList.add('leaving');
    window.setTimeout(function () { node.remove(); }, 240);
  }, type === 'error' ? 5200 : 3200);
}

function debounce(fn, wait) {
  let timer = null;
  return function () {
    const args = arguments;
    const self = this;
    clearTimeout(timer);
    timer = setTimeout(function () { fn.apply(self, args); }, wait);
  };
}

function parseRoute() {
  const raw = (location.hash || '#/home').replace(/^#\/?/, '');
  const parts = raw.split('/').filter(Boolean);
  if (parts[0] === 'journal' && parts[1]) {
    return { name: 'journal', journalId: Number(parts[1]) };
  }
  if (['home', 'today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive', 'journals', 'settings'].indexOf(parts[0]) >= 0) {
    return { name: parts[0], journalId: null };
  }
  return { name: 'home', journalId: null };
}

function navigate(hash) {
  if (location.hash === hash) handleRoute(true);
  else location.hash = hash;
}

function setActiveNav(name) {
  $$('.nav-item[data-nav]').forEach(function (node) {
    node.classList.toggle('active', node.getAttribute('data-nav') === name);
  });
  $$('.journal-nav-item').forEach(function (node) {
    node.classList.toggle('active', name === 'journal' && Number(node.getAttribute('data-journal')) === Number(state.route.journalId));
  });
}

function closeMobileNav() {
  document.body.classList.remove('nav-open');
}

function applyTheme(theme) {
  const dark = theme === 'dark';
  if (dark) document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem('safelens.theme', dark ? 'dark' : 'light'); } catch (err) { /* ignore */ }
  const button = $('#theme-toggle');
  if (button) {
    button.title = dark ? '切换到浅色主题' : '切换到深色主题';
    button.setAttribute('aria-label', button.title);
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', dark ? '#11151a' : '#ffffff');
}

function bindEvents() {
  window.addEventListener('hashchange', function () { handleRoute(false); });
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  $('#theme-toggle').addEventListener('click', function () {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });

  $('#sync-hub-button').addEventListener('click', function (event) {
    event.stopPropagation();
    handleSyncHubClick();
  });
  $('#sync-pop-close').addEventListener('click', closeSyncPopover);
  $('#sync-pop-start').addEventListener('click', function () { startSync(null); });
  $('#sync-pop-cancel').addEventListener('click', cancelSync);
  $('#sync-pop-confirm-cancel').addEventListener('click', closeSyncPopover);
  $('#sync-pop-confirm-start').addEventListener('click', function () { startSync(null, true); });
  $('#sync-pop-settings').addEventListener('click', function () {
    closeSyncPopover();
    openSettingsDialog('schedule');
  });
  $('#sidebar-settings').addEventListener('click', function () { openSettingsDialog(); });
  $('#journal-settings-link').addEventListener('click', function () { openSettingsDialog('schedule'); });

  $('#hero-digest').addEventListener('click', function () { openDigest('today'); });
  $('#latest-view-all').addEventListener('click', function (event) {
    event.preventDefault();
    state.searchField = 'all';
    state.sort = 'interest';
    state.page = 1;
    navigate('#/today');
  });

  $('#journal-nav').addEventListener('click', function (event) {
    const toggle = event.target.closest('[data-group-toggle]');
    if (!toggle) return;
    const key = toggle.getAttribute('data-group-toggle');
    state.journalGroups[key] = !state.journalGroups[key];
    try { localStorage.setItem('safelens.journalGroups', JSON.stringify(state.journalGroups)); } catch (err) { /* 忽略存储失败 */ }
    renderSidebar();
  });

  $('#mobile-menu').addEventListener('click', function () { document.body.classList.add('nav-open'); });
  $('#mobile-scrim').addEventListener('click', closeMobileNav);

  $('#search-field').addEventListener('change', function (event) {
    state.searchField = event.target.value;
    updateSearchFieldHint();
    state.page = 1;
    if (state.q) loadPapers();
  });

  function applySearch() {
    state.q = $('#search-input').value.trim();
    state.page = 1;
    loadPapers();
  }

  $('#search-submit').addEventListener('click', applySearch);
  $('#search-input').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      applySearch();
    }
  });
  $('#search-input').addEventListener('input', debounce(function (event) {
    state.q = event.target.value.trim();
    state.page = 1;
    loadPapers();
  }, 420));

  $('#clear-search').addEventListener('click', function () {
    state.q = '';
    state.searchField = 'all';
    state.journalFilter = '';
    state.topic = '';
    state.range = 'all';
    state.sort = 'interest';
    state.page = 1;
    $('#search-input').value = '';
    $('#search-field').value = 'all';
    updateSearchFieldHint();
    loadPapers();
  });

  $('#filter-journal').addEventListener('change', function (event) {
    state.journalFilter = event.target.value;
    state.page = 1;
    loadPapers();
  });
  $('#filter-topic').addEventListener('change', function (event) {
    state.topic = event.target.value;
    state.page = 1;
    loadPapers();
  });
  $('#filter-range').addEventListener('change', function (event) {
    state.range = event.target.value;
    state.page = 1;
    loadPapers();
  });
  $('#filter-sort').addEventListener('change', function (event) {
    state.sort = event.target.value;
    state.page = 1;
    loadPapers();
  });

  $('#select-all-results').addEventListener('click', selectAllResults);

  $('#select-all').addEventListener('change', function (event) {
    const ids = state.data.items.map(function (p) { return p.id; });
    if (event.target.checked) ids.forEach(function (id) { state.selected.add(id); });
    else ids.forEach(function (id) { state.selected.delete(id); });
    renderPaperList();
    renderSelectionBar();
  });

  $('#selection-mode').addEventListener('click', function (event) {
    const button = event.target.closest('[data-bulk]');
    if (!button) return;
    bulkAction(button.getAttribute('data-bulk'));
  });
  $('#selection-clear').addEventListener('click', function () {
    state.selected.clear();
    renderPaperList();
    renderSelectionBar();
  });

  $('#add-journal-button').addEventListener('click', function () { openJournalModal(null); });
  $('#journal-grid').addEventListener('change', function (event) {
    const input = event.target.closest('[data-journal-toggle]');
    if (!input) return;
    toggleJournal(Number(input.getAttribute('data-journal-toggle')), input.checked);
  });
  $('#journal-grid').addEventListener('click', function (event) {
    const sync = event.target.closest('[data-sync-journal]');
    if (sync) { startSync(Number(sync.getAttribute('data-sync-journal'))); return; }
    const edit = event.target.closest('[data-edit-journal]');
    if (edit) { openJournalModal(journalById(Number(edit.getAttribute('data-edit-journal')))); return; }
  });



  $('#drawer-close').addEventListener('click', closeDrawer);
  $('#drawer-scrim').addEventListener('click', closeDrawer);
  $('#drawer-prev').addEventListener('click', function () { moveDrawer(-1); });
  $('#drawer-next').addEventListener('click', function () { moveDrawer(1); });
  $('#drawer-body').addEventListener('click', handleDrawerClick);
  $('#drawer-body').addEventListener('keydown', handleDrawerKeydown);

  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-scrim').addEventListener('click', closeModal);
  $('#export-menu-button').addEventListener('click', openExportModal);
  window.setInterval(quietRefresh, 120000);

  document.addEventListener('click', function (event) {
    if (!event.target.closest('.sync-hub-wrap') && !event.target.closest('#sync-popover')) closeSyncPopover();
  });

  document.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      const current = parseRoute().name;
      if (['today', 'inbox', 'starred', 'later', 'all', 'archive', 'journal'].includes(current)) {
        $('#search-input').focus();
        $('#search-input').select();
      } else {
        navigate('#/all');
        window.setTimeout(function () {
          const input = $('#search-input');
          if (input) { input.focus(); input.select(); }
        }, 350);
      }
      return;
    }
    if (event.key === 'Escape') {
      const popover = $('#sync-popover');
      if (popover && !popover.classList.contains('hidden')) { closeSyncPopover(); return; }
      if (state.currentModal) { closeModal(); return; }
      if (state.drawerOpen) { closeDrawer(); return; }
      closeMobileNav();
    }
    if (state.drawerOpen && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      if (event.key === 'ArrowLeft') { event.preventDefault(); moveDrawer(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); moveDrawer(1); }
    }
  });
}

function connectAppEvents() {
  if (window.__safelensEvents || typeof EventSource === 'undefined') return;
  if (new URLSearchParams(location.search).get('noevents')) return;
  const source = new EventSource('/api/app/events');
  source.addEventListener('reload', function () {
    source.close();
    window.location.reload();
  });
  window.__safelensEvents = source;
}

async function loadBootstrap() {
  try {
    const data = await api('/api/bootstrap');
    state.summary = data.summary;
    state.journals = data.journals || [];
    state.topics = data.topics || [];
    state.settings = data.settings || {};
    state.scheduler = data.scheduler || null;
    state.syncState = data.sync_state || null;
    state.ready = true;
    renderShell();
    if (state.syncState && state.syncState.running) startPolling();
    connectAppEvents();
    await handleRoute(true);
    const openId = new URLSearchParams(location.search).get('paper');
    if (openId) window.setTimeout(function () { openDrawerById(Number(openId)); }, 350);
    const digestScope = new URLSearchParams(location.search).get('digest');
    const digestFocus = new URLSearchParams(location.search).get('digestfocus');
    if (digestScope) window.setTimeout(function () { openDigest(digestScope, digestFocus); }, 450);
  } catch (err) {
    toast('无法加载工作台：' + err.message, 'error');
    setText('#hero-title', '工作台连接失败');
    setText('#hero-sub', '请确认后台服务正在运行，然后刷新页面。');
  }
}

async function refreshSummary() {
  try {
    const data = await api('/api/summary');
    state.summary = data.summary;
    state.topics = data.topics || [];
    renderShell();
    if (state.route.name === 'home') renderDashboard();
  } catch (err) { console.error(err); }
}

async function quietRefresh() {
  if (state.syncState && state.syncState.running) return;
  try {
    const data = await api('/api/summary');
    state.summary = data.summary;
    state.topics = data.topics || [];
    renderSidebar();
    if (state.route.name === 'home') renderDashboard();
  } catch (err) { /* 后台轮询不打扰用户 */ }
}

async function refreshJournals() {
  try {
    const data = await api('/api/journals');
    state.journals = data.journals || [];
    state.scheduler = data.scheduler || state.scheduler;
    renderSidebar();
    if (state.route.name === 'journals') renderJournals();
    if ($('#settings-dialog-nav')) refreshSettingsNavValues();
  } catch (err) { console.error(err); }
}

function renderShell() {
  renderSidebar();
  if (state.route.name === 'home') renderDashboard();
  if (state.route.name === 'journals') renderJournals();
  renderSyncUI();
}

function renderSidebar() {
  const summary = state.summary || {};
  const counts = [
    ['#count-today', summary.today_new || 0],
    ['#count-inbox', summary.unread || 0],
    ['#count-starred', summary.starred || 0],
    ['#count-later', summary.later || 0],
    ['#count-watch', summary.watch_new || 0],
    ['#count-all', summary.total || 0]
  ];
  counts.forEach(function (pair) {
    const node = $(pair[0]);
    if (!node) return;
    node.textContent = pair[1];
    node.classList.toggle('zero', Number(pair[1]) === 0);
  });

  const lastCheck = summary.last_sync;
  setText('#sidebar-last-check', '检查时间：' + (lastCheck && (lastCheck.finished_at || lastCheck.started_at) ? fmtCnDate(lastCheck.finished_at || lastCheck.started_at) : '暂无'));

  const nav = $('#journal-nav');
  if (!nav) return;
  const domestic = state.journals.filter(isDomesticJournal);
  const international = state.journals.filter(function (j) { return !isDomesticJournal(j); });
  const journalLink = function (j) {
    const hot = Number(j.today_new) > 0;
    const count = hot ? '+' + j.today_new : (Number(j.unread_count) > 0 ? j.unread_count : '');
    return '<a class="journal-nav-item" href="#/journal/' + j.id + '" data-journal="' + j.id + '" title="' + esc(j.name) + '">' +
      '<i class="journal-swatch" style="--jc:' + esc(j.color) + '"></i>' +
      '<span class="journal-nav-name">' + esc(j.name) + '</span>' +
      '<span class="journal-nav-count ' + (hot ? 'hot' : '') + '">' + esc(count) + '</span>' +
      '</a>';
  };
  const group = function (key, label, list) {
    if (!list.length) return '';
    const open = !!state.journalGroups[key];
    return '<button class="journal-group-label ' + (open ? '' : 'collapsed') + '" data-group-toggle="' + key + '" aria-expanded="' + open + '">' +
      icon('chevron-down') + '<span class="group-title">' + label + '</span><b>' + list.length + '</b></button>' +
      '<div class="journal-group-items ' + (open ? '' : 'hidden') + '">' + list.map(journalLink).join('') + '</div>';
  };
  nav.innerHTML = group('domestic', '国内期刊', domestic) + group('international', '国际期刊', international);

  setActiveNav(state.route.name);
}

function renderDashboard() {
  const summary = state.summary || {};
  const journals = state.journals || [];
  const todayNew = Number(summary.today_new || 0);
  const todayAdded = Number(summary.today_added || 0);
  const total = Number(summary.total || 0);
  const firstBuild = total > 0 && todayNew === 0 && todayAdded >= Math.max(1, total * 0.8);
  const today = new Date();
  const greeting = greetingCn(today);
  $('#hero-kicker').textContent = todayCn() + ' ' + weekdayCn(today);

  if (todayNew > 0) {
    $('#hero-title').innerHTML = greeting + '，今天有 <em>' + todayNew + '</em> 篇新论文';
    $('#hero-today').setAttribute('href', '#/today');
    $('#hero-today').innerHTML = '查看今日新增 ' + icon('chevron-right');
  } else if (total === 0) {
    $('#hero-title').innerHTML = '文献库正在等待第一次检查';
    $('#hero-today').setAttribute('href', '#/journals');
    $('#hero-today').innerHTML = '查看监控期刊 ' + icon('chevron-right');
  } else if (firstBuild) {
    $('#hero-title').innerHTML = '文献库已就绪，已收录 <em>' + total + '</em> 篇';
    $('#hero-today').setAttribute('href', '#/all');
    $('#hero-today').innerHTML = '浏览文献库 ' + icon('chevron-right');
  } else {
    $('#hero-title').innerHTML = greeting + '，今天暂时安静，<em>0</em> 篇新论文';
    $('#hero-today').setAttribute('href', '#/all');
    $('#hero-today').innerHTML = '浏览文献库 ' + icon('chevron-right');
  }
  setText('#hero-count', todayNew);
  setText('#stat-today', todayNew);
  setText('#stat-unread', summary.unread || 0);
  setText('#stat-starred', summary.starred || 0);
  setText('#stat-journals', summary.journal_count || journals.length || 0);
  setText('#radar-caption', 'LIVE · ' + (summary.journal_count || journals.length || 0) + ' JOURNALS');
  setText('#stat-today-sub', todayNew > 0 ? '来自 ' + (summary.journal_count || 0) + ' 本期刊' : (todayAdded > 0 ? '本次入库 ' + todayAdded + ' 篇' : '等待下一次检查'));
  setText('#stat-journals-sub', '官网 RSS + Crossref');
  const latest = summary.latest || [];
  const latestNode = $('#latest-list');
  if (!latest.length) {
    latestNode.innerHTML = emptyState('book', '还没有论文', '从期刊监控页确认来源，或使用左下角的刷新按钮开始检查。', '<a class="btn btn-primary" href="#/journals">查看监控期刊</a>');
  } else {
    latestNode.innerHTML = latest.map(function (p) { return paperCard(p, true); }).join('');
  }

  const sorted = journals.slice().sort(function (a, b) {
    return (Number(b.today_new) - Number(a.today_new)) || (Number(b.unread_count) - Number(a.unread_count));
  }).slice(0, 6);
  const maxUnread = Math.max.apply(null, sorted.map(function (j) { return Number(j.unread_count) || 1; }).concat([1]));
  $('#pulse-list').innerHTML = sorted.length ? sorted.map(function (j) {
    const width = Math.max(3, Math.round((Number(j.unread_count) / maxUnread) * 100));
    const meta = Number(j.today_new) > 0 ? ('今日 +' + j.today_new) : (j.last_sync_at ? relativeTime(j.last_sync_at) : '尚未检查');
    return '<a class="pulse-row" href="#/journal/' + j.id + '">' +
      '<i class="journal-swatch" style="--jc:' + esc(j.color) + '"></i>' +
      '<div><div class="pulse-name">' + esc(j.name) + '</div><div class="pulse-meta">' + esc(meta) + '</div><div class="mini-bar"><span style="width:' + width + '%;--bar:' + esc(j.color) + '"></span></div></div>' +
      '<div class="pulse-count"><b>' + (j.today_new || 0) + '</b><br>今日</div>' +
      '</a>';
  }).join('') : '<div class="empty-state" style="padding:24px 5px"><p>还没有期刊数据。</p></div>';

  $('#topic-cloud').innerHTML = (state.topics || []).slice(0, 18).map(function (t) {
    return '<button class="topic-chip" data-topic="' + esc(t.name) + '"><i style="--tc:' + esc(t.color) + '"></i>' + esc(t.name) + '<span>' + t.count + '</span></button>';
  }).join('') || '<p class="settings-meta">完成首次检查后，会自动提取研究主题。</p>';
  $$('#topic-cloud .topic-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      state.topic = chip.getAttribute('data-topic');
      state.page = 1;
      navigate('#/all');
    });
  });
  renderSyncUI();
}

function syncedToday() {
  const summary = state.summary || {};
  const last = summary.last_sync;
  if (!last || !last.finished_at) return false;
  return String(last.finished_at).slice(0, 10) === String(summary.today || '');
}

function openSyncPopover() {
  const popover = $('#sync-popover');
  if (!popover) return;
  popover.classList.remove('hidden');
  $('#sync-hub-button').setAttribute('aria-expanded', 'true');
  renderSyncUI();
}

function closeSyncPopover() {
  const popover = $('#sync-popover');
  if (!popover) return;
  state.syncConfirm = false;
  popover.classList.add('hidden');
  $('#sync-hub-button').setAttribute('aria-expanded', 'false');
}

function openSyncConfirm() {
  state.syncConfirm = true;
  openSyncPopover();
  renderSyncUI();
}

function handleSyncHubClick() {
  const popover = $('#sync-popover');
  if (popover && !popover.classList.contains('hidden')) {
    closeSyncPopover();
    return;
  }
  if (state.syncState && state.syncState.running) {
    state.syncConfirm = false;
    openSyncPopover();
    return;
  }
  if (syncedToday()) {
    openSyncConfirm();
  } else {
    state.syncConfirm = false;
    startSync(null);
  }
}

async function cancelSync() {
  if (!state.syncState || !state.syncState.running) return;
  try {
    await api('/api/sync/cancel', { method: 'POST', body: {} });
    toast('已请求中断检查，正在停止当前期刊…', 'info');
  } catch (err) {
    toast(err.message, 'error');
  }
}

function renderSyncUI() {
  const sync = state.syncState || {};
  const summary = state.summary || {};
  const running = !!sync.running;
  const synced = syncedToday();
  const hub = $('#sync-hub-button');
  const dot = $('#sync-hub-dot');
  if (hub) {
    hub.classList.toggle('is-running', running);
    hub.title = running ? '正在检查，点击查看或中断' : (synced ? '今日已同步，点击查看' : '今日尚未同步，点击检查');
  }
  if (dot) {
    let cls = 'sync-hub-dot';
    if (running) cls += ' running';
    else if (summary.last_run && summary.last_run.status === 'failed') cls += ' error';
    else if (synced) cls += ' ok';
    dot.className = cls;
  }
  const title = $('#sync-pop-title');
  const message = $('#sync-pop-message');
  const meta = $('#sync-pop-meta');
  const bar = $('#sync-pop-bar');
  const current = $('#sync-pop-current');
  const start = $('#sync-pop-start');
  const cancel = $('#sync-pop-cancel');
  const confirmCancel = $('#sync-pop-confirm-cancel');
  const confirmStart = $('#sync-pop-confirm-start');
  const settingsLink = $('#sync-pop-settings');
  const confirming = !!state.syncConfirm && !running;
  if (start) start.classList.toggle('hidden', running || confirming);
  if (cancel) cancel.classList.toggle('hidden', !running);
  if (confirmCancel) confirmCancel.classList.toggle('hidden', !confirming);
  if (confirmStart) confirmStart.classList.toggle('hidden', !confirming);
  if (settingsLink) settingsLink.classList.toggle('hidden', confirming);
  if (bar) {
    const total = Number(sync.total || 0);
    const done = Number(sync.done || 0);
    bar.style.width = running && total ? Math.min(100, Math.round((done / total) * 100)) + '%' : (synced ? '100%' : '0%');
  }
  if (current) {
    if (running && sync.current) {
      current.classList.remove('hidden');
      current.textContent = '正在读取：' + sync.current;
    } else if (running && sync.message) {
      current.classList.remove('hidden');
      current.textContent = sync.message;
    } else {
      current.classList.add('hidden');
      current.textContent = '';
    }
  }
  if (title) {
    if (running) title.textContent = '正在检查…';
    else if (summary.last_run && summary.last_run.status === 'failed') title.textContent = '上次检查失败';
    else if (synced) title.textContent = '今日已同步';
    else title.textContent = '今日尚未同步';
  }
  if (message) {
    if (running) message.textContent = sync.message || '正在读取期刊官网，可随时中断。';
    else if (confirming) message.textContent = '本日已同步，是否仍要进行检查？';
    else if (summary.last_run && summary.last_run.status === 'failed') message.textContent = summary.last_run.detail && summary.last_run.detail.errors && summary.last_run.detail.errors[0] ? summary.last_run.detail.errors[0].error : '上次检查未完成，请重试。';
    else if (synced) message.textContent = '今日的期刊检查已完成，状态灯为绿色。';
    else message.textContent = '每日 ' + (state.settings.daily_time || '08:00') + ' 自动检查全部 ' + (summary.journal_count || 0) + ' 本期刊。';
  }
  if (meta) {
    if (running) {
      const total = Number(sync.total || 0);
      const done = Number(sync.done || 0);
      meta.textContent = total ? ('进度 ' + done + '/' + total + ' 本期刊 · 新增 ' + (sync.new || 0) + ' 篇') : '正在连接期刊官网…';
    } else if (summary.last_sync) {
      meta.textContent = '上次检查 ' + fmtDateTime(summary.last_sync.finished_at || summary.last_sync.started_at) + ' · 新增 ' + (summary.last_sync.new_count || 0) + ' 篇';
    } else {
      meta.textContent = '暂无同步记录';
    }
  }
  const progress = $('#sync-progress');
  if (progress) {
    const span = progress.querySelector('span');
    const total = Number(sync.total || 0);
    const done = Number(sync.done || 0);
    if (running) span.style.width = (total ? Math.max(4, Math.round((done / total) * 100)) : 10) + '%';
    else span.style.width = '0%';
  }
}

function paperSourceUrl(paper) {
  return paper.url || paper.doi_url || (paper.doi ? 'https://doi.org/' + paper.doi : '');
}

function paperSourceLabel(url) {
  return String(url || '').replace(/^https?:\/\//, '').replace(/^www\./, '');
}

function paperPreviewUrl(paper) {
  return paper.preview_url || paper.url || paper.doi_url || (paper.doi ? 'https://doi.org/' + paper.doi : '');
}

function paperPdfUrl(paper) {
  return paper.pdf_url ? '/api/papers/' + paper.id + '/pdf' : '';
}

function formatCitation(paper) {
  if (paper.citation_text) return paper.citation_text;
  const authors = (paper.authors || []).join(', ') || '佚名';
  const year = (paper.published_date || '').slice(0, 4) || 'n.d.';
  let text = authors + '. ' + (paper.title || '') + '[J]. ' + (paper.journal_name || '') + ', ' + year;
  if (paper.volume) text += ', ' + paper.volume;
  if (paper.issue) text += '(' + paper.issue + ')';
  if (paper.pages) text += ': ' + paper.pages;
  text += '.';
  if (paper.doi) text += ' DOI: ' + paper.doi + '.';
  return text;
}

function paperCard(paper, compact) {
  const isRead = !!paper.read_at;
  const keywords = state.settings.watch_keywords || [];
  const color = paper.journal_color || '#c2552d';
  const abstract = paper.abstract || '';
  const topics = (paper.topics || []).slice(0, compact ? 2 : 3);
  const date = fmtDate(paper.published_date, paper.published_precision) || '日期待定';
  const authors = shortAuthors(paper.authors, compact ? 2 : 3) || '作者信息待补';
  const volumeInfo = (paper.volume || paper.issue || paper.pages)
    ? [paper.volume ? paper.volume + (paper.issue ? '(' + paper.issue + ')' : '') : '',
       paper.pages ? ': ' + paper.pages : ''].filter(Boolean).join('')
    : '';
  const newBadge = isFresh(paper) ? '<span class="badge new">NEW</span>' : '';
  const oaBadge = paper.open_access ? '<span class="badge oa">OA</span>' : '';
  const interestBadge = (paper.interest_match || paper.interest_score) ? '<span class="badge interest">关注</span>' : '';
  const watchBadge = paper.watch_match ? '<span class="badge watch" title="命中作者追踪名单">关注作者</span>' : '';
  const risingBadge = paper.citation_rising ? '<span class="badge rising" title="近期被引数明显上升">引用上升</span>' : '';
  const pdfBadge = paper.has_local_pdf ? '<span class="badge pdf" title="PDF 已保存到本地库">本地PDF</span>' : '';
  const citationBadge = Number(paper.citation_count || 0) > 0 ? '<span class="paper-citation">被引 ' + Number(paper.citation_count) + '</span>' : '';
  const backfillBadge = isBackfill(paper) ? '<span class="badge backfill" title="首次入库时该论文已出版超过 45 天">补录</span>' : '';
  const tags = topics.map(function (t) {
    return '<span class="mini-tag"><i style="--tc:' + topicColor(t) + '"></i>' + esc(t) + '</span>';
  }).join('');
  const excerpt = abstract ? '<p class="paper-excerpt">' + highlight(abstract.slice(0, compact ? 180 : 260), keywords) + '</p>' : '';
  const sourceUrl = paperSourceUrl(paper);
  const sourceLink = sourceUrl && !compact ? '<a class="paper-source-link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener" data-open-external="' + paper.id + '" title="' + esc(sourceUrl) + '">原文链接 <span>' + esc(paperSourceLabel(sourceUrl)) + '</span>' + icon('external') + '</a>' : '';
  const previewUrl = paperPreviewUrl(paper);
  const previewLink = previewUrl && !compact ? '<a class="paper-preview-link" href="' + esc(previewUrl) + '" target="_blank" rel="noopener">在线浏览</a>' : '';
  const pdfUrl = paperPdfUrl(paper);
  const pdfLink = pdfUrl && !compact ? '<a class="paper-pdf-link" href="' + esc(pdfUrl) + '" title="' + (paper.has_local_pdf ? '打开本地已存的 PDF' : '下载 PDF，成功后自动存入本地库') + '">' + (paper.has_local_pdf ? '打开 PDF（本地）' : 'PDF 下载') + '</a>' : '';
  const isSelected = state.selected.has(paper.id);
  return '<article class="paper-card ' + (isRead ? 'is-read ' : '') + (isSelected ? 'selected ' : '') + (compact ? 'compact ' : '') + '" data-id="' + paper.id + '" style="--journal-color:' + esc(color) + '">' +
    (compact ? '' : '<label class="paper-check"><input type="checkbox" data-select="' + paper.id + '"' + (isSelected ? ' checked' : '') + '></label>') +
    '<div class="paper-main">' +
      '<div class="paper-meta">' +
        '<span class="journal-swatch" style="--jc:' + esc(color) + '"></span>' +
        '<span class="journal-name">' + esc(paper.journal_name || '') + '</span>' +
        '<span class="sep">·</span><span>' + esc(date) + '</span>' +
        (volumeInfo ? '<span class="sep">·</span><span class="paper-vol">' + esc(volumeInfo) + '</span>' : '') +
        newBadge + oaBadge + interestBadge + watchBadge + risingBadge + pdfBadge + citationBadge + backfillBadge +
      '</div>' +
      '<h3 class="paper-title" data-open="' + paper.id + '">' + highlight(paper.title, keywords) + '</h3>' +
      '<p class="paper-authors">' + esc(authors) + '</p>' +
      excerpt +
      (tags ? '<div class="paper-tags">' + tags + '</div>' : '') +
      previewLink + pdfLink + sourceLink +
    '</div>' +
    '<div class="paper-actions">' +
      '<button class="paper-action ' + (paper.starred ? 'is-on' : '') + '" data-action="star" data-id="' + paper.id + '" title="收藏">' + icon('star') + '</button>' +
      '<button class="paper-action ' + (paper.read_later ? 'is-on' : '') + '" data-action="later" data-id="' + paper.id + '" title="稍后读">' + icon('clock') + '</button>' +
      '<button class="paper-action" data-action="read" data-id="' + paper.id + '" title="' + (isRead ? '标为未读' : '标为已读') + '">' + icon(isRead ? 'check' : 'inbox') + '</button>' +
      (sourceUrl ? '<a class="paper-action" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener" data-open-external="' + paper.id + '" title="打开原文">' + icon('external') + '</a>' : '') +
    '</div>' +
  '</article>';
}

function currentPaperParams() {
  const route = state.route;
  let view = route.name;
  if (route.name === 'journal') view = state.view || 'all';
  if (['today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive'].indexOf(view) < 0) view = 'all';
  const params = new URLSearchParams();
  params.set('view', view);
  params.set('sort', state.sort);
  params.set('field', state.searchField);
  if (state.q) params.set('q', state.q);
  if (state.topic) params.set('topic', state.topic);
  if (state.journalFilter) params.set('journal', state.journalFilter);
  if (state.range !== 'all') {
    const days = Number(state.range);
    const from = new Date();
    from.setDate(from.getDate() - days);
    params.set('from', from.toISOString().slice(0, 10));
  }
  return params;
}

async function selectAllResults() {
  const button = $('#select-all-results');
  if (button) { button.disabled = true; button.textContent = '正在选择…'; }
  try {
    const data = await api('/api/papers/ids?' + currentPaperParams().toString());
    state.selected = new Set((data.ids || []).map(Number));
    renderPaperList();
    renderSelectionBar();
    toast('已选择当前筛选下的 ' + state.selected.size + ' 篇论文');
  } catch (err) {
    toast(err.message, 'error');
    renderSelectionBar();
  }
}

async function loadPapers() {
  const route = state.route;
  let view = route.name;
  if (route.name === 'journal') view = state.view || 'all';
  if (!['today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive'].includes(view)) view = 'all';
  state.view = view;
  state.loading = true;
  renderPaperList(true);
  const params = new URLSearchParams();
  params.set('view', view);
  params.set('page', String(state.page));
  params.set('page_size', String(state.pageSize));
  params.set('sort', state.sort);
  params.set('field', state.searchField);
  if (state.q) params.set('q', state.q);
  if (state.topic) params.set('topic', state.topic);
  if (state.journalFilter) params.set('journal', state.journalFilter);
  if (state.range !== 'all') {
    const days = Number(state.range);
    const from = new Date();
    from.setDate(from.getDate() - days);
    params.set('from', from.toISOString().slice(0, 10));
  }
  try {
    const data = await api('/api/papers?' + params.toString());
    state.data = data;
    state.selected.clear();
    state.loading = false;
    renderPaperList();
    renderFilters();
    renderPagination();
    renderSelectionBar();
    updateListHeading();
  } catch (err) {
    state.loading = false;
    $('#paper-list').innerHTML = emptyState('x', '加载失败', err.message, '<button class="btn btn-primary" onclick="loadPapers()">重试</button>');
    toast(err.message, 'error');
  }
}

function updateListHeading() {
  const meta = VIEW_META[state.route.name] || VIEW_META.all;
  setText('#list-title', meta.title || '全部论文');
  setText('#list-eyebrow', meta.eyebrow || 'LITERATURE LIBRARY');
  setText('#list-subtitle', meta.desc || '');
  if (state.route.name === 'journal' && state.route.journalId) {
    const journal = journalById(state.route.journalId);
    if (journal) {
      setText('#list-title', journal.name);
      setText('#list-eyebrow', (journal.publisher || 'JOURNAL') + ' · ISSN ' + (journal.issn || '—'));
      setText('#list-subtitle', '正在跟踪该期刊的官网更新。共 ' + (journal.paper_count || 0) + ' 篇论文，' + (journal.unread_count || 0) + ' 篇待读。');
    }
  }
}

function renderPaperList(loading) {
  const node = $('#paper-list');
  if (!node) return;
  if (loading) {
    node.innerHTML = '<div class="skeleton-row"></div><div class="skeleton-row"></div><div class="skeleton-row"></div><div class="skeleton-row"></div>';
    return;
  }
  if (!state.data.items.length) {
    const route = state.route.name;
    let title = '这里还没有论文';
    let desc = '运行一次检查，把期刊官网的最新论文收进来。';
    if (state.q) { title = '没有找到匹配的论文'; desc = '试试更短的关键词，或清除筛选条件。'; }
    else if (state.searchField === 'interests') { title = '关注点暂无匹配论文'; desc = '可以在设置 → 研究偏好中调整关注领域，或切换检索字段。'; }
    else if (route === 'starred') { title = '还没有收藏'; desc = '在论文卡片右侧点击星标，把重要论文留在这里。'; }
    else if (route === 'later') { title = '稍后读是空的'; desc = '看到感兴趣但暂时没空读的论文，可以先放进稍后读。'; }
    else if (route === 'archive') { title = '归档是空的'; desc = '归档的论文会从主列表隐藏，但仍保留在数据库中。'; }
    else if (route === 'watch') { title = '暂无追踪作者的论文'; desc = '在设置 → 作者追踪中添加你关注的作者或机构，命中论文会自动汇总到这里。'; }
    const action = state.q ? '' : '<a class="btn btn-primary" href="#/all">浏览全部论文</a>';
    node.innerHTML = emptyState('inbox', title, desc, action);
    return;
  }
  node.innerHTML = state.data.items.map(function (p) { return paperCard(p, false); }).join('');
  const selectAll = $('#select-all');
  if (selectAll) {
    const selectedOnPage = state.data.items.filter(function (p) { return state.selected.has(p.id); }).length;
    selectAll.checked = state.data.items.length > 0 && selectedOnPage === state.data.items.length;
    selectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < state.data.items.length;
  }
}

const SEARCH_FIELD_HINTS = {
  interests: '搜索关注点相关论文…',
  all: '输入关键词…',
  title: '在标题中搜索…',
  author: '在作者中搜索…',
  abstract: '在摘要中搜索…',
  doi: '输入 DOI…',
  journal: '在期刊名称中搜索…'
};

function updateSearchFieldHint() {
  const input = $('#search-input');
  const field = $('#search-field');
  if (field && field.value !== state.searchField) field.value = state.searchField;
  if (input) input.placeholder = SEARCH_FIELD_HINTS[state.searchField] || SEARCH_FIELD_HINTS.all;
}

function renderFilters() {
  $('#filter-journal').innerHTML = '<option value="">全部期刊</option>' + state.journals.map(function (j) {
    return '<option value="' + j.id + '"' + (Number(state.journalFilter) === Number(j.id) ? ' selected' : '') + '>' + esc(j.name) + '</option>';
  }).join('');
  $('#filter-topic').innerHTML = '<option value="">全部关键词</option>' + state.topics.map(function (t) {
    return '<option value="' + esc(t.name) + '"' + (state.topic === t.name ? ' selected' : '') + '>' + esc(t.name) + '（' + t.count + '）</option>';
  }).join('');
  $('#filter-range').value = state.range;
  $('#filter-sort').value = state.sort;
  $('#search-field').value = state.searchField;
  $('#search-input').value = state.q;
  updateSearchFieldHint();
}

function renderPagination() {
  const node = $('#pagination');
  if (!node) return;
  if (state.data.pages <= 1) { node.innerHTML = ''; return; }
  node.innerHTML = '<button data-page="prev"' + (state.page <= 1 ? ' disabled' : '') + '>' + icon('chevron-left') + ' 上一页</button>' +
    '<span class="page-info">第 ' + state.data.page + ' / ' + state.data.pages + ' 页 · 共 ' + state.data.total + ' 篇</span>' +
    '<button data-page="next"' + (state.page >= state.data.pages ? ' disabled' : '') + '>下一页 ' + icon('chevron-right') + '</button>';
  node.querySelector('[data-page="prev"]').addEventListener('click', function () { if (state.page > 1) { state.page -= 1; loadPapers(); } });
  node.querySelector('[data-page="next"]').addEventListener('click', function () { if (state.page < state.data.pages) { state.page += 1; loadPapers(); } });
}

function renderSelectionBar() {
  const count = state.selected.size;
  const selectionMode = $('#selection-mode');
  if (selectionMode) selectionMode.classList.toggle('hidden', count === 0);
  const exportButton = $('#export-menu-button');
  if (exportButton) exportButton.classList.toggle('hidden', count === 0);
  const total = Number((state.data && state.data.total) || state.data.items.length || 0);
  setText('#selection-count', '已选 ' + count + ' 篇 / 共 ' + total + ' 篇');
  const allButton = $('#select-all-results');
  if (allButton) {
    const pageCount = (state.data && state.data.items.length) || 0;
    const allSelected = total > 0 && count >= total;
    allButton.classList.toggle('hidden', total <= pageCount || total === 0);
    allButton.disabled = allSelected;
    allButton.textContent = allSelected ? '已全选 ' + total + ' 篇' : '全选全部 ' + total + ' 篇';
  }
  $$('#selection-mode [data-bulk]').forEach(function (button) {
    button.disabled = count === 0;
  });
}

async function bulkAction(action) {
  if (!state.selected.size) return;
  const count = state.selected.size;
  try {
    const result = await api('/api/papers/bulk', { method: 'POST', body: { ids: Array.from(state.selected), action: action } });
    state.selected.clear();
    await refreshSummary();
    await loadPapers();
    const updated = Number(result.updated || count);
    if (action === 'read') toast('已标记 ' + updated + ' 篇为已读 · 当前待读 ' + ((state.summary && state.summary.unread) || 0) + ' 篇');
    else if (action === 'clear_rising') toast('已清除 ' + updated + ' 篇的引用上升提醒');
    else toast('已更新 ' + updated + ' 篇论文');
  } catch (err) { toast(err.message, 'error'); }
}

function digestItemById(id) {
  const groups = (state.digest && state.digest.groups) || [];
  for (let i = 0; i < groups.length; i += 1) {
    const item = groups[i].items.find(function (p) { return Number(p.id) === Number(id); });
    if (item) return item;
  }
  return null;
}

async function togglePaper(id, action) {
  const paper = state.data.items.find(function (p) { return p.id === id; }) ||
    ((state.summary && state.summary.latest) || []).find(function (p) { return p.id === id; }) ||
    (state.drawerPaper && state.drawerPaper.id === id ? state.drawerPaper : null) ||
    digestItemById(id);
  if (!paper) return;
  let body = {};
  if (action === 'star') body = { starred: !paper.starred };
  else if (action === 'later') body = { read_later: !paper.read_later };
  else if (action === 'read') body = { read: !paper.read_at };
  else if (action === 'archive') body = { archived: true };
  try {
    const result = await api('/api/papers/' + id, { method: 'PATCH', body: body });
    Object.assign(paper, result.paper);
    if (state.drawerPaper && state.drawerPaper.id === id) state.drawerPaper = result.paper;
    renderPaperList();
    renderSelectionBar();
    if (state.drawerOpen) renderDrawer(result.paper);
    if (state.route.name === 'home') renderDashboard();
    refreshSummary();
  } catch (err) { toast(err.message, 'error'); }
}

async function openExternal(id) {
  const paper = state.data.items.find(function (p) { return p.id === id; }) ||
    ((state.summary && state.summary.latest) || []).find(function (p) { return p.id === id; }) ||
    (state.drawerPaper && state.drawerPaper.id === id ? state.drawerPaper : null) ||
    digestItemById(id);
  if (!paper) return;
  const url = paperSourceUrl(paper);
  if (!url) { toast('这篇论文没有可用的原文链接', 'error'); return; }
  window.open(url, '_blank', 'noopener');
  if (!paper.read_at) {
    try {
      const result = await api('/api/papers/' + id, { method: 'PATCH', body: { read: true } });
      Object.assign(paper, result.paper);
      renderPaperList();
      refreshSummary();
    } catch (err) { /* 打开原文不应被阅读状态阻塞 */ }
  }
}

function renderJournals() {
  const grid = $('#journal-grid');
  if (!grid) return;
  const scheduler = state.scheduler || {};
  setText('#journal-schedule-note', scheduler.installed ? ('计划任务已安装 · 下次运行 ' + (scheduler.next_run || '—')) : '尚未安装计划任务，可在设置中安装');
  grid.innerHTML = state.journals.map(function (j) {
    const status = !j.enabled ? '<span class="status-pill off">已停用</span>' :
      (j.last_sync_status === 'error' ? '<span class="status-pill error">检查异常</span>' : '<span class="status-pill">监控中</span>');
    return '<article class="journal-card ' + (j.enabled ? '' : 'disabled') + '" style="--jc:' + esc(j.color) + '">' +
      '<div class="journal-card-top"><span class="journal-swatch" style="--jc:' + esc(j.color) + '"></span>' + status + '</div>' +
      '<h3>' + esc(j.name) + '</h3>' +
      '<div class="journal-pub">' + esc(j.publisher || '') + ' · ISSN ' + esc(j.issn || '—') + (j.is_preset ? '' : ' · 自定义') + '</div>' +
      '<div class="journal-stats">' +
        '<div class="journal-stat"><b>' + (j.paper_count || 0) + '</b><span>已收录</span></div>' +
        '<div class="journal-stat hot"><b>' + (j.today_new || 0) + '</b><span>今日新增</span></div>' +
        '<div class="journal-stat"><b>' + (j.unread_count || 0) + '</b><span>待读</span></div>' +
      '</div>' +
      '<div class="journal-last">上次检查 <strong>' + (j.last_sync_at ? relativeTime(j.last_sync_at) : '尚未检查') + '</strong></div>' +
      '<div class="journal-actions">' +
        (j.homepage_url ? '<a href="' + esc(j.homepage_url) + '" target="_blank" rel="noopener">' + icon('external') + '官网</a>' : '') +
        '<button data-sync-journal="' + j.id + '">' + icon('refresh') + '同步</button>' +
        '<button data-edit-journal="' + j.id + '">RSS</button>' +
        '<label class="switch" title="启用/停用"><input type="checkbox" data-journal-toggle="' + j.id + '"' + (j.enabled ? ' checked' : '') + '><span></span></label>' +
      '</div>' +
    '</article>';
  }).join('') + '<button class="journal-card add-card" id="add-journal-card"><span class="add-plus">' + icon('plus') + '</span><strong>添加期刊</strong><span>支持官网 RSS 或 ISSN</span></button>';
  const addCard = $('#add-journal-card');
  if (addCard) addCard.addEventListener('click', function () { openJournalModal(null); });
}

async function toggleJournal(id, enabled) {
  try {
    await api('/api/journals/' + id, { method: 'PATCH', body: { enabled: enabled } });
    const journal = journalById(id);
    if (journal) journal.enabled = enabled;
    toast(enabled ? '已启用该期刊' : '已停用该期刊（论文保留）');
    await refreshJournals();
    await refreshSummary();
  } catch (err) {
    toast(err.message, 'error');
    await refreshJournals();
  }
}

function openJournalModal(journal, returnToSettings) {
  const isEdit = !!journal;
  const name = journal ? journal.name : '';
  const issn = journal ? journal.issn : '';
  const rss = journal ? journal.rss_url : '';
  const homepage = journal ? journal.homepage_url : '';
  const publisher = journal ? journal.publisher : '';
  const body = '<p class="modal-intro">' + (isEdit ? '修改期刊的抓取地址。预设期刊也可以换成你所在机构可访问的 RSS。' : '有官网 RSS 时优先监控官网；只填 ISSN 时，系统会用 Crossref 元数据跟踪该期刊。') + '</p>' +
    '<div class="form-grid">' +
      '<label class="field full"><span>期刊名称 *</span><input id="j-name" value="' + esc(name) + '" placeholder="例如：Safety Science"></label>' +
      '<label class="field"><span>ISSN</span><input id="j-issn" value="' + esc(issn) + '" placeholder="0925-7535"></label>' +
      '<label class="field"><span>出版商</span><input id="j-publisher" value="' + esc(publisher) + '" placeholder="Elsevier / Springer / Wiley"></label>' +
      '<label class="field full"><span>官网 RSS 地址</span><input id="j-rss" value="' + esc(rss) + '" placeholder="https://rss.sciencedirect.com/publication/science/09257535"></label>' +
      '<label class="field full"><span>期刊官网</span><input id="j-homepage" value="' + esc(homepage) + '" placeholder="https://www.sciencedirect.com/journal/safety-science"></label>' +
    '</div>' +
    '<div class="modal-note">RSS 是发现新论文的首选来源；DOI、作者和卷期信息会由 Crossref 自动补齐。添加后可点击期刊卡片上的“同步”立即建库。</div>';
  const footer = (isEdit && !journal.is_preset ? '<button class="btn btn-danger" id="j-delete">删除</button>' : '') +
    '<span class="spacer"></span><button class="btn btn-primary" id="j-save">' + (isEdit ? '保存修改' : '添加期刊') + '</button>';
  openModal({
    kicker: isEdit ? 'EDIT SOURCE' : 'NEW SOURCE',
    title: isEdit ? '编辑期刊' : '添加期刊',
    body: body,
    foot: footer,
    onMount: function () {
      $('#j-save').addEventListener('click', async function () {
        const payload = {
          name: $('#j-name').value.trim(),
          issn: $('#j-issn').value.trim(),
          publisher: $('#j-publisher').value.trim(),
          rss_url: $('#j-rss').value.trim(),
          homepage_url: $('#j-homepage').value.trim()
        };
        if (!payload.name) { toast('请填写期刊名称', 'error'); return; }
        if (!payload.issn && !payload.rss_url) { toast('请至少填写 ISSN 或 RSS 地址', 'error'); return; }
        try {
          if (isEdit) await api('/api/journals/' + journal.id, { method: 'PATCH', body: payload });
          else await api('/api/journals', { method: 'POST', body: payload });
          closeModal();
          toast(isEdit ? '期刊已更新' : '期刊已添加，可以立即同步');
          await refreshJournals();
          if (returnToSettings) openSettingsDialog('journals');
        } catch (err) { toast(err.message, 'error'); }
      });
      if (isEdit && !journal.is_preset) {
        $('#j-delete').addEventListener('click', async function () {
          if (!window.confirm('删除该期刊会同时删除它已收录的论文，确定吗？')) return;
          try {
            await api('/api/journals/' + journal.id, { method: 'DELETE' });
            closeModal();
            toast('期刊已删除');
            await refreshJournals();
            await refreshSummary();
            if (returnToSettings) openSettingsDialog('journals');
          } catch (err) { toast(err.message, 'error'); }
        });
      }
    }
  });
}

function settingsNavItems() {
  const settings = state.settings || {};
  const scheduler = state.scheduler;
  const keywords = settings.watch_keywords || [];
  return [
    ['schedule', 'calendar', '检查与同步', scheduler && scheduler.installed ? (settings.daily_time || '08:00') : '未安装'],
    ['keywords', 'spark', '关注关键词', keywords.length + ' 个'],
    ['interests', 'filter', '研究偏好', ((settings.research_interests || []).length) + ' 个'],
    ['watch', 'bell', '作者追踪', ((settings.watched_authors || []).length) + ' 位'],
    ['journals', 'book', '期刊管理', (state.journals || []).length + ' 本'],
    ['data', 'download', '数据与导出', ((state.summary && state.summary.total) || 0) + ' 篇'],
    ['history', 'clock', '同步记录', '查看'],
    ['about', 'settings', '关于', 'v1.1.0']
  ];
}

function refreshSettingsNavValues() {
  settingsNavItems().forEach(function (item) {
    setText('#settings-' + item[0] + '-value', item[3]);
  });
}

function openSettingsDialog(section) {
  const nav = settingsNavItems().map(function (item) {
    return '<button class="settings-nav-item" data-settings-section="' + item[0] + '">' +
      '<svg><use href="#i-' + item[1] + '"></use></svg>' +
      '<span>' + item[2] + '</span>' +
      '<small id="settings-' + item[0] + '-value">' + esc(item[3]) + '</small>' +
    '</button>';
  }).join('');
  openModal({
    kicker: 'PREFERENCES & DATA',
    title: '设置与数据',
    modalClass: 'settings-dialog-modal',
    body: '<div class="settings-dialog"><nav class="settings-dialog-nav" id="settings-dialog-nav">' + nav + '</nav>' +
      '<div class="settings-dialog-pane" id="settings-dialog-pane"></div></div>',
    onMount: function () {
      $('#settings-dialog-nav').onclick = function (event) {
        const button = event.target.closest('[data-settings-section]');
        if (!button) return;
        renderSettingsSection(button.getAttribute('data-settings-section'));
      };
      renderSettingsSection(section || 'schedule');
    }
  });
}

function renderSettingsSection(section) {
  const allowed = ['schedule', 'keywords', 'interests', 'watch', 'journals', 'data', 'history', 'about'];
  if (allowed.indexOf(section) < 0) section = 'schedule';
  state.settingsSection = section;
  $$('#settings-dialog-nav [data-settings-section]').forEach(function (button) {
    button.classList.toggle('active', button.getAttribute('data-settings-section') === section);
  });
  const pane = $('#settings-dialog-pane');
  if (!pane) return;
  pane.onclick = null;
  pane.onchange = null;
  pane.scrollTop = 0;
  if (section === 'schedule') return renderSchedulePane(pane);
  if (section === 'keywords') return renderKeywordPane(pane);
  if (section === 'interests') return renderInterestPane(pane);
  if (section === 'watch') return renderWatchPane(pane);
  if (section === 'journals') return renderJournalPane(pane);
  if (section === 'data') return renderDataPane(pane);
  if (section === 'history') return renderHistoryPane(pane);
  return renderAboutPane(pane);
}

function renderSchedulePane(pane) {
  const time = (state.settings && state.settings.daily_time) || '08:00';
  pane.innerHTML = '<div class="settings-pane-title">检查与同步</div>' +
    '<p class="settings-section-desc">Windows 计划任务会在设定时间运行同步脚本，即使工作台没有打开也能更新文献库。</p>' +
    '<div class="settings-modal-head"><div><span class="panel-kicker">DAILY CHECK</span><strong>每天 ' + esc(time) + '</strong></div><span class="pill" id="scheduler-pill">读取中</span></div>' +
    '<div class="form-grid">' +
      '<label class="field"><span>检查时间</span><input type="time" id="setting-time" value="' + esc(time) + '"></label>' +
      '<div class="field"><span>下次运行</span><div class="readonly-value" id="scheduler-next">—</div></div>' +
    '</div>' +
    '<div class="settings-meta" id="scheduler-meta">正在读取计划任务状态…</div>' +
    '<div class="settings-pane-actions"><button class="btn btn-primary" id="install-scheduler">保存并安装计划任务</button></div>';
  $('#install-scheduler').addEventListener('click', installScheduler);
  renderScheduler();
}

function renderKeywordPane(pane) {
  const keywords = (state.settings && state.settings.watch_keywords) || [];
  pane.innerHTML = '<div class="settings-pane-title">关注关键词</div>' +
    '<p class="settings-section-desc">关键词用于高亮标题与摘要；中文期刊的作者关键词会显示在论文详情中。用逗号分隔，中英文均可。</p>' +
    '<label class="field"><span>关键词</span><input type="text" id="setting-keywords" value="' + esc(keywords.join(', ')) + '" placeholder="risk, resilience, human factors, fire"></label>' +
    '<div class="keyword-preview" id="keyword-preview"></div>' +
    '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-settings">保存设置</button></div>';
  renderKeywordPreview(keywords);
  $('#setting-keywords').addEventListener('input', function (event) { renderKeywordPreview(parseKeywords(event.target.value)); });
  $('#save-settings').addEventListener('click', saveSettings);
}

function renderInterestPane(pane) {
  const presets = ['大模型', '数据驱动', '智能体', '算法', '机器学习', '人工智能', '知识图谱', '数字孪生', '优化算法', '计算机视觉', '自然语言处理'];
  const selected = (state.settings && state.settings.research_interests) || [];
  const custom = selected.filter(function (value) { return presets.indexOf(value) < 0; });
  pane.innerHTML = '<div class="settings-pane-title">研究偏好</div>' +
    '<p class="settings-section-desc">选择你在简报中优先关注的领域。今日简报会单独提供“我的关注”标签，只汇总匹配这些兴趣的论文。</p>' +
    '<div class="interest-chips" id="interest-chips">' +
      presets.map(function (value) {
        return '<button class="interest-chip ' + (selected.indexOf(value) >= 0 ? 'is-on' : '') + '" data-interest="' + esc(value) + '">' + esc(value) + '</button>';
      }).join('') +
    '</div>' +
    '<label class="field" style="margin-top:16px"><span>自定义关注词（逗号分隔）</span><input type="text" id="setting-interests-custom" value="' + esc(custom.join(', ')) + '" placeholder="例如：强化学习, 可解释性, 多模态"></label>' +
    '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-interests">保存偏好</button></div>';
  pane.onclick = function (event) {
    const chip = event.target.closest('[data-interest]');
    if (!chip) return;
    chip.classList.toggle('is-on');
  };
  $('#save-interests').addEventListener('click', async function () {
    const chosen = $$('#interest-chips .interest-chip.is-on').map(function (chip) { return chip.getAttribute('data-interest'); });
    const extra = parseKeywords($('#setting-interests-custom').value);
    const merged = Array.from(new Set(chosen.concat(extra))).slice(0, 30);
    try {
      const result = await api('/api/settings', { method: 'PUT', body: { research_interests: merged } });
      state.settings = result.settings;
      toast('研究偏好已保存');
      refreshSettingsNavValues();
    } catch (err) { toast(err.message, 'error'); }
  });
}

function renderWatchPane(pane) {
  const authors = (state.settings && state.settings.watched_authors) || [];
  pane.innerHTML = '<div class="settings-pane-title">作者追踪</div>' +
    '<p class="settings-section-desc">填入你关注的作者或机构名称（逗号分隔，中英文均可）。命中论文会自动进入左侧“作者关注”，并在卡片与详情上标注；今日简报的“我的关注”也会包含它们。</p>' +
    '<label class="field"><span>追踪名单</span><input type="text" id="setting-watch-authors" value="' + esc(authors.join(', ')) + '" placeholder="例如：Reason, 傅贵, 某大学安全学院"></label>' +
    '<div class="keyword-preview" id="watch-preview"></div>' +
    '<div class="settings-pane-actions"><button class="btn btn-primary" id="save-watch">保存名单</button></div>';
  const previewNode = $('#watch-preview');
  const preview = function (words) { if (previewNode) previewNode.innerHTML = (words || []).map(function (w) { return '<span>' + esc(w) + '</span>'; }).join(''); };
  preview(authors);
  $('#setting-watch-authors').addEventListener('input', function (event) { preview(parseKeywords(event.target.value)); });
  $('#save-watch').addEventListener('click', async function () {
    const list = parseKeywords($('#setting-watch-authors').value);
    try {
      const result = await api('/api/settings', { method: 'PUT', body: { watched_authors: list } });
      state.settings = result.settings;
      toast('作者追踪名单已保存');
      refreshSettingsNavValues();
      refreshSummary();
      if (state.route.name === 'watch') loadPapers();
    } catch (err) { toast(err.message, 'error'); }
  });
}

function fmtBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1048576) return (value / 1048576).toFixed(1) + ' MB';
  if (value >= 1024) return Math.round(value / 1024) + ' KB';
  return value + ' B';
}

function renderDataPane(pane) {
  pane.innerHTML = '<div class="settings-pane-title">数据与导出</div>' +
    '<p class="settings-section-desc">导出当前全部未归档论文，CSV 可直接用 Excel 打开，BibTeX 可导入 Zotero、EndNote 等工具。</p>' +
    '<div class="export-buttons export-buttons-stack">' +
      '<a class="btn btn-quiet" href="/api/export?format=csv&view=all">CSV 表格</a>' +
      '<a class="btn btn-quiet" href="/api/export?format=bibtex&view=all">BibTeX</a>' +
      '<a class="btn btn-quiet" href="/api/export?format=markdown&view=all">Markdown</a>' +
    '</div>' +
    '<div class="drawer-section-title"><span>本地 PDF 库</span></div>' +
    '<div class="settings-meta" id="local-pdf-stats">正在读取…</div>' +
    '<div class="settings-pane-actions"><button class="btn btn-quiet" id="prune-local-pdfs">清理失效记录</button></div>' +
    '<div class="settings-meta">当前共 ' + ((state.summary && state.summary.total) || 0) + ' 篇论文 · 数据库保存在本机 data/safelens.db · PDF 库在 data/pdfs/</div>';
  loadLocalPdfStats();
  $('#prune-local-pdfs').addEventListener('click', loadLocalPdfStats);
}

async function loadLocalPdfStats() {
  const node = $('#local-pdf-stats');
  if (!node) return;
  try {
    const data = await api('/api/local-pdfs');
    const stats = data.stats || { count: 0, bytes: 0 };
    node.textContent = '已缓存 ' + stats.count + ' 篇 PDF，共 ' + fmtBytes(stats.bytes) +
      (data.pruned ? (' · 已清理 ' + data.pruned + ' 条失效记录') : '') +
      '。点击“PDF 下载”时会自动缓存到本地，之后离线可开。';
  } catch (err) {
    node.textContent = '本地 PDF 统计读取失败：' + err.message;
  }
}

function renderHistoryPane(pane) {
  pane.innerHTML = '<div class="settings-pane-title">同步记录</div>' +
    '<div class="modal-section-head"><p class="settings-section-desc">最近 30 次同步记录。</p><button class="text-link as-button" id="refresh-history">刷新</button></div>' +
    '<div class="history-table-wrap"><table class="history-table"><thead><tr><th>开始时间</th><th>触发</th><th>状态</th><th>期刊</th><th>新增收录</th><th>更新</th><th>耗时</th></tr></thead><tbody id="history-body"></tbody></table></div>';
  $('#refresh-history').addEventListener('click', loadHistory);
  loadHistory();
}

function renderAboutPane(pane) {
  pane.innerHTML = '<div class="settings-pane-title">关于 SafeLens</div>' +
    '<div class="about-card about-card-modal"><div class="about-mark">S</div><div>' +
    '<h3>SafeLens · 安全科学论文雷达</h3>' +
    '<p>本地优先的安全科学文献追踪工作台。官网 RSS 负责发现，Crossref 与 OpenAlex 负责补齐元数据，SQLite 保存你的阅读状态。</p>' +
    '<p class="about-meta">版本 1.1.0 · 数据目录 data/ · 快捷键：Ctrl K 搜索，Esc 关闭面板</p>' +
    '</div></div>';
}

function renderJournalPane(pane) {
  const journals = state.journals || [];
  const rows = journals.map(function (journal) {
    return '<div class="jm-row" style="--jc:' + esc(journal.color || '#c2552d') + '">' +
      '<span class="journal-swatch"></span>' +
      '<div class="jm-copy"><strong>' + esc(journal.name) + '</strong><span>ISSN ' + esc(journal.issn || '—') + ' · ' + (journal.enabled ? '监控中' : '已停用') + '</span></div>' +
      '<label class="switch" title="启用/停用"><input type="checkbox" data-jm-toggle="' + journal.id + '"' + (journal.enabled ? ' checked' : '') + '><span></span></label>' +
      '<button class="icon-btn" data-jm-edit="' + journal.id + '" title="编辑期刊"><svg><use href="#i-settings"></use></svg></button>' +
    '</div>';
  }).join('');
  pane.innerHTML = '<div class="settings-pane-head"><div><div class="settings-pane-title">期刊管理</div>' +
    '<p class="settings-section-desc">共 ' + journals.length + ' 本期刊。停用期刊不会删除已收录的论文，也会暂停后续检查。</p></div>' +
    '<button class="btn btn-primary" id="jm-add">添加期刊</button></div>' +
    '<div class="journal-manager-list">' + (rows || '<div class="modal-empty">还没有期刊</div>') + '</div>';
  $('#jm-add').addEventListener('click', function () { openJournalModal(null, true); });
  pane.onclick = function (event) {
    const edit = event.target.closest('[data-jm-edit]');
    if (!edit) return;
    const journal = journalById(Number(edit.getAttribute('data-jm-edit')));
    if (journal) openJournalModal(journal, true);
  };
  pane.onchange = async function (event) {
    const toggle = event.target.closest('[data-jm-toggle]');
    if (!toggle) return;
    await toggleJournal(Number(toggle.getAttribute('data-jm-toggle')), toggle.checked);
    renderSettingsSection('journals');
  };
}
function parseKeywords(value) {
  return String(value || '').split(/[,，;；]/).map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 30);
}

function renderKeywordPreview(words) {
  const node = $('#keyword-preview');
  if (!node) return;
  node.innerHTML = (words || []).map(function (w) { return '<span>' + esc(w) + '</span>'; }).join('');
}

function renderScheduler() {
  const scheduler = state.scheduler || {};
  const pill = $('#scheduler-pill');
  if (pill) {
    pill.textContent = scheduler.installed ? '已安装' : '未安装';
    pill.className = 'pill ' + (scheduler.installed ? 'ok' : 'error');
  }
  setText('#settings-schedule-value', scheduler.installed ? ((state.settings && state.settings.daily_time) || '08:00') : '未安装');
  setText('#scheduler-next', scheduler.installed ? (scheduler.next_run || '—') : '—');
  const bits = [];
  if (scheduler.installed) {
    const stateMap = { Ready: '就绪', Running: '运行中', Disabled: '已禁用', Queued: '排队中' };
    bits.push('状态：' + (stateMap[scheduler.state] || scheduler.state || '—'));
    if (scheduler.last_run) bits.push('上次运行：' + scheduler.last_run);
    else bits.push('上次运行：尚未运行');
    if (scheduler.last_result != null) bits.push(scheduler.last_result === 0 ? '上次结果：成功' : ('返回码：' + scheduler.last_result));
    bits.push('任务名：' + (scheduler.task_name || 'SafetyScienceMonitor_Daily0800'));
  } else {
    bits.push('尚未安装 Windows 计划任务。点击上方按钮即可创建，之后即使不打开工作台也会每天自动检查。');
    if (scheduler.error) bits.push('提示：计划任务状态读取失败，请重新安装。');
  }
  setText('#scheduler-meta', bits.join(' · '));
}

async function saveSettings() {
  const timeNode = $('#setting-time');
  const keywordNode = $('#setting-keywords');
  const dailyTime = timeNode ? (timeNode.value || '08:00') : ((state.settings && state.settings.daily_time) || '08:00');
  const keywords = keywordNode ? parseKeywords(keywordNode.value) : ((state.settings && state.settings.watch_keywords) || []);
  try {
    const result = await api('/api/settings', { method: 'PUT', body: { daily_time: dailyTime, watch_keywords: keywords } });
    state.settings = result.settings;
    closeModal();
    toast('设置已保存');
    refreshSettingsNavValues();
    renderSidebar();
  } catch (err) { toast(err.message, 'error'); }
}

async function installScheduler() {
  const button = $('#install-scheduler');
  button.disabled = true;
  button.textContent = '正在安装…';
  try {
    const timeNode = $('#setting-time');
    const keywordNode = $('#setting-keywords');
    const dailyTime = timeNode ? (timeNode.value || '08:00') : ((state.settings && state.settings.daily_time) || '08:00');
    const keywords = keywordNode ? parseKeywords(keywordNode.value) : ((state.settings && state.settings.watch_keywords) || []);
    await api('/api/settings', { method: 'PUT', body: { daily_time: dailyTime, watch_keywords: keywords } });
    const result = await api('/api/scheduler/install', { method: 'POST', body: {} });
    if (result.ok) {
      state.scheduler = result.scheduler || null;
      state.settings.daily_time = dailyTime;
      state.settings.watch_keywords = keywords;
      toast('每日 ' + dailyTime + ' 检查任务已安装');
      renderScheduler();
      refreshSettingsNavValues();
      renderSidebar();
    } else {
      toast(result.error || '计划任务安装失败', 'error');
    }
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '保存并安装计划任务';
  }
}

async function loadHistory() {
  const tbody = $('#history-body');
  if (!tbody) return;
  try {
    const data = await api('/api/sync/history?limit=10');
    const runs = data.runs || [];
    if (!runs.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="history-empty">还没有同步记录</td></tr>';
      return;
    }
    tbody.innerHTML = runs.map(function (run) {
      const start = parseLocal(run.started_at);
      const end = parseLocal(run.finished_at || run.started_at);
      const seconds = start && end ? Math.max(0, Math.round((end - start) / 1000)) : 0;
      const statusText = { done: '完成', partial: '部分完成', failed: '失败', running: '运行中', cancelled: '已中断' }[run.status] || run.status;
      return '<tr><td>' + esc(fmtDateTime(run.started_at)) + '</td><td>' + esc(run.trigger) + '</td>' +
        '<td><span class="run-status ' + esc(run.status) + '">' + esc(statusText) + '</span></td>' +
        '<td>' + (run.journals_ok || 0) + '/' + (run.journals_total || 0) + '</td>' +
        '<td>' + (run.new_count || 0) + '</td><td>' + (run.updated_count || 0) + '</td><td>' + seconds + 's</td></tr>';
    }).join('');
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="7">读取失败：' + esc(err.message) + '</td></tr>';
  }
}

async function startSync(journalId, force) {
  if (state.syncState && state.syncState.running) {
    openSyncPopover();
    toast('已有检查正在运行', 'info');
    return;
  }
  if (!force && !journalId && syncedToday()) {
    openSyncConfirm();
    return;
  }
  state.syncConfirm = false;
  try {
    const body = journalId ? { journal_id: journalId } : {};
    const data = await api('/api/sync', { method: 'POST', body: body });
    state.syncState = data.state;
    openSyncPopover();
    renderSyncUI();
    toast(journalId ? '已开始检查该期刊' : '已开始检查全部期刊', 'info');
    startPolling();
  } catch (err) { toast(err.message, 'error'); }
}

function startPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(pollSync, 1200);
  pollSync();
}

async function pollSync() {
  try {
    const data = await api('/api/sync/status');
    state.syncState = data.state || {};
    renderSyncUI();
    if (!state.syncState.running) {
      clearInterval(state.poll);
      state.poll = null;
      await refreshSummary();
      await refreshJournals();
      if (['today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive', 'journal'].includes(state.route.name)) loadPapers();
      if (state.syncState.status === 'done') {
        toast('检查完成，新增收录 ' + (state.syncState.new || 0) + ' 篇');
      } else if (state.syncState.status === 'cancelled') {
        toast('检查已中断，已完成的条目已保存', 'info');
      } else if (state.syncState.status === 'partial' || state.syncState.status === 'failed') {
        toast(state.syncState.message || '检查未完全成功，请查看同步记录', 'error');
      }
    }
  } catch (err) {
    clearInterval(state.poll);
    state.poll = null;
  }
}

async function handleRoute(force) {
  const route = parseRoute();
  const routeChanged = state.route.name !== route.name || Number(state.route.journalId) !== Number(route.journalId);
  state.route = route;
  if (routeChanged) state.page = 1;
  if (route.name === 'journal') state.view = 'all';
  closeMobileNav();
  $$('.view').forEach(function (node) { node.classList.add('hidden'); });
  const meta = VIEW_META[route.name] || VIEW_META.home;
  if (route.name === 'home') {
    $('#view-home').classList.remove('hidden');
    setText('#page-title', '总览');
    setText('#page-eyebrow', meta.eyebrow);
    renderDashboard();
  } else if (['today', 'inbox', 'starred', 'later', 'watch', 'all', 'archive', 'journal'].includes(route.name)) {
    $('#view-papers').classList.remove('hidden');
    if (route.name === 'journal') {
      state.journalFilter = String(route.journalId);
      const journal = journalById(route.journalId);
      setText('#page-title', journal ? journal.name : '期刊');
      setText('#page-eyebrow', 'JOURNAL · ' + (journal ? (journal.publisher || '') : ''));
    } else {
      state.journalFilter = '';
      setText('#page-title', meta.title);
      setText('#page-eyebrow', meta.eyebrow);
    }
    await loadPapers();
  } else if (route.name === 'journals') {
    $('#view-journals').classList.remove('hidden');
    setText('#page-title', '期刊监控');
    setText('#page-eyebrow', 'SOURCE LIBRARY');
    renderJournals();
  } else if (route.name === 'settings') {
    navigate('#/home');
    return;
  }
  setActiveNav(route.name);
  if (force) window.scrollTo({ top: 0, behavior: 'auto' });
}

function paperFromCurrent(id) {
  return (state.data.items || []).find(function (p) { return p.id === id; }) ||
    ((state.summary && state.summary.latest) || []).find(function (p) { return p.id === id; }) ||
    null;
}

async function openDrawerById(id) {
  let paper;
  try {
    const data = await api('/api/papers/' + id);
    paper = data.paper;
  } catch (err) { toast(err.message, 'error'); return; }
  state.drawerPaper = paper;
  state.drawerOpen = true;
  $('#drawer-layer').classList.remove('hidden');
  $('#drawer-layer').setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  renderDrawer(paper);
  $('#drawer-close').focus();
}

function closeDrawer() {
  state.drawerOpen = false;
  state.drawerPaper = null;
  $('#drawer-layer').classList.add('hidden');
  $('#drawer-layer').setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

function metadataText(value) {
  if (Array.isArray(value)) return value.map(metadataText).join('；');
  if (value && typeof value === 'object') return JSON.stringify(value);
  return value == null ? '' : String(value);
}

function metadataLink(url, label) {
  if (!/^https?:\/\//i.test(url || '')) return '';
  return '<a class="drawer-link" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(label) + '</a>';
}

function renderSourceMetadata(paper) {
  const source = paper.source_metadata || {};
  const audit = paper.metadata_audit || {};
  const labels = { verified: '原文已核实', missing: '原文未提取到', conflict: '与已有记录不同', enriched: '原文补全', extracted: '原文已提取', available: '已提取', unavailable: '无法核查', stale: '数据已变更，需重新核查', failed: '核查失败', partial: '部分字段待核查', ok: '核查完成', unverified: '未核实', pending: '待核查' };
  const fields = [['title', '标题'], ['authors', '作者'], ['affiliations', '机构'], ['funding', '基金'], ['abstract', '摘要'], ['keywords', '关键词']];
  const englishOnly = !/[\u3400-\u9fff]/.test(source.title || paper.title || '');
  const rows = fields.map(function (pair) {
    return '<div class="source-field"><h4>' + pair[1] + '</h4>' + (englishOnly ? ['en'] : ['zh', 'en']).map(function (lang) {
      const value = source[pair[0] + '_' + lang];
      return '<div class="source-language"><span>' + (lang === 'zh' ? '中文' : 'English') + '</span><p>' + esc(metadataText(value) || '尚未从原文提取到') + '</p></div>';
    }).join('') + (source[pair[0]] && !source[pair[0] + '_zh'] && !source[pair[0] + '_en'] ? '<p>' + esc(metadataText(source[pair[0]])) + '</p>' : '') + '</div>';
  }).join('');
  const fieldNames = { doi: 'DOI', pdf_url: 'PDF 链接', preview_url: '在线浏览', citation_text: '原文引用', citation_text_en: '英文引用', download_links: '下载格式' };
  fields.forEach(function (pair) { fieldNames[pair[0]] = pair[1]; fieldNames[pair[0] + '_zh'] = pair[1] + '（中文）'; fieldNames[pair[0] + '_en'] = pair[1] + '（英文）'; });
  const acceptance = audit.acceptance || { status: 'pending', reasons: ['尚未完成合格检验'] };
  const acceptanceLabels = { passed: '合格', failed: '未通过', blocked: '受阻', pending: '待核查' };
  const findings = Object.entries(audit.fields || {}).map(function (entry) {
    const field = entry[0], finding = entry[1];
    return '<details class="audit-field"><summary>' + esc(fieldNames[field] || field) + ' · ' + esc(labels[finding.status] || finding.status || '待核查') + '</summary>' +
      '<p>原文：' + esc(metadataText(finding.source_value) || '未提取到') + '</p>' +
      '<p>库中：' + esc(metadataText(finding.stored_value) || '未记录') + '</p>' +
      '<p>证据：' + esc(metadataText(finding.evidence) || '无') + '</p>' + metadataLink(finding.source_url || audit.final_url || audit.source_url, '查看证据页面') + '</details>';
  }).join('');
  return '<section class="source-panel"><div class="drawer-section-title"><span>原文信息 · 中英文</span></div>' +
    '<p class="settings-meta">保留最近一次成功提取的官网原文，不自动翻译；空项表示尚未提取到。</p>' + rows +
    '<div class="drawer-section-title"><span>官网引用与下载格式</span></div>' +
    ['citation_text', 'citation_text_en'].map(function (key) { return source[key] ? '<p class="drawer-extra">' + esc(source[key]) + '</p>' : ''; }).join('') +
    '<div class="drawer-link-row">' + metadataLink(source.preview_url, '官网在线浏览') + metadataLink(source.pdf_url, '官网 PDF') + '</div>' +
    '<div class="drawer-link-row">' +
    [['bibtex', 'BibTeX'], ['ris', 'RIS / EndNote'], ['json', '完整元数据 JSON'], ['csv', 'CSV']].map(function (f) {
      return '<a class="drawer-link" href="/api/export?format=' + f[0] + '&ids=' + paper.id + '">' + f[1] + '</a>';
    }).join('') + '</div><p class="settings-meta">以上引用文件由本地元数据生成。</p>' +
    '<div class="drawer-link-row">' + (source.download_links || []).map(function (link) { return metadataLink(link.url, '官网 · ' + (link.label || link.format || '下载')); }).join('') + '</div>' +
    '<div class="drawer-section-title"><span>审计 Agent · 原文核查</span></div>' +
    '<p class="audit-acceptance" data-acceptance="' + esc(acceptance.status) + '">合格检验：' + esc(acceptanceLabels[acceptance.status] || '待核查') + '</p>' +
    '<ul>' + (acceptance.reasons || []).map(function (reason) { return '<li>' + esc(reason) + '</li>'; }).join('') + '</ul>' +
    '<button class="btn btn-quiet" data-run-audit="' + paper.id + '">抓取原文并核查</button>' +
    '<p class="settings-meta">' + esc(audit.status ? (labels[audit.status] || audit.status) : '尚未核查') + (audit.checked_at ? ' · ' + esc(audit.checked_at) : '') + '</p>' +
    (audit.errors || []).map(function (error) { return '<p class="audit-error">' + esc(metadataText(error)) + '</p>'; }).join('') +
    '<p class="settings-meta">' + esc(audit.link_check || '链接需在实际打开时确认可用性。') + '</p>' + metadataLink(audit.final_url || audit.source_url, '本次核查来源') +
    '<details><summary>逐字段证据与差异</summary>' + (findings || '<p>运行核查后展示字段证据。</p>') + '</details></section>';
}

function watchAuthorsMatched(paper) {
  const watched = (state.settings && state.settings.watched_authors) || [];
  if (!watched.length || !paper) return [];
  const authors = (paper.authors || []).concat(paper.authors_en || []);
  return watched.filter(function (w) {
    const needle = String(w).toLowerCase();
    return authors.some(function (a) { return String(a).toLowerCase().indexOf(needle) >= 0; });
  });
}

async function loadRelatedPapers(paperId) {
  const node = $('#drawer-related');
  if (!node) return;
  try {
    const data = await api('/api/papers/' + paperId + '/related');
    if (!state.drawerOpen || !state.drawerPaper || state.drawerPaper.id !== paperId) return;
    const items = data.items || [];
    node.innerHTML = items.length ? items.map(function (p) {
      return '<button class="related-item" data-open="' + p.id + '" title="打开论文详情">' +
        '<span class="related-title">' + esc(p.title) + '</span>' +
        '<span class="related-meta">' + esc(p.journal_name || '') + ' · ' + esc((p.related_reasons || []).join('、') || '相关') + '</span>' +
      '</button>';
    }).join('') : '<p class="settings-meta">暂未找到足够相似的论文；收库越多，推荐越准。</p>';
  } catch (err) {
    if (node && state.drawerPaper && state.drawerPaper.id === paperId) {
      node.innerHTML = '<p class="settings-meta">相关论文加载失败：' + esc(err.message) + '</p>';
    }
  }
}

function renderDrawer(paper) {
  if (!paper) return;
  const sourceFields = paper.source_metadata || {};
  const color = paper.journal_color || '#c2552d';
  const drawerJournal = $('#drawer-journal');
  drawerJournal.innerHTML = '<i style="--jc:' + esc(color) + '"></i>' + esc(paper.journal_name || '');
  const keywords = state.settings.watch_keywords || [];
  const abstract = paper.abstract || '';
  const topics = paper.topics || [];
  const userTags = paper.user_tags || [];
  const meta = [
    ['期刊', paper.journal_name || '—'],
    ['发表', fmtDate(paper.published_date, paper.published_precision)],
    ['在线', paper.online_date || '—'],
    ['卷 / 期', [paper.volume, paper.issue].filter(Boolean).join(' / ') || '—'],
    ['页码', paper.pages || '—'],
    ['DOI', paper.doi || '—'],
    ['来源', paper.source_label || '期刊官网']
  ];
  if (paper.received_date) meta.push(['收稿', paper.received_date]);
  if (paper.revised_date) meta.push(['修回', paper.revised_date]);
  if (paper.accepted_date) meta.push(['录用', paper.accepted_date]);
  const citations = paper.citation_count || 0;
  const sourceUrl = paperSourceUrl(paper);
  const previewUrl = paperPreviewUrl(paper);
  const pdfUrl = paperPdfUrl(paper);
  const englishOnly = !/[\u3400-\u9fff]/.test(paper.title || '');
  const citationText = englishOnly ? (sourceFields.citation_text_en || paper.citation_text_en || formatCitation(paper)) : formatCitation(paper);
  const citationEn = englishOnly ? '' : (sourceFields.citation_text_en || paper.citation_text_en || '');
  const citationRow = function (label, text, lang) {
    return '<div class="drawer-citation-row"><span class="drawer-citation-label">' + label + '</span>' +
      '<p>' + esc(text) + '</p>' +
      '<button class="citation-copy" data-citation-copy="' + lang + '" title="复制引用" aria-label="复制引用">' + icon('copy') + '</button></div>';
  };
  const linkRow = '<div class="drawer-link-row">' +
      '<a class="drawer-link" href="' + esc(previewUrl || sourceUrl || '#') + '" target="_blank" rel="noopener">在线浏览 ' + icon('external') + '</a>' +
      (pdfUrl ? '<a class="drawer-link" href="' + esc(pdfUrl) + '" title="' + (paper.has_local_pdf ? '本地库已保存，可直接打开' : '下载成功后自动存入本地 PDF 库') + '">' + (paper.has_local_pdf ? '打开 PDF（本地）' : 'PDF 下载') + '</a>' : '') +
      (sourceUrl ? '<a class="drawer-link" href="' + esc(sourceUrl) + '" target="_blank" rel="noopener">原文链接 ' + icon('external') + '</a>' : '') +
    '</div>' +
    '<div class="drawer-citation"><div class="drawer-citation-head"><span>引用格式</span></div>' +
      citationRow(englishOnly ? '英文' : '中文', citationText, 'cn') +
      (englishOnly ? '' : citationEn ? citationRow('英文', citationEn, 'en') : '<div class="drawer-citation-row is-empty"><span class="drawer-citation-label">英文</span><p>英文引用暂未获取</p></div>') +
    '</div>';
  const matchedWatch = watchAuthorsMatched(paper);
  const watchBadge = matchedWatch.length ? '<span class="badge watch" title="命中作者追踪名单">关注作者 · ' + esc(matchedWatch.slice(0, 2).join('、')) + '</span>' : '';
  const risingBadge = paper.citation_rising ? '<span class="badge rising" title="近期被引数明显上升">引用上升</span>' : '';
  const localPdfBadge = paper.has_local_pdf ? '<span class="badge pdf" title="PDF 已保存到本地库">本地PDF</span>' : '';
  const body = '<div class="drawer-meta-row">' +
      '<span class="badge ' + (isFresh(paper) ? 'new' : 'neutral') + '">' + (isFresh(paper) ? 'NEW' : 'IN LIBRARY') + '</span>' +
      watchBadge + risingBadge + localPdfBadge +
      '<span class="settings-meta">入库 ' + fmtDateTime(paper.first_seen_at) + ' · 引用 ' + citations + '</span>' +
    '</div>' +
    '<h2 class="drawer-title">' + highlight(paper.title, keywords) + '</h2>' +
    (paper.title_en ? '<p class="drawer-title-en">' + esc(paper.title_en) + '</p>' : '') +
    '<p class="drawer-authors"><strong>' + esc(shortAuthors(paper.authors, 99) || '作者信息待补') + '</strong></p>' +
    (paper.authors_en && paper.authors_en.length ? '<p class="drawer-authors-en">' + esc(paper.authors_en.join(', ')) + '</p>' : '') +
    '<div class="drawer-meta">' + meta.map(function (pair) {
      return '<div class="meta-item"><span>' + esc(pair[0]) + '</span><strong>' + esc(pair[1]) + '</strong></div>';
    }).join('') + '</div>' + linkRow + renderSourceMetadata(paper) +
    (sourceFields.abstract ? '' : '<div class="drawer-section-title"><span>摘要</span>' + (paper.open_access ? '<span class="badge oa">OPEN ACCESS</span>' : '') + '</div>' +
    (abstract ? '<p class="drawer-abstract" id="drawer-abstract">' + highlight(abstract, keywords) + '</p><button class="abstract-toggle" id="abstract-toggle">收起摘要</button>' : '<p class="drawer-abstract">摘要尚未获取，可抓取原文并核查。</p>')) +
    '<div class="drawer-section-title"><span>主题</span></div>' +
    '<div class="drawer-tags">' + (topics.length ? topics.map(function (t) { return '<span class="mini-tag"><i style="--tc:' + topicColor(t) + '"></i>' + esc(t) + '</span>'; }).join('') : '<span class="settings-meta">暂无主题标签</span>') + '</div>' +
    (paper.keywords && paper.keywords.length ? '<div class="drawer-section-title"><span>作者关键词</span></div><div class="drawer-tags">' + paper.keywords.slice(0, 12).map(function (k) { return '<span class="keyword-tag">' + esc(k) + '</span>'; }).join('') + '</div>' : '') +
    (paper.keywords_en && paper.keywords_en.length ? '<div class="drawer-section-title"><span>Keywords</span></div><div class="drawer-tags">' + paper.keywords_en.slice(0, 12).map(function (k) { return '<span class="keyword-tag">' + esc(k) + '</span>'; }).join('') + '</div>' : '') +
    (paper.affiliations ? '<div class="drawer-section-title"><span>作者机构</span></div><p class="drawer-extra">' + esc(paper.affiliations) + '</p>' : '') +
    (paper.funding ? '<div class="drawer-section-title"><span>基金信息</span></div><p class="drawer-extra">' + esc(paper.funding) + '</p>' : '') +
    (!sourceFields.abstract_en && paper.abstract_en ? '<div class="drawer-section-title"><span>英文摘要</span></div><p class="drawer-abstract drawer-abstract-en">' + esc(paper.abstract_en) + '</p>' : '') +
    '<div class="drawer-section-title"><span>我的标签</span></div>' +
    '<div class="drawer-tags" id="user-tags">' + (userTags.length ? userTags.map(function (t) {
      return '<span class="user-tag">' + esc(t) + '<button data-remove-tag="' + esc(t) + '" title="移除">×</button></span>';
    }).join('') : '<span class="settings-meta">还没有自定义标签</span>') + '</div>' +
    '<div class="tag-input-row"><input id="tag-input" placeholder="输入标签后回车，例如：待组会"><button id="add-tag">添加</button></div>' +
    '<div class="drawer-section-title"><span>阅读笔记</span><span id="note-status"></span></div>' +
    '<textarea class="drawer-notes" id="drawer-notes" placeholder="记录这篇文章对你的价值、方法或可复用的观点…">' + esc(paper.notes || '') + '</textarea>' +
    '<div class="drawer-section-title"><span>操作</span></div>' +
    '<div class="drawer-action-grid">' +
      '<button class="drawer-action ' + (paper.starred ? 'is-on' : '') + '" data-drawer-action="star">' + icon('star') + (paper.starred ? '已收藏' : '收藏') + '</button>' +
      '<button class="drawer-action ' + (paper.read_later ? 'is-on' : '') + '" data-drawer-action="later">' + icon('clock') + (paper.read_later ? '已在稍后读' : '稍后读') + '</button>' +
      '<button class="drawer-action" data-drawer-action="read">' + icon('check') + (paper.read_at ? '标为未读' : '标为已读') + '</button>' +
      '<button class="drawer-action" data-drawer-action="archive">' + icon('archive') + '归档</button>' +
    '</div>' +
    '<div class="drawer-section-title"><span>相关论文</span></div>' +
    '<div id="drawer-related"><p class="settings-meta">正在查找相关论文…</p></div>';
  $('#drawer-body').innerHTML = body;
  loadRelatedPapers(paper.id);
  const foot = '<div class="drawer-foot-actions">' +
      '<button class="icon-btn" id="drawer-note" title="聚焦笔记">' + icon('note') + '</button>' +
    '</div>';
  let footNode = $('#drawer .drawer-foot');
  if (!footNode) {
    footNode = document.createElement('div');
    footNode.className = 'drawer-foot';
    $('#drawer').appendChild(footNode);
  }
  footNode.innerHTML = foot;
  $$('.citation-copy').forEach(function (button) {
    button.addEventListener('click', function () {
      copyText(button.getAttribute('data-citation-copy') === 'en' ? citationEn : citationText);
    });
  });
  $('#drawer-note').addEventListener('click', function () { $('#drawer-notes').focus(); });
  const toggle = $('#abstract-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      const node = $('#drawer-abstract');
      const clamped = node.classList.toggle('clamped');
      toggle.textContent = clamped ? '展开全文摘要' : '收起摘要';
    });
  }
  const notes = $('#drawer-notes');
  const saveNotes = debounce(async function () {
    try {
      await api('/api/papers/' + paper.id, { method: 'PATCH', body: { notes: notes.value } });
      paper.notes = notes.value;
      setText('#note-status', '已保存');
      window.setTimeout(function () { setText('#note-status', ''); }, 1800);
    } catch (err) { toast(err.message, 'error'); }
  }, 700);
  notes.addEventListener('input', saveNotes);
  $('#add-tag').addEventListener('click', addUserTag);
  $('#tag-input').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); addUserTag(); }
  });
}

async function addUserTag() {
  const paper = state.drawerPaper;
  if (!paper) return;
  const input = $('#tag-input');
  const value = input.value.trim();
  if (!value) return;
  const tags = (paper.user_tags || []).slice();
  if (tags.indexOf(value) < 0) tags.push(value);
  try {
    const result = await api('/api/papers/' + paper.id, { method: 'PATCH', body: { user_tags: tags } });
    state.drawerPaper = result.paper;
    renderDrawer(result.paper);
  } catch (err) { toast(err.message, 'error'); }
}

function handleDrawerClick(event) {
  const auditButton = event.target.closest('[data-run-audit]');
  if (auditButton) {
    const id = Number(auditButton.dataset.runAudit);
    auditButton.disabled = true;
    auditButton.textContent = '正在抓取和核查…';
    api('/api/papers/' + id + '/audit', { method: 'POST', body: {} }).then(function (result) {
      if (state.drawerOpen && state.drawerPaper && state.drawerPaper.id === id) {
        state.drawerPaper = result.paper;
        renderDrawer(result.paper);
      }
    }).catch(function (error) { toast(error.message, 'error'); }).finally(function () {
      auditButton.disabled = false;
      auditButton.textContent = '抓取原文并核查';
    });
    return;
  }
  const action = event.target.closest('[data-drawer-action]');
  if (action) {
    const name = action.getAttribute('data-drawer-action');
    if (name === 'read') togglePaper(state.drawerPaper.id, 'read');
    else if (name === 'star') togglePaper(state.drawerPaper.id, 'star');
    else if (name === 'later') togglePaper(state.drawerPaper.id, 'later');
    else if (name === 'archive') togglePaper(state.drawerPaper.id, 'archive');
    return;
  }
  const remove = event.target.closest('[data-remove-tag]');
  if (remove && state.drawerPaper) {
    const tags = (state.drawerPaper.user_tags || []).filter(function (t) { return t !== remove.getAttribute('data-remove-tag'); });
    api('/api/papers/' + state.drawerPaper.id, { method: 'PATCH', body: { user_tags: tags } }).then(function (result) {
      state.drawerPaper = result.paper;
      renderDrawer(result.paper);
    }).catch(function (err) { toast(err.message, 'error'); });
  }
}

function handleDrawerKeydown(event) {
  if (event.key === 'Enter' && event.target.id === 'tag-input') {
    event.preventDefault();
    addUserTag();
  }
}

function moveDrawer(delta) {
  if (!state.data.items.length || !state.drawerPaper) return;
  const index = state.data.items.findIndex(function (p) { return p.id === state.drawerPaper.id; });
  if (index < 0) return;
  const next = state.data.items[index + delta];
  if (next) openDrawerById(next.id);
}

function copyCitation(paper) {
  copyText(formatCitation(paper));
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { toast('已复制到剪贴板'); }).catch(function () { fallbackCopy(text); });
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); toast('已复制到剪贴板'); }
  catch (err) { toast('复制失败，请手动选择文本', 'error'); }
  area.remove();
}

function openModal(config) {
  state.currentModal = config;
  $('#modal').className = 'modal' + (config.modalClass ? ' ' + config.modalClass : '');
  $('#modal-kicker').textContent = config.kicker || 'SAFELENS';
  $('#modal-title').textContent = config.title || '';
  $('#modal-body').onclick = null;
  $('#modal-body').onchange = null;
  $('#modal-body').innerHTML = config.body || '';
  const foot = config.foot || '';
  $('#modal-foot').innerHTML = foot;
  $('#modal-foot').classList.toggle('hidden', !foot);
  $('#modal-layer').classList.remove('hidden');
  $('#modal-layer').setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  if (config.onMount) config.onMount();
}

function closeModal() {
  state.currentModal = null;
  $('#modal-layer').classList.add('hidden');
  $('#modal-layer').setAttribute('aria-hidden', 'true');
  if (!state.drawerOpen) document.body.style.overflow = '';
}

async function openDigest(scope, focus) {
  state.digestScope = 'today';
  state.digestFocus = focus || '';
  openModal({
    kicker: 'DAILY DIGEST',
    title: '安全科学论文简报',
    body: '<div id="digest-content"><div class="skeleton-row"></div><div class="skeleton-row"></div></div>',
    foot: '<span class="spacer"></span><button class="btn btn-quiet" id="digest-copy">' + icon('copy') + '复制 Markdown</button><button class="btn btn-primary" id="digest-download">' + icon('download') + '下载 .md</button>',
    onMount: function () {
      $('#digest-copy').addEventListener('click', function () { if (state.digest) copyText(state.digest.markdown); });
      $('#digest-download').addEventListener('click', downloadDigest);
      loadDigest(state.digestScope, state.digestFocus);
    }
  });
}

async function loadDigest(scope, focus) {
  state.digestScope = 'today';
  state.digestFocus = focus || '';
  const content = $('#digest-content');
  if (content) content.innerHTML = '<div class="skeleton-row"></div><div class="skeleton-row"></div>';
  try {
    let url = '/api/digest?scope=today';
    if (focus === 'interests') url += '&focus=interests';
    const data = await api(url);
    state.digest = data;
    renderDigest(data);
  } catch (err) {
    if (content) content.innerHTML = '<div class="digest-empty">' + esc(err.message) + '</div>';
  }
}

function renderDigest(data) {
  const node = $('#digest-content');
  if (!node) return;
  const tabs = [
    { scope: 'today', focus: '', label: '今日新增' },
    { scope: 'today', focus: 'interests', label: '我的关注' }
  ];
  const activeFocus = data.focus || '';
  let html = '<div class="digest-tabs">' + tabs.map(function (tab) {
    const active = data.scope === tab.scope && activeFocus === tab.focus;
    return '<button class="digest-tab ' + (active ? 'active' : '') + '" data-digest-scope="' + tab.scope + '" data-digest-focus="' + tab.focus + '">' + tab.label + '</button>';
  }).join('') + '</div>';
  if (!data.count) {
    if (activeFocus === 'interests') {
      html += '<div class="digest-empty"><strong>今天暂无匹配“我的关注”的论文</strong><br>' +
        ((data.interests || []).length ? '可以在设置 → 研究偏好中调整关注领域。' : '还没有设置研究偏好，先选择你关注的领域。') +
        '<br><button class="text-link as-button" id="digest-edit-interests">设置研究偏好</button></div>';
    } else {
      html += '<div class="digest-empty"><strong>' + esc(data.label) + '暂无论文</strong><br>今天还没有收到新的期刊更新。</div>';
    }
  } else {
    const totalCount = data.total_count != null ? data.total_count : data.count;
    html += '<p class="modal-intro">' + esc(data.date) + ' · ' + esc(data.label) + ' · 共 ' + totalCount + ' 篇' +
      (data.truncated ? '（显示前 ' + data.count + ' 篇）' : '') + '</p>';
    html += data.groups.map(function (group) {
      return '<div class="digest-group"><h4><i style="--jc:' + esc(group.color || '#c2552d') + '"></i>' + esc(group.journal) + '<span>' + group.items.length + ' 篇</span></h4>' +
        group.items.map(function (item) {
          const itemUrl = paperSourceUrl(item);
          return '<div class="digest-item"><div class="digest-item-main"><a href="' + esc(itemUrl || '#') + '" target="_blank" rel="noopener">' + esc(item.title) + '</a>' +
            '<p>' + esc(shortAuthors(item.authors, 2)) + ' · ' + esc(fmtDate(item.published_date, item.published_precision)) + '</p></div>' +
            '<div class="digest-item-actions">' +
              '<button class="paper-action ' + (item.starred ? 'is-on' : '') + '" data-digest-action="star" data-id="' + item.id + '" title="收藏">' + icon('star') + '</button>' +
              '<button class="paper-action ' + (item.read_later ? 'is-on' : '') + '" data-digest-action="later" data-id="' + item.id + '" title="稍后读">' + icon('clock') + '</button>' +
            '</div></div>';
        }).join('') + '</div>';
    }).join('');
  }
  node.innerHTML = html;
  $$('[data-digest-action]').forEach(function (button) {
    button.addEventListener('click', async function () {
      const id = Number(button.getAttribute('data-id'));
      const action = button.getAttribute('data-digest-action');
      await togglePaper(id, action);
      const item = digestItemById(id);
      if (item) button.classList.toggle('is-on', action === 'star' ? !!item.starred : !!item.read_later);
    });
  });
  $$('[data-digest-scope]').forEach(function (button) {
    button.addEventListener('click', function () {
      loadDigest(button.getAttribute('data-digest-scope'), button.getAttribute('data-digest-focus'));
    });
  });
  const editInterests = $('#digest-edit-interests');
  if (editInterests) editInterests.addEventListener('click', function () {
    closeModal();
    openSettingsDialog('interests');
  });
}

function downloadDigest() {
  if (!state.digest) return;
  const blob = new Blob([state.digest.markdown], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'safelens-digest-' + state.digest.date + '.md';
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function openExportModal() {
  openModal({
    kicker: 'EXPORT',
    title: '导出文献',
    body: '<p class="modal-intro">导出当前“全部论文”视图中的未归档论文。CSV 适合 Excel，BibTeX 可直接导入 Zotero / EndNote。</p>' +
      '<div class="export-buttons" style="flex-direction:column;align-items:stretch">' +
        '<a class="btn btn-quiet" href="/api/export?format=csv&view=all">CSV 表格（Excel 可直接打开）</a>' +
        '<a class="btn btn-quiet" href="/api/export?format=bibtex&view=all">BibTeX（Zotero / EndNote）</a>' +
        '<a class="btn btn-quiet" href="/api/export?format=markdown&view=all">Markdown（适合笔记软件）</a>' +
      '</div>' +
      '<div class="modal-note">当前共 ' + ((state.summary && state.summary.total) || 0) + ' 篇论文。导出不会改变阅读状态。</div>'
  });
}

document.addEventListener('click', function (event) {
  const open = event.target.closest('[data-open]');
  if (open) { openDrawerById(Number(open.getAttribute('data-open'))); return; }
  const card = event.target.closest('.paper-card');
  if (card && !event.target.closest('input, button, a, label')) {
    openDrawerById(Number(card.getAttribute('data-id')));
    return;
  }
  const action = event.target.closest('[data-action]');
  if (action) {
    event.preventDefault();
    const id = Number(action.getAttribute('data-id'));
    const name = action.getAttribute('data-action');
    if (name === 'star' || name === 'later' || name === 'read') togglePaper(id, name);
    else if (name === 'archive') togglePaper(id, 'archive');
    return;
  }
  const external = event.target.closest('[data-open-external]');
  if (external) {
    event.preventDefault();
    openExternal(Number(external.getAttribute('data-open-external')));
    return;
  }
  const select = event.target.closest('[data-select]');
  if (select) {
    const id = Number(select.getAttribute('data-select'));
    if (select.checked) state.selected.add(id); else state.selected.delete(id);
    const card = select.closest('.paper-card');
    if (card) card.classList.toggle('selected', select.checked);
    renderSelectionBar();
    const all = $('#select-all');
    if (all) all.checked = state.data.items.length > 0 && state.data.items.every(function (p) { return state.selected.has(p.id); });
  }
});

document.addEventListener('DOMContentLoaded', function () {
  try {
    const saved = JSON.parse(localStorage.getItem('safelens.journalGroups') || '{}');
    if (typeof saved.domestic === 'boolean') state.journalGroups.domestic = saved.domestic;
    if (typeof saved.international === 'boolean') state.journalGroups.international = saved.international;
  } catch (err) { /* 忽略损坏的本地设置 */ }
  bindEvents();
  loadBootstrap();
});

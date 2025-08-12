import { apiFetch } from '/static/js/api.js';

const authScreen = document.getElementById('auth-screen');
const mainScreen = document.getElementById('main-screen');

function showAuth(show) {
  authScreen.classList.toggle('hidden', !show);
  mainScreen.classList.toggle('hidden', show);
}

function formatDateForMobile(dateString) {
    if (!dateString) return 'N/A';
    try {
        const d = new Date(dateString);
        return `<div class="date-cell">${d.toLocaleDateString()}<br><span class="time-part">${d.toLocaleTimeString()}</span></div>`;
    } catch (e) {
        return 'Invalid Date';
    }
}

async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const errorEl = document.getElementById('auth-error');
  errorEl.textContent = '';
  const formData = new URLSearchParams();
  formData.append('username', username);
  formData.append('password', password);
  try {
    const res = await fetch('/api/ui/auth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData,
    });
    if (!res.ok) {
      let msg = '用户名或密码错误';
      try { const d = await res.json(); msg = d.detail || msg; } catch {}
      throw new Error(msg);
    }
    const data = await res.json();
    localStorage.setItem('danmu_api_token', data.access_token);
    await checkLogin();
  } catch (err) {
    errorEl.textContent = `登录失败: ${err.message || err}`;
  }
}

async function logout() {
  try { await apiFetch('/api/ui/auth/logout', { method: 'POST' }); } catch {}
  localStorage.removeItem('danmu_api_token');
  showAuth(true);
}

async function checkLogin() {
  const token = localStorage.getItem('danmu_api_token');
  if (!token) { showAuth(true); return; }
  try {
    const me = await apiFetch('/api/ui/auth/users/me');
    document.getElementById('current-user-name').textContent = me.username || '';
    showAuth(false);
  } catch (err) {
    localStorage.removeItem('danmu_api_token');
    showAuth(true);
  }
}

function toggleLoader(show) {
  document.getElementById('loader').classList.toggle('hidden', !show);
}

// 搜索结果骨架屏切换
function showResultsSkeleton(show) {
  const sk = document.getElementById('results-skeleton');
  if (!sk) return;
  sk.classList.toggle('hidden', !show);
  // 同时隐藏/显示实际结果与“空”提示，避免视觉重叠
  const list = document.getElementById('results-list');
  const empty = document.getElementById('results-empty');
  if (show) {
    if (list) list.innerHTML = '';
    empty?.classList.add('hidden');
  }
}

function typeToLabel(t) {
  return ({ tv_series: '电视节目', movie: '电影/剧场版' }[t] || t);
}

function renderResults(items) {
  const ul = document.getElementById('results-list');
  ul.innerHTML = '';
  const empty = document.getElementById('results-empty');
  if (!items || items.length === 0) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  items.forEach(item => {
    const li = document.createElement('li');
    const poster = createPosterImage(item.imageUrl, item.title);

    const info = document.createElement('div');
    info.className = 'info';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = item.title;
    const meta = document.createElement('div');
    meta.className = 'meta';
    const parts = [`源: ${item.provider}`, `类型: ${typeToLabel(item.type)}`];
    if (item.type === 'tv_series') {
      if (item.season) parts.push(`季: ${String(item.season).padStart(2, '0')}`);
      if (item.currentEpisodeIndex) parts.push(`集: ${String(item.currentEpisodeIndex).padStart(2, '0')}`);
    }
    meta.textContent = parts.join(' | ');
    info.appendChild(title);
    info.appendChild(meta);

    const actionWrap = document.createElement('div');
    actionWrap.style.display = 'grid';
    actionWrap.style.gap = '6px';
    actionWrap.style.justifyItems = 'end';

    const act = document.createElement('button');
    act.className = 'row-action';
    act.textContent = '导入';
    act.addEventListener('click', async (e) => {
      e.stopPropagation();
      act.disabled = true; act.textContent = '提交中...';
      try {
        startTasksProgressLoop();
        const payload = {
          provider: item.provider,
          media_id: item.mediaId,
          anime_title: item.title,
          type: item.type,
          season: item.season,
          image_url: item.imageUrl,
          douban_id: item.douban_id,
          current_episode_index: item.currentEpisodeIndex,
        };
        const data = await apiFetch('/api/ui/import', { method: 'POST', body: JSON.stringify(payload) });
        alert(data.message || '已提交导入任务');
      } catch (err) {
        alert(`导入失败: ${err.message || err}`);
      } finally {
        act.disabled = false; act.textContent = '导入';
        stopTasksProgressLoop();
      }
    });

    // 复制标题按钮（便于改名/分享）
    const copyBtn = document.createElement('button');
    copyBtn.className = 'row-action';
    copyBtn.textContent = '复制题名';
    copyBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const text = `${item.title} ${item.type === 'tv_series' && item.season ? `(S${String(item.season).padStart(2, '0')})` : ''}`.trim();
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px'; document.body.appendChild(ta);
          ta.focus(); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
        }
      } catch {}
    });

    actionWrap.appendChild(act);
    actionWrap.appendChild(copyBtn);

    li.appendChild(poster);
    li.appendChild(info);
    li.appendChild(actionWrap);
    ul.appendChild(li);
  });
}

async function handleSearch(e) {
  e.preventDefault();
  const kw = document.getElementById('search-input').value.trim();
  if (!kw) return;
  saveRecentKeyword(kw);
  showResultsSkeleton(true);
  startSearchProgressLoop();
  try {
    const data = await apiFetch(`/api/ui/search/provider?keyword=${encodeURIComponent(kw)}`);
    renderResults(data.results || []);
  } catch (err) {
    alert(`搜索失败: ${err.message || err}`);
  } finally {
    showResultsSkeleton(false);
    completeSearchProgress();
  }
}

// 最近搜索
const RECENT_KEY = 'mobile_recent_keywords_v1';
function readRecentKeywords() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
}
function writeRecentKeywords(arr) { localStorage.setItem(RECENT_KEY, JSON.stringify(arr.slice(0, 8))); }
function saveRecentKeyword(kw) {
  const items = readRecentKeywords();
  const existedIdx = items.indexOf(kw);
  if (existedIdx !== -1) items.splice(existedIdx, 1);
  items.unshift(kw);
  writeRecentKeywords(items);
  renderRecent();
}
function deleteRecentKeyword(kw) {
  const items = readRecentKeywords();
  const index = items.indexOf(kw);
  if (index > -1) {
    items.splice(index, 1);
    writeRecentKeywords(items);
    renderRecent();
  }
}
function renderRecent() {
  let wrap = document.getElementById('recent-card');
  if (!wrap) {
    wrap = document.createElement('section');
    wrap.id = 'recent-card';
    wrap.className = 'card';
    const title = document.createElement('h2'); title.textContent = '最近搜索'; title.style.margin = '6px 0 10px'; title.style.fontSize = '16px';
    const list = document.createElement('div'); list.id = 'recent-list';
    wrap.appendChild(title); wrap.appendChild(list);
    document.querySelector('.content').insertBefore(wrap, document.getElementById('results-card'));
  }
  const list = document.getElementById('recent-list');
  list.innerHTML = '';
  readRecentKeywords().forEach(kw => {
    const chipWrapper = document.createElement('div');
    chipWrapper.className = 'chip-wrapper';

    const keywordBtn = document.createElement('button');
    keywordBtn.className = 'chip';
    keywordBtn.textContent = kw;
    keywordBtn.addEventListener('click', () => {
      document.getElementById('search-input').value = kw;
      document.getElementById('search-form').dispatchEvent(new Event('submit'));
    });

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'chip-delete';
    deleteBtn.innerHTML = '&times;';
    deleteBtn.title = `删除 "${kw}"`;
    deleteBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteRecentKeyword(kw);
    });

    chipWrapper.appendChild(keywordBtn);
    chipWrapper.appendChild(deleteBtn);
    list.appendChild(chipWrapper);
  });
}

// 简易弹幕库视图
async function loadLibrary() {
  const ul = document.getElementById('library-list');
  ul.innerHTML = '<li class="small">加载中...</li>';
  try {
    const data = await apiFetch('/api/ui/library');
    ul.innerHTML = '';
    const animes = data.animes || [];
    if (animes.length === 0) {
      ul.innerHTML = '<li class="small">库为空</li>';
      return;
    }
    animes.forEach(a => {
      const li = document.createElement('li');
      const left = createPosterImage(a.imageUrl, a.title);
      const info = document.createElement('div');
      info.className = 'info';
      const title = document.createElement('div'); title.className = 'title'; title.textContent = a.title;
      const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = `${typeToLabel(a.type)} · 季 ${a.season} · 源 ${a.sourceCount}`;
      info.appendChild(title); info.appendChild(meta);
      const actions = document.createElement('div');
      actions.style.display = 'grid'; actions.style.gap = '6px'; actions.style.justifyItems = 'end';
      const viewBtn = document.createElement('button'); viewBtn.className = 'row-action'; viewBtn.textContent = '源/集';
      viewBtn.addEventListener('click', () => showAnimeSources(a.animeId, a.title));
      const delBtn = document.createElement('button'); delBtn.className = 'row-action'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => {
        if (!confirm(`删除 ${a.title}？此为后台任务`)) return;
        await apiFetch(`/api/ui/library/anime/${a.animeId}`, { method: 'DELETE' });
        loadLibrary();
      });
      actions.appendChild(viewBtn); actions.appendChild(delBtn);
      li.appendChild(left); li.appendChild(info); li.appendChild(actions);
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`;
  }
}

async function showAnimeSources(animeId, title) {
  const ul = document.getElementById('library-list');
  ul.innerHTML = `<li class="small">${title} · 源列表加载中...</li>`;
  try {
    const sources = await apiFetch(`/api/ui/library/anime/${animeId}/sources`);
    ul.innerHTML = '';
    if (sources.length === 0) { ul.innerHTML = '<li class="small">无源</li>'; return; }
    sources.forEach(s => {
      const li = document.createElement('li');
      li.style.gridTemplateColumns = '1fr auto';
      li.innerHTML = `<div><div class="title">${s.provider_name}</div><div class="meta">${s.media_id}</div></div>`;
      const actions = document.createElement('div'); actions.style.display = 'grid'; actions.style.gap = '6px'; actions.style.justifyItems = 'end';
      const epBtn = document.createElement('button'); epBtn.className = 'row-action'; epBtn.textContent = '分集';
      epBtn.addEventListener('click', () => showEpisodes(s.source_id, title, animeId));
      const delBtn = document.createElement('button'); delBtn.className = 'row-action'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => { if (!confirm('删除该源？')) return; await apiFetch(`/api/ui/library/source/${s.source_id}`, { method: 'DELETE' }); showAnimeSources(animeId, title); });
      actions.appendChild(epBtn); actions.appendChild(delBtn); li.appendChild(actions); ul.appendChild(li);
    });
  } catch (e) { ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`; }
}

async function showEpisodes(sourceId, title, animeId) {
  const ul = document.getElementById('library-list');
  ul.innerHTML = `<li class="small">${title} · 分集加载中...</li>`;
  try {
    const eps = await apiFetch(`/api/ui/library/source/${sourceId}/episodes`);
    ul.innerHTML = '';
    if (eps.length === 0) { ul.innerHTML = '<li class="small">无分集</li>'; return; }
    eps.forEach(ep => {
      const li = document.createElement('li');
      li.style.gridTemplateColumns = '1fr auto';
      li.innerHTML = `<div><div class="title">${ep.title}</div><div class="meta">集 ${ep.episode_index} · 弹幕 ${ep.comment_count}</div></div>`;
      const actions = document.createElement('div'); actions.style.display = 'grid'; actions.style.gap = '6px'; actions.style.justifyItems = 'end';
      const refreshBtn = document.createElement('button'); refreshBtn.className = 'row-action'; refreshBtn.textContent = '刷新';
      refreshBtn.addEventListener('click', async () => { await apiFetch(`/api/ui/library/episode/${ep.id}/refresh`, { method: 'POST' }); alert('已触发刷新'); });
      const delBtn = document.createElement('button'); delBtn.className = 'row-action'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => { if (!confirm('删除该分集？')) return; await apiFetch(`/api/ui/library/episode/${ep.id}`, { method: 'DELETE' }); showEpisodes(sourceId, title, animeId); });
      actions.appendChild(refreshBtn); actions.appendChild(delBtn); li.appendChild(actions); ul.appendChild(li);
    });
  } catch (e) { ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`; }
}

// 简易任务视图
async function loadTasks() {
  const ul = document.getElementById('tasks-list');
  ul.innerHTML = '<li class="small">加载中...</li>';
  try {
    const tasks = await apiFetch('/api/ui/tasks');
    ul.innerHTML = '';
    if (!tasks || tasks.length === 0) {
      ul.innerHTML = '<li class="small">暂无任务</li>';
      return;
    }
    tasks.forEach(t => {
      const li = document.createElement('li');
      li.style.gridTemplateColumns = '1fr auto';
      li.innerHTML = `<div><div class="title">${t.title}</div><div class="meta">${t.status} · ${t.description || ''}</div></div>`;
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = `<li class="small">加载失败: ${e.message || e}</li>`;
  }
}

// Tab 切换
function switchTab(tab) {
  const searchCard = document.getElementById('search-card');
  const resultsCard = document.getElementById('results-card');
  const tasksCard = document.getElementById('tasks-card');
  const libraryCard = document.getElementById('library-card');
  const tokensCard = document.getElementById('tokens-card');
  const settingsCard = document.getElementById('settings-card');
  const recentCard = document.getElementById('recent-card');
  document.getElementById('tab-search').classList.remove('active');
  document.getElementById('tab-library').classList.remove('active');
  document.getElementById('tab-tasks').classList.remove('active');
  document.getElementById('tab-tokens').classList.remove('active');
  document.getElementById('tab-settings').classList.remove('active');
  moveNavIndicator(tab);
  if (tab === 'search') {
    [searchCard, resultsCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.remove('hidden');
    [tasksCard, libraryCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-search').classList.add('active');
    [searchCard, resultsCard].forEach(el => el.classList.add('anim-in'));
  } else if (tab === 'library') {
    [libraryCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, tasksCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-library').classList.add('active');
    loadLibrary();
    libraryCard.classList.add('anim-in');
  } else if (tab === 'tasks') {
    [tasksCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, libraryCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-tasks').classList.add('active');
    loadTasks();
    // 进入任务页时展示渐进式进度，导入提交或加载完成时结束
    startTasksProgressLoop();
    tasksCard.classList.add('anim-in');
  } else if (tab === 'tokens') {
    [tokensCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, libraryCard, tasksCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-tokens').classList.add('active');
    // 初始化 Token 配置：域名、UA 模式
    (async () => {
      try {
        const domain = await apiFetch('/api/ui/config/custom_api_domain');
        document.getElementById('token-custom-domain-input').value = domain.value || '';
      } catch {}
      try {
        const mode = await apiFetch('/api/ui/config/ua_filter_mode');
        document.getElementById('token-ua-filter-mode').value = mode.value || 'off';
      } catch {}
      loadTokens();
    })();
    tokensCard.classList.add('anim-in');
  } else if (tab === 'settings') {
    [settingsCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, libraryCard, tasksCard, tokensCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-settings').classList.add('active');
    initMobileSettingsOnce();
    settingsCard.classList.add('anim-in');
  }
}

// 导航滑块指示器
function moveNavIndicator(tab) {
  const order = { 'search': 0, 'library': 1, 'tasks': 2, 'tokens': 3, 'settings': 4 };
  const idx = order[tab] || 0;
  const indicator = document.getElementById('nav-indicator');
  if (!indicator) return;
  const nav = document.querySelector('.bottom-nav');
  const style = getComputedStyle(nav);
  const paddingLeft = parseFloat(style.paddingLeft) || 12;
  const paddingRight = parseFloat(style.paddingRight) || 12;
  const gap = 8;
  const items = Array.from(nav.querySelectorAll('.nav-btn'));
  const count = items.length || 5;
  const navWidth = nav.clientWidth - paddingLeft - paddingRight;
  const slotWidth = (navWidth - gap * (count - 1)) / count;
  // 调整指示器本身宽度，避免计算误差
  const indicatorEl = document.getElementById('nav-indicator');
  indicatorEl.style.width = `${slotWidth}px`;
  const x = paddingLeft + idx * (slotWidth + gap);
  indicator.style.transform = `translate(${x}px, -50%)`;
}

function getActiveTabKey() {
  const active = document.querySelector('.bottom-nav .nav-btn.active');
  if (!active || !active.id) return 'search';
  return active.id.replace('tab-', '');
}

// Init
document.getElementById('login-form').addEventListener('submit', handleLogin);
document.getElementById('logout-btn').addEventListener('click', logout);
document.getElementById('search-form').addEventListener('submit', handleSearch);
// 兜底：点击搜索按钮可能未触发表单 submit（某些浏览器内核）
document.querySelector('#search-form .primary')?.addEventListener('click', (e) => {
  e.preventDefault();
  handleSearch(new Event('submit'));
});
// 输入防抖（预留联想）
let searchDebounceTimer = null;
let searchProgressTimer = null;
let tasksPollTimer = null; // 定时轮询任务进度
document.getElementById('search-input').addEventListener('input', () => {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    const _v = document.getElementById('search-input').value.trim();
    // TODO: 可接入联想 API
  }, 300);
});
document.getElementById('tab-search').addEventListener('click', () => switchTab('search'));
document.getElementById('tab-library').addEventListener('click', () => switchTab('library'));
document.getElementById('tab-tasks').addEventListener('click', () => switchTab('tasks'));
document.getElementById('tab-tokens').addEventListener('click', () => switchTab('tokens'));
document.getElementById('tab-settings').addEventListener('click', () => switchTab('settings'));
checkLogin();
renderRecent();
// 初始与尺寸变化时，确保滑块位置准确
moveNavIndicator('search');
window.addEventListener('resize', () => moveNavIndicator(getActiveTabKey()));

// --- Progress bars ---
function setSearchProgress(percentOrNull) {
  const el = document.getElementById('search-progress');
  if (!el) return;
  const bar = el.querySelector('.bar');
  const label = document.getElementById('search-progress-label');
  if (percentOrNull == null) {
    el.classList.add('indeterminate');
    el.classList.remove('hidden');
    bar.style.width = '';
    if (label) label.textContent = '';
  } else if (percentOrNull === 100) {
    el.classList.add('hidden');
    el.classList.remove('indeterminate');
    bar.style.width = '100%';
    if (label) label.textContent = '100%';
  } else {
    el.classList.remove('indeterminate');
    el.classList.remove('hidden');
    const v = Math.max(0, Math.min(100, percentOrNull));
    bar.style.width = `${v}%`;
    if (label) label.textContent = `${Math.round(v)}%`;
  }
}

function setTasksProgress(percentOrNull) {
  const el = document.getElementById('tasks-progress');
  if (!el) return;
  const bar = el.querySelector('.bar');
  const label = document.getElementById('tasks-progress-label');
  if (percentOrNull == null) {
    el.classList.add('indeterminate');
    el.classList.remove('hidden');
    bar.style.width = '';
    if (label) label.textContent = '';
  } else if (percentOrNull === 100) {
    el.classList.add('hidden');
    el.classList.remove('indeterminate');
    bar.style.width = '100%';
    if (label) label.textContent = '100%';
  } else {
    el.classList.remove('indeterminate');
    el.classList.remove('hidden');
    const v = Math.max(0, Math.min(100, percentOrNull));
    bar.style.width = `${v}%`;
    if (label) label.textContent = `${Math.round(v)}%`;
  }
}

function startSearchProgressLoop() {
  clearInterval(searchProgressTimer);
  let p = 10;
  setSearchProgress(p);
  searchProgressTimer = setInterval(() => {
    p = Math.min(90, p + 1);
    setSearchProgress(p);
    if (p >= 90) clearInterval(searchProgressTimer);
  }, 80);
}

function completeSearchProgress() {
  clearInterval(searchProgressTimer);
  setSearchProgress(100);
}

async function pollTasksProgressOnce() {
  try {
    const tasks = await apiFetch('/api/ui/tasks');
    // 查找运行中的导入类任务，聚合进度（取平均或最大值）
    const running = (tasks || []).filter(t => t.status === '运行中' || t.status === '排队中');
    if (running.length === 0) { setTasksProgress(100); return; }
    const numeric = running.map(t => Number(t.progress) || 0);
    const avg = Math.round(numeric.reduce((a,b)=>a+b,0) / numeric.length);
    setTasksProgress(avg);
  } catch (e) {
    // 失败不打断 UI
  }
}

function startTasksProgressLoop() {
  clearInterval(tasksPollTimer);
  // 立即拉一次，以便尽快显示真实进度
  pollTasksProgressOnce();
  tasksPollTimer = setInterval(pollTasksProgressOnce, 1500);
}

function stopTasksProgressLoop() {
  clearInterval(tasksPollTimer);
  tasksPollTimer = null;
  setTasksProgress(100);
}
// Settings 复刻（账户/Webhook/Bangumi/TMDB/豆瓣/TVDB）
let settingsInitialized = false;
function initMobileSettingsOnce() {
  if (settingsInitialized) return; settingsInitialized = true;
  const subTabs = [
    ['mset-tab-account', 'mset-account'],
    ['mset-tab-webhook', 'mset-webhook'],
    ['mset-tab-bangumi', 'mset-bangumi'],
    ['mset-tab-tmdb', 'mset-tmdb'],
    ['mset-tab-douban', 'mset-douban'],
    ['mset-tab-tvdb', 'mset-tvdb'],
  ];
  const showView = (id) => {
    subTabs.forEach(([tabId, viewId]) => {
      document.getElementById(tabId).classList.toggle('active', viewId === id);
      const view = document.getElementById(viewId);
      const isTarget = viewId === id;
      view.classList.toggle('hidden', !isTarget);
      if (isTarget) view.classList.add('anim-in');
    });
  };
  subTabs.forEach(([tabId, viewId]) => {
    document.getElementById(tabId).addEventListener('click', () => showView(viewId));
  });
  // 默认显示账户
  showView('mset-account');

  // 账户：修改密码
  document.getElementById('mset-save-password-btn').addEventListener('click', async () => {
    const oldp = document.getElementById('mset-old-password').value;
    const newp = document.getElementById('mset-new-password').value;
    const conf = document.getElementById('mset-confirm-password').value;
    const msg = document.getElementById('mset-password-msg');
    msg.textContent = '';
    if (newp.length < 8) { msg.textContent = '新密码至少8位'; return; }
    if (newp !== conf) { msg.textContent = '两次密码不一致'; return; }
    try {
      await apiFetch('/api/ui/auth/users/me/password', { method: 'PUT', body: JSON.stringify({ old_password: oldp, new_password: newp }) });
      msg.textContent = '已修改';
    } catch (e) { msg.textContent = `失败: ${e.message || e}`; }
  });

  // Webhook：加载
  (async () => {
    try {
      const { value: apiKey } = await apiFetch('/api/ui/config/webhook_api_key');
      document.getElementById('mset-webhook-api-key').value = apiKey || '未生成';
      const { value: domain } = await apiFetch('/api/ui/config/webhook_custom_domain');
      document.getElementById('mset-webhook-domain').value = domain || '';
      const services = await apiFetch('/api/ui/webhooks/available');
      const sel = document.getElementById('mset-webhook-service'); sel.innerHTML = '';
      services.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });
      updateWebhookUrlPreview();
    } catch {}
  })();
  function updateWebhookUrlPreview() {
    const apiKey = document.getElementById('mset-webhook-api-key').value || '';
    const domain = document.getElementById('mset-webhook-domain').value || '';
    const service = document.getElementById('mset-webhook-service').value || '';
    const base = domain || window.location.origin;
    document.getElementById('mset-webhook-url').value = service ? `${base}/api/webhook/${service}?api_key=${apiKey}` : '';
  }
  document.getElementById('mset-regenerate-webhook-key').addEventListener('click', async () => {
    const { value } = await apiFetch('/api/ui/config/webhook_api_key/regenerate', { method: 'POST' });
    document.getElementById('mset-webhook-api-key').value = value || '';
    updateWebhookUrlPreview();
    alert('已生成新 Key');
  });
  document.getElementById('mset-save-webhook-domain').addEventListener('click', async () => {
    const d = (document.getElementById('mset-webhook-domain').value || '').trim();
    const msg = document.getElementById('mset-webhook-domain-msg');
    msg.textContent = '';
    try { await apiFetch('/api/ui/config/webhook_custom_domain', { method: 'PUT', body: JSON.stringify({ value: d }) }); msg.textContent = '已保存'; }
    catch (e) { msg.textContent = `保存失败: ${e.message || e}`; }
    updateWebhookUrlPreview();
  });
  document.getElementById('mset-webhook-service').addEventListener('change', updateWebhookUrlPreview);
  document.getElementById('mset-copy-webhook-url').addEventListener('click', async () => { await safeCopy(document.getElementById('mset-webhook-url').value); alert('已复制'); });

  // Bangumi
  (async () => {
    try {
      const cfg = await apiFetch('/api/ui/config/bangumi');
      document.getElementById('mset-bgm-client-id').value = cfg.bangumi_client_id || '';
      document.getElementById('mset-bgm-client-secret').value = cfg.bangumi_client_secret || '';
    } catch {}
    try { updateBgmState(await apiFetch('/api/bgm/auth/state')); } catch { updateBgmState({ is_authenticated: false }); }
  })();
  function updateBgmState(state) {
    const wrap = document.getElementById('mset-bgm-state');
    const loginBtn = document.getElementById('mset-bgm-login');
    const logoutBtn = document.getElementById('mset-bgm-logout');
    const authed = !!state.is_authenticated;
    wrap.textContent = authed ? `已授权 ${state.nickname}（ID ${state.bangumi_user_id}）` : '未授权';
    logoutBtn.classList.toggle('hidden', !authed);
  }
  document.getElementById('mset-save-bgm').addEventListener('click', async () => {
    const payload = { bangumi_client_id: document.getElementById('mset-bgm-client-id').value.trim(), bangumi_client_secret: document.getElementById('mset-bgm-client-secret').value.trim() };
    await apiFetch('/api/ui/config/bangumi', { method: 'PUT', body: JSON.stringify(payload) });
    alert('已保存');
  });
  document.getElementById('mset-bgm-login').addEventListener('click', async () => {
    try { const { url } = await apiFetch('/api/bgm/auth/url'); window.open(url, '_blank'); } catch (e) { alert(e.message || e); }
  });
  document.getElementById('mset-bgm-logout').addEventListener('click', async () => { await apiFetch('/api/bgm/auth', { method: 'DELETE' }); updateBgmState({ is_authenticated: false }); });

  // TMDB
  (async () => {
    try {
      const cfg = await apiFetch('/api/ui/config/tmdb');
      document.getElementById('mset-tmdb-key').value = cfg.tmdb_api_key || '';
      document.getElementById('mset-tmdb-api-base').value = cfg.tmdb_api_base_url || '';
      document.getElementById('mset-tmdb-img-base').value = cfg.tmdb_image_base_url || '';
    } catch {}
  })();
  document.getElementById('mset-save-tmdb').addEventListener('click', async () => {
    const payload = { tmdb_api_key: document.getElementById('mset-tmdb-key').value.trim(), tmdb_api_base_url: document.getElementById('mset-tmdb-api-base').value.trim(), tmdb_image_base_url: document.getElementById('mset-tmdb-img-base').value.trim() };
    await apiFetch('/api/ui/config/tmdb', { method: 'PUT', body: JSON.stringify(payload) });
    document.getElementById('mset-tmdb-msg').textContent = '已保存';
  });

  // Douban
  (async () => {
    try { const data = await apiFetch('/api/ui/config/douban_cookie'); document.getElementById('mset-douban-cookie').value = data.value || ''; } catch {}
  })();
  document.getElementById('mset-save-douban').addEventListener('click', async () => {
    const value = document.getElementById('mset-douban-cookie').value.trim();
    await apiFetch('/api/ui/config/douban_cookie', { method: 'PUT', body: JSON.stringify({ value }) });
    document.getElementById('mset-douban-msg').textContent = '已保存';
  });

  // TVDB
  (async () => { try { const data = await apiFetch('/api/ui/config/tvdb_api_key'); document.getElementById('mset-tvdb-key').value = data.value || ''; } catch {} })();
  document.getElementById('mset-save-tvdb').addEventListener('click', async () => {
    const value = document.getElementById('mset-tvdb-key').value.trim();
    await apiFetch('/api/ui/config/tvdb_api_key', { method: 'PUT', body: JSON.stringify({ value }) });
    document.getElementById('mset-tvdb-msg').textContent = '已保存';
  });
}

// Token 完整管理（对齐桌面端主要能力）
async function loadTokens() {
  const ul = document.getElementById('token-list');
  ul.innerHTML = '<li class="small">加载中...</li>';
  try {
    const tokens = await apiFetch('/api/ui/tokens');
    ul.innerHTML = '';
    if (!tokens || tokens.length === 0) { ul.innerHTML = '<li class="small">暂无 Token</li>'; return; }
    tokens.forEach(t => {
      const li = document.createElement('li');
      li.classList.add('token-list-item');

      // Column 1: Name
      const nameCell = document.createElement('div');
      nameCell.className = 'info';
      nameCell.innerHTML = `<div class="title">${t.name}</div>`;

      // Column 2: Status
      const statusCell = document.createElement('div');
      statusCell.className = 'status-cell';
      statusCell.innerHTML = `<span class="status-icon ${t.is_enabled ? 'enabled' : 'disabled'}">${t.is_enabled ? '✅' : '❌'}</span>`;

      // Column 3: Time
      const timeCell = document.createElement('div');
      timeCell.className = 'time-cell';
      const createdDate = new Date(t.created_at);
      const expiresDate = t.expires_at ? new Date(t.expires_at) : null;
      const createdDateStr = createdDate.toLocaleDateString();
      const createdTimeStr = createdDate.toLocaleTimeString();
      let expiresDateStr = '永久';
      let expiresTimeStr = '&nbsp;'; // 使用一个空格来保持对齐
      if (expiresDate) {
          expiresDateStr = expiresDate.toLocaleDateString();
          expiresTimeStr = expiresDate.toLocaleTimeString();
      }
      timeCell.innerHTML = `
          <div class="time-row created-time">
            <div class="time-label-split"><span>创建</span><span>时间</span></div>
            <div class="time-value-split"><span>${createdDateStr}</span><span>${createdTimeStr}</span></div>
          </div>
          <div class="time-row expires-time">
            <div class="time-label-split"><span>过期</span><span>时间</span></div>
            <div class="time-value-split"><span>${expiresDateStr}</span><span>${expiresTimeStr}</span></div>
          </div>
      `;

      // Column 4: Actions
      const actionsCell = document.createElement('div');
      actionsCell.className = 'actions-cell';

      const copyBtn = document.createElement('button'); copyBtn.className = 'row-action'; copyBtn.textContent = '复制链接';
      copyBtn.addEventListener('click', async () => {
        const domain = (document.getElementById('token-custom-domain-input').value || '').trim();
        const url = domain ? `${domain.replace(/\/$/, '')}/api/${t.token}` : t.token;
        await safeCopy(url);
        alert('已复制');
      });
      const logBtn = document.createElement('button'); logBtn.className = 'row-action'; logBtn.textContent = '访问日志';
      logBtn.addEventListener('click', () => showTokenLog(t.id, t.name));
      const toggleBtn = document.createElement('button'); toggleBtn.className = 'row-action'; toggleBtn.textContent = t.is_enabled ? '禁用' : '启用';
      toggleBtn.addEventListener('click', async () => { await apiFetch(`/api/ui/tokens/${t.id}/toggle`, { method: 'PUT' }); loadTokens(); });
      const delBtn = document.createElement('button'); delBtn.className = 'row-action'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => { if (!confirm('删除该 Token？')) return; await apiFetch(`/api/ui/tokens/${t.id}`, { method: 'DELETE' }); loadTokens(); });
      actionsCell.appendChild(copyBtn); actionsCell.appendChild(logBtn); actionsCell.appendChild(toggleBtn); actionsCell.appendChild(delBtn);

      li.appendChild(nameCell);
      li.appendChild(statusCell);
      li.appendChild(timeCell);
      li.appendChild(actionsCell);
      ul.appendChild(li);
    });
  } catch (e) { ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`; }
}

document.getElementById('token-add-btn')?.addEventListener('click', async () => {
  const name = (document.getElementById('token-new-name').value || '').trim();
  const validity = document.getElementById('token-validity')?.value || 'permanent';
  if (!name) return;
  await apiFetch('/api/ui/tokens', { method: 'POST', body: JSON.stringify({ name, validity_period: validity }) });
  document.getElementById('token-new-name').value = '';
  loadTokens();
});

// Token: 自定义域名 & UA 模式 & 名单 & 日志
document.getElementById('token-save-domain-btn')?.addEventListener('click', async () => {
  const domain = (document.getElementById('token-custom-domain-input').value || '').trim().replace(/\/$/, '');
  const msg = document.getElementById('token-domain-save-msg');
  msg.textContent = '';
  try {
    await apiFetch('/api/ui/config/custom_api_domain', { method: 'PUT', body: JSON.stringify({ value: domain }) });
    msg.textContent = '已保存';
  } catch (e) { msg.textContent = `保存失败: ${e.message || e}`; }
});

document.getElementById('token-manage-ua-list-btn')?.addEventListener('click', () => {
  switchCard('tokens-ua-card');
  loadUaRules();
});

document.getElementById('token-ua-back-btn')?.addEventListener('click', () => switchCard('tokens-card'));

document.getElementById('token-save-ua-mode-btn')?.addEventListener('click', async () => {
  const mode = document.getElementById('token-ua-filter-mode').value;
  const msg = document.getElementById('token-ua-mode-save-msg');
  msg.textContent = '';
  try { await apiFetch('/api/ui/config/ua_filter_mode', { method: 'PUT', body: JSON.stringify({ value: mode }) }); msg.textContent = '已保存'; }
  catch (e) { msg.textContent = `保存失败: ${e.message || e}`; }
});

async function loadUaRules() {
  const ul = document.getElementById('token-ua-list');
  ul.innerHTML = '<li class="small">加载中...</li>';
  try {
    const rules = await apiFetch('/api/ui/ua-rules');
    ul.innerHTML = '';
    if (!rules || rules.length === 0) { ul.innerHTML = '<li class="small">名单为空</li>'; return; }
    rules.forEach(r => {
      const li = document.createElement('li');
      const dateHtml = formatDateForMobile(r.created_at);
      li.innerHTML = `<div><div class="title">${r.ua_string}</div></div>${dateHtml}`;
      const del = document.createElement('button'); del.className = 'row-action'; del.textContent = '删除';
      del.addEventListener('click', async () => { await apiFetch(`/api/ui/ua-rules/${r.id}`, { method: 'DELETE' }); loadUaRules(); });
      const actions = document.createElement('div'); actions.style.display = 'grid'; actions.style.justifyItems = 'end'; actions.appendChild(del); // This seems redundant, but keeping for consistency if other actions are added.
      li.appendChild(actions); ul.appendChild(li);
    });
  } catch (e) { ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`; }
}

document.getElementById('token-ua-add-btn')?.addEventListener('click', async () => {
  const v = (document.getElementById('token-ua-new').value || '').trim();
  if (!v) return;
  await apiFetch('/api/ui/ua-rules', { method: 'POST', body: JSON.stringify({ ua_string: v }) });
  document.getElementById('token-ua-new').value = '';
  loadUaRules();
});

function showTokenLog(tokenId, name) {
  switchCard('tokens-log-card');
  document.getElementById('token-log-title').textContent = `Token 访问日志: ${name}`;
  loadTokenLog(tokenId);
}

document.getElementById('token-log-back-btn')?.addEventListener('click', () => switchCard('tokens-card'));

async function loadTokenLog(tokenId) {
  const ul = document.getElementById('token-log-list');
  ul.innerHTML = '<li class="small">加载中...</li>';
  try {
    const logs = await apiFetch(`/api/ui/tokens/${tokenId}/logs`);
    ul.innerHTML = '';
    if (!logs || logs.length === 0) { ul.innerHTML = '<li class="small">暂无记录</li>'; return; }
    logs.forEach(l => {
      const li = document.createElement('li');
      const dateHtml = formatDateForMobile(l.access_time);
      li.innerHTML = `<div class="info"><div class="title">${l.ip_address} · ${l.status}</div><div class="meta">${l.user_agent || 'No User-Agent'}</div></div>${dateHtml}`;
       ul.appendChild(li);
    });
  } catch (e) { ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`; }
}

function switchCard(cardId) {
  ['tokens-card', 'tokens-ua-card', 'tokens-log-card'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('hidden', id !== cardId);
  });
}

async function safeCopy(text) {
  if (navigator.clipboard && window.isSecureContext) { try { await navigator.clipboard.writeText(text); return; } catch {} }
  const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px'; document.body.appendChild(ta); ta.focus(); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
}

// --- Helpers ---
function normalizeImageUrl(url) {
  if (!url) return null;
  if (url.startsWith('//')) return 'https:' + url;
  return url;
}

function createPosterImage(src, altText) {
  const img = document.createElement('img');
  img.className = 'poster';
  const normalized = normalizeImageUrl(src);
  img.src = normalized || '/static/placeholder.png';
  img.alt = altText || '';
  img.referrerPolicy = 'strict-origin-when-cross-origin';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.crossOrigin = 'anonymous';
  img.onerror = () => { if (img.src !== window.location.origin + '/static/placeholder.png' && !img.src.endsWith('/static/placeholder.png')) { img.onerror = null; img.src = '/static/placeholder.png'; } };
  return img;
}
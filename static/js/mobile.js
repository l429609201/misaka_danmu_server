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
    empty.classList.add('anim-bounce');
    return;
  }
  empty.classList.add('hidden');
  items.forEach((item, index) => {
    const li = document.createElement('li');
    // 添加动画类和延迟
    li.style.setProperty('--item-index', index);
    li.classList.add('search-result-item');
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

    actionWrap.appendChild(act);

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
  
  // 启动搜索按钮环形进度条
  const searchBtn = document.querySelector('#search-form .primary');
  searchBtn.classList.add('searching');
  searchBtn.textContent = '搜索中...';
  
  // 启动真实搜索进度跟踪
  startSearchProgressAnimation();
  
  try {
    // 阶段4：开始搜索
    setSearchPhase('搜索中');
    
    const data = await apiFetch(`/api/ui/search/provider?keyword=${encodeURIComponent(kw)}`);
    
    // 阶段5：处理结果
    setSearchPhase('处理结果');
    renderResults(data.results || []);
    
    // 阶段6：完成
    setSearchPhase('完成');
    
  } catch (err) {
    alert(`搜索失败: ${err.message || err}`);
    // 错误时也要完成进度条
    setSearchPhase('完成');
  } finally {
    showResultsSkeleton(false);
    
    // 停止搜索按钮环形进度条
    stopSearchProgressAnimation();
    searchBtn.classList.remove('searching');
    searchBtn.textContent = '搜索';
  }
}

// 最近搜索
const RECENT_KEY = 'mobile_recent_keywords_v1';
function readRecentKeywords() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
}
function writeRecentKeywords(arr) { localStorage.setItem(RECENT_KEY, JSON.stringify(arr.slice(0, 6))); }
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

// 全局变量存储弹幕库数据
let libraryData = [];

// 简易弹幕库视图
async function loadLibrary(showLoading = true) {
  const ul = document.getElementById('library-list');
  const skeleton = document.getElementById('library-skeleton');
  
  if (showLoading) {
    ul.innerHTML = '';
    if (skeleton) {
      skeleton.classList.add('show'); // 显示骨架屏
    }
  }
  
  try {
    const data = await apiFetch('/api/ui/library');
    
    // 隐藏骨架屏
    if (skeleton) {
      skeleton.classList.remove('show');
    }
    
    // 存储数据用于筛选
    libraryData = data.animes || [];
    
    // 渲染内容
    renderLibrary(libraryData);
  } catch (e) {
    // 隐藏骨架屏
    if (skeleton) {
      skeleton.classList.remove('show');
    }
    ul.innerHTML = `<li class=\"small\">加载失败: ${e.message || e}</li>`;
  }
}

// 渲染弹幕库列表
function renderLibrary(animes) {
  const ul = document.getElementById('library-list');
  ul.innerHTML = '';
  
  if (animes.length === 0) {
    ul.innerHTML = '<li class="small">库为空</li>';
    return;
  }
  
  animes.forEach(a => {
      const li = document.createElement('li');
      li.className = 'library-item';
      
      // 上半部分：海报和信息
      const topSection = document.createElement('div');
      topSection.className = 'library-item-top';
      
      const left = createPosterImage(a.imageUrl, a.title);
      const info = document.createElement('div');
      info.className = 'info';
      const title = document.createElement('div'); title.className = 'title'; title.textContent = a.title;
      const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = `${typeToLabel(a.type)} · 季 ${a.season} · 源 ${a.sourceCount}`;
      info.appendChild(title); info.appendChild(meta);
      
      topSection.appendChild(left);
      topSection.appendChild(info);
      
      // 下半部分：按钮组
      const actions = document.createElement('div');
      actions.className = 'library-actions';
      
      const viewBtn = document.createElement('button'); viewBtn.className = 'library-btn'; viewBtn.textContent = '源/集';
      viewBtn.addEventListener('click', () => showAnimeSources(a.animeId, a.title));
      
      const refreshBtn = document.createElement('button'); refreshBtn.className = 'library-btn'; refreshBtn.textContent = '刷新';
      refreshBtn.addEventListener('click', async () => {
        if (!confirm(`刷新 ${a.title} 的所有弹幕？此操作将重新获取所有分集的弹幕`)) return;
        try {
          refreshBtn.disabled = true; refreshBtn.textContent = '刷新中...';
          // 获取动画的所有源，然后刷新每个源
          const sources = await apiFetch(`/api/ui/library/anime/${a.animeId}/sources`);
          for (const source of sources) {
            await apiFetch(`/api/ui/library/source/${source.source_id}/refresh`, { method: 'POST' });
          }
          alert(`${a.title} 的刷新任务已提交`);
          loadLibrary();
        } catch (error) {
          alert(`刷新失败: ${error.message || error}`);
        } finally {
          refreshBtn.disabled = false; refreshBtn.textContent = '刷新';
        }
      });
      
      const delBtn = document.createElement('button'); delBtn.className = 'library-btn library-btn-danger'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => {
        if (!confirm(`删除 ${a.title}？此为后台任务`)) return;
        await apiFetch(`/api/ui/library/anime/${a.animeId}`, { method: 'DELETE' });
        loadLibrary();
      });
      
      actions.appendChild(viewBtn);
      actions.appendChild(refreshBtn);
      actions.appendChild(delBtn);
      
      li.appendChild(topSection);
      li.appendChild(actions);
      ul.appendChild(li);
    });
}

// 弹幕库筛选功能
function filterLibrary(searchTerm) {
  if (!searchTerm.trim()) {
    renderLibrary(libraryData);
    return;
  }
  
  const filtered = libraryData.filter(anime => 
    anime.title.toLowerCase().includes(searchTerm.toLowerCase())
  );
  
  renderLibrary(filtered);
}

// 初始化弹幕库筛选
function initLibraryFilter() {
  const filterInput = document.getElementById('library-filter-input');
  if (filterInput) {
    filterInput.addEventListener('input', (e) => {
      filterLibrary(e.target.value);
    });
  }
}

// 下拉刷新功能已移除

// 弹幕库刷新按钮
function initLibraryRefreshButton() {
  const refreshBtn = document.getElementById('library-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      refreshBtn.style.transform = 'rotate(360deg)';
      refreshBtn.style.transition = 'transform 0.5s ease';
      await loadLibrary(true);
      setTimeout(() => {
        refreshBtn.style.transform = 'rotate(0deg)';
      }, 500);
    });
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

// 任务进度管理
let taskProgressData = {
  totalProgress: 0,
  stats: {
    running: 0,
    queued: 0,
    completed: 0,
    failed: 0
  }
};

// 更新任务进度条
function updateTaskProgress(progress, animated = false) {
  const progressRing = document.querySelector('.task-progress-ring .progress');
  const progressPercentage = document.querySelector('.task-progress-percentage');
  
  if (!progressRing || !progressPercentage) return;
  
  // 计算环形进度条偏移量
  const circumference = 2 * Math.PI * 52; // r = 52
  const offset = circumference - (circumference * progress / 100);
  
  // 更新进度环
  progressRing.style.strokeDashoffset = offset;
  
  // 更新百分比显示
  progressPercentage.textContent = `${Math.round(progress)}%`;
  
  // 添加动画效果
  if (animated && progress > 0 && progress < 100) {
    progressRing.classList.add('animated');
  } else {
    progressRing.classList.remove('animated');
  }
  
  // 完成时的特效
  if (progress >= 100) {
    progressRing.classList.remove('animated');
    progressRing.classList.add('completed');
    setTimeout(() => {
      progressRing.classList.remove('completed');
    }, 2000);
  }
  
  taskProgressData.totalProgress = progress;
}

// 更新任务统计
function updateTaskStats(stats) {
  const elements = {
    running: document.getElementById('task-stat-running'),
    queued: document.getElementById('task-stat-queued'),
    completed: document.getElementById('task-stat-completed'),
    failed: document.getElementById('task-stat-failed')
  };
  
  Object.keys(stats).forEach(key => {
    if (elements[key]) {
      const element = elements[key];
      const oldValue = parseInt(element.textContent) || 0;
      const newValue = stats[key] || 0;
      
      // 数字变化动画
      if (oldValue !== newValue) {
        element.style.transform = 'scale(1.2)';
        element.style.color = key === 'running' ? 'var(--primary)' : 
                              key === 'completed' ? 'var(--success)' : 
                              key === 'failed' ? 'var(--error)' : 
                              'var(--warning)';
        
        setTimeout(() => {
          element.textContent = newValue;
          element.style.transform = 'scale(1)';
        }, 150);
      }
    }
  });
  
  taskProgressData.stats = { ...stats };
}

// 计算总体进度
function calculateOverallProgress(tasks) {
  if (!tasks || tasks.length === 0) {
    return {
      totalProgress: 0,
      stats: { running: 0, queued: 0, completed: 0, failed: 0 }
    };
  }
  
  const stats = {
    running: 0,
    queued: 0,
    completed: 0,
    failed: 0
  };
  
  let totalProgress = 0;
  let totalTasks = 0;
  
  tasks.forEach(task => {
    const status = task.status;
    const progress = Number(task.progress) || 0;
    
    // 统计各状态任务数量
    if (status === '运行中') {
      stats.running++;
      totalProgress += progress;
      totalTasks++;
    } else if (status === '排队中') {
      stats.queued++;
      totalProgress += 0; // 排队中的任务进度为0
      totalTasks++;
    } else if (status === '已完成') {
      stats.completed++;
      totalProgress += 100;
      totalTasks++;
    } else if (status === '失败') {
      stats.failed++;
      totalProgress += 100; // 失败也算完成
      totalTasks++;
    }
  });
  
  // 计算总体进度：所有任务的平均进度
  const overallProgress = totalTasks > 0 ? totalProgress / totalTasks : 0;
  
  console.log(`📊 进度计算详情: 总任务${totalTasks}个, 总进度${totalProgress}, 平均进度${overallProgress.toFixed(1)}%`);
  
  return {
    totalProgress: Math.min(100, Math.max(0, overallProgress)),
    stats
  };
}

// 简易任务视图 - 带骨架屏的初始加载
async function loadTasks() {
  const skeleton = document.getElementById('tasks-skeleton');
  
  if (skeleton) {
    skeleton.classList.add('show');
  }
  
  try {
    const tasks = await apiFetch('/api/ui/tasks');
    
    // 隐藏骨架屏
    if (skeleton) {
      skeleton.classList.remove('show');
    }
    
    // 使用新的数据加载函数
    await loadTasksData(tasks);
    
  } catch (e) {
    // 隐藏骨架屏
    if (skeleton) {
      skeleton.classList.remove('show');
    }
    
    const ul = document.getElementById('tasks-list');
    ul.innerHTML = `<li class="small">加载失败: ${e.message || e}</li>`;
    
    // 错误时重置进度
    updateTaskProgress(0);
    updateTaskStats({ running: 0, queued: 0, completed: 0, failed: 0 });
  }
}

// 清理动画类
function clearAnimationClasses(element) {
  if (!element) return;
  const animClasses = ['anim-in', 'anim-slide-left', 'anim-slide-right', 'anim-zoom', 'anim-flip', 'anim-bounce'];
  animClasses.forEach(cls => element.classList.remove(cls));
  // 移除强制重绘以减少页面跳动
  // element.offsetHeight;
}

// Tab 切换
function switchTab(tab) {
  console.log(`SwitchTab called with: ${tab}`); // 调试日志
  
  // 获取所有卡片元素
  const searchCard = document.getElementById('search-card');
  const resultsCard = document.getElementById('results-card');
  const tasksCard = document.getElementById('tasks-card');
  const libraryCard = document.getElementById('library-card');
  const tokensCard = document.getElementById('tokens-card');
  const settingsCard = document.getElementById('settings-card');
  const recentCard = document.getElementById('recent-card');
  
  // 验证核心卡片元素是否存在（recentCard 可能尚未生成，不作为必需项）
  const cards = [searchCard, resultsCard, tasksCard, libraryCard, tokensCard, settingsCard];
  if (cards.some(card => !card)) {
    console.error('One or more required card elements not found');
    // 尽量不中断，而是继续按已有元素进行切换
  }
  
  // 清理所有卡片的动画类
  const allCards = [searchCard, resultsCard, tasksCard, libraryCard, tokensCard, settingsCard];
  if (recentCard) allCards.push(recentCard);
  allCards.forEach(clearAnimationClasses);
  
  // 移除所有导航按钮的active状态
  const navButtons = ['tab-search', 'tab-library', 'tab-tasks', 'tab-tokens', 'tab-settings'];
  navButtons.forEach(buttonId => {
    const button = document.getElementById(buttonId);
    if (button) {
      button.classList.remove('active');
    }
  });
  
  moveNavIndicator(tab);
  
  // 停止任务轮询（如果在其他页面）
  if (tab !== 'tasks') {
    stopTasksProgressLoop();
  }
  
  // 立即显示/隐藏卡片，然后延迟添加动画
  if (tab === 'search') {
    [searchCard, resultsCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.remove('hidden');
    [tasksCard, libraryCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-search').classList.add('active');
    
    // 延迟添加动画以确保DOM更新完成
    setTimeout(() => {
      [searchCard, resultsCard].forEach(el => el.classList.add('anim-slide-left'));
      if (recentCard) recentCard.classList.add('anim-slide-left');
    }, 10);
  } else if (tab === 'library') {
    [libraryCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, tasksCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-library').classList.add('active');
    
    // 清空筛选框
    const filterInput = document.getElementById('library-filter-input');
    if (filterInput) {
      filterInput.value = '';
    }
    
    loadLibrary();
  } else if (tab === 'tasks') {
    [tasksCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, libraryCard, tokensCard, settingsCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-tasks').classList.add('active');
    loadTasks();
    // 进入任务页时开始自动刷新任务列表
    startTasksProgressLoop();
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
    
    // 触摸设备上禁用 Token 页入场动画，避免全屏闪烁
    const isTouch = window.matchMedia && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
    const isDark = (document.documentElement.getAttribute('data-theme') === 'dark');
    if (!isTouch && !isDark) {
      setTimeout(() => {
        tokensCard.classList.add('anim-slide-left');
      }, 10);
    }
  } else if (tab === 'settings') {
    [settingsCard].forEach(el => el.classList.remove('hidden'));
    if (recentCard) recentCard.classList.add('hidden');
    [searchCard, resultsCard, libraryCard, tasksCard, tokensCard].forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-settings').classList.add('active');
    initMobileSettingsOnce();
    
    setTimeout(() => {
      settingsCard.classList.add('anim-slide-left');
    }, 10);
  }
}

// 导航滑块指示器 (新设计中不需要，但保留函数避免错误)
function moveNavIndicator(tab) {
  // 新设计不需要滑块指示器，直接返回
  return;
}

function getActiveTabKey() {
  const active = document.querySelector('.bottom-nav .nav-btn.active');
  if (!active || !active.id) return 'search';
  return active.id.replace('tab-', '');
}

// 主题管理
function getTheme() {
  return localStorage.getItem('theme') || 'default';
}

function setTheme(theme) {
  localStorage.setItem('theme', theme);
  document.documentElement.setAttribute('data-theme', theme);
  
  // 更新主界面的主题按钮
  const toggle = document.getElementById('theme-toggle');
  const loginToggle = document.getElementById('theme-toggle-login');
  
  const updateButton = (btn) => {
    if (!btn) return;
    if (theme === 'dark') {
      btn.textContent = '☀️';
      btn.title = '切换到亮色模式';
    } else if (theme === 'light') {
      btn.textContent = '🌙';
      btn.title = '切换到深色模式';
    } else {
      btn.textContent = '🎨';
      btn.title = '切换到深色模式';
    }
  };
  
  updateButton(toggle);
  updateButton(loginToggle);
  console.log('Theme set to:', theme); // 调试日志
}

function toggleTheme() {
  const current = getTheme();
  let next;
  if (current === 'default') next = 'dark';
  else if (current === 'dark') next = 'light';
  else next = 'default';
  console.log('Toggling theme from', current, 'to', next); // 调试日志
  setTheme(next);
}

// Init
document.getElementById('login-form').addEventListener('submit', handleLogin);
document.getElementById('logout-btn')?.addEventListener('click', logout);
document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);
document.getElementById('theme-toggle-login')?.addEventListener('click', toggleTheme);
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
let searchProgressAnimation = null; // 搜索进度动画
document.getElementById('search-input').addEventListener('input', () => {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    const _v = document.getElementById('search-input').value.trim();
    // TODO: 可接入联想 API
  }, 300);
});
// 导航栏事件监听器 - 使用更稳健的事件绑定
function initNavigationListeners() {
  const navButtons = [
    ['tab-search', 'search'],
    ['tab-library', 'library'],
    ['tab-tasks', 'tasks'],
    ['tab-tokens', 'tokens'],
    ['tab-settings', 'settings']
  ];

  navButtons.forEach(([buttonId, tabName]) => {
    const button = document.getElementById(buttonId);
    if (button) {
      // 移除可能存在的旧事件监听器
      button.removeEventListener('click', button._clickHandler);
      
      // 创建新的事件处理函数
      const clickHandler = (e) => {
        e.preventDefault();
        e.stopPropagation();
        console.log(`Switching to ${tabName}`); // 调试日志
        switchTab(tabName);
      };
      
      // 保存引用以便后续移除
      button._clickHandler = clickHandler;
      
      // 添加事件监听器
      button.addEventListener('click', clickHandler);
      
      // 添加触摸事件监听器以确保移动端兼容性
      button.addEventListener('touchend', (e) => {
        e.preventDefault();
        clickHandler(e);
      });
    }
  });
}

// 调用初始化函数
initNavigationListeners();
checkLogin();
renderRecent();
// 初始化主题
setTheme(getTheme());
  // 初始化弹幕库功能
  initLibraryRefreshButton();
  initLibraryFilter();
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
    
    // 更新任务进度条和任务列表 (如果在任务页面)
    const tasksCard = document.getElementById('tasks-card');
    if (tasksCard && !tasksCard.classList.contains('hidden')) {
      // 重新加载整个任务列表以获取最新数据
      await loadTasksData(tasks);
    }
    
    // 保持原有的逻辑兼容性
    const running = (tasks || []).filter(t => t.status === '运行中' || t.status === '排队中');
    if (running.length === 0) { setTasksProgress(100); return; }
    const numeric = running.map(t => Number(t.progress) || 0);
    const avg = Math.round(numeric.reduce((a,b)=>a+b,0) / numeric.length);
    setTasksProgress(avg);
  } catch (e) {
    // 失败不打断 UI
    console.log('轮询任务进度失败:', e);
  }
}

// 新增函数：用于处理任务数据更新，避免重复的骨架屏显示
async function loadTasksData(tasks = null) {
  const ul = document.getElementById('tasks-list');
  
  try {
    // 如果没有传入tasks数据，则重新获取
    if (!tasks) {
      tasks = await apiFetch('/api/ui/tasks');
    }
    
    // 计算并更新总体进度
    const progressData = calculateOverallProgress(tasks);
    updateTaskProgress(progressData.totalProgress, true);
    updateTaskStats(progressData.stats);
    
    ul.innerHTML = '';
    if (!tasks || tasks.length === 0) {
      ul.innerHTML = '<li class="small">暂无任务</li>';
      // 无任务时重置进度
      updateTaskProgress(0);
      updateTaskStats({ running: 0, queued: 0, completed: 0, failed: 0 });
      return;
    }
    
    tasks.forEach(t => {
      const li = document.createElement('li');
      li.className = 'task-item';
      li.setAttribute('data-status', t.status); // 添加状态属性用于CSS选择器
      li.style.gridTemplateColumns = '1fr';
      
      // 任务状态颜色映射
      const statusColors = {
        "已完成": "var(--success)",
        "失败": "var(--error)", 
        "排队中": "var(--warning)",
        "运行中": "var(--primary)"
      };
      
      const statusColor = statusColors[t.status] || "var(--primary)";
      const progress = Number(t.progress) || 0;
      
      li.innerHTML = `
        <div class="task-header">
          <div class="title">${t.title}</div>
          <div class="meta">${t.status} · ${progress}% · ${t.description || ''}</div>
        </div>
        <div class="task-progress-bar-container">
          <div class="task-progress-bar" style="width: ${progress}%; background-color: ${statusColor};"></div>
        </div>
      `;
      
      ul.appendChild(li);
    });
    
    console.log(`📊 任务数据更新: ${Math.round(progressData.totalProgress)}%`, progressData.stats);
    
  } catch (e) {
    ul.innerHTML = `<li class="small">加载失败: ${e.message || e}</li>`;
    
    // 错误时重置进度
    updateTaskProgress(0);
    updateTaskStats({ running: 0, queued: 0, completed: 0, failed: 0 });
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
// 环形进度条动画 - 与真实搜索进度关联
let searchProgressState = {
  current: 0,
  phases: [
    { name: '准备搜索', progress: 5, duration: 150 },
    { name: '连接服务器', progress: 15, duration: 200 },
    { name: '发送请求', progress: 25, duration: 100 },
    { name: '搜索中', progress: 85, duration: 0 }, // 这个阶段时间不定，等待服务器响应
    { name: '处理结果', progress: 95, duration: 150 },
    { name: '完成', progress: 100, duration: 50 }
  ],
  currentPhase: 0
};

function updateSearchProgress(targetProgress, smooth = true) {
  const searchBtn = document.querySelector('#search-form .primary');
  const progressRing = document.querySelector('.search-progress-ring');
  const progressPath = progressRing?.querySelector('.progress');
  
  if (!searchBtn || !progressPath) return;
  
  // 计算SVG进度条的偏移量
  const perimeter = 296; // 矩形周长
  const targetOffset = perimeter - (perimeter * targetProgress / 100);
  
  if (smooth) {
    // 平滑过渡到目标进度
    const startProgress = searchProgressState.current;
    const progressDiff = targetProgress - startProgress;
    const steps = 20;
    const stepSize = progressDiff / steps;
    const stepDuration = 50;
    
    let step = 0;
    const progressInterval = setInterval(() => {
      step++;
      const currentProgress = startProgress + (stepSize * step);
      
      if (step >= steps || currentProgress >= targetProgress) {
        searchProgressState.current = targetProgress;
        const offset = perimeter - (perimeter * targetProgress / 100);
        progressPath.style.strokeDashoffset = offset;
        clearInterval(progressInterval);
      } else {
        searchProgressState.current = currentProgress;
        const offset = perimeter - (perimeter * currentProgress / 100);
        progressPath.style.strokeDashoffset = offset;
      }
    }, stepDuration);
  } else {
    // 直接设置进度
    searchProgressState.current = targetProgress;
    progressPath.style.strokeDashoffset = targetOffset;
  }
}

function startSearchProgressAnimation() {
  const searchBtn = document.querySelector('#search-form .primary');
  const progressRing = document.querySelector('.search-progress-ring');
  const progressPath = progressRing?.querySelector('.progress');
  
  if (!searchBtn || !progressRing || !progressPath) return;
  
  // 显示进度环
  progressRing.classList.add('active');
  searchBtn.classList.add('searching');
  searchBtn.disabled = true;
  
  // 重置状态
  searchProgressState.current = 0;
  searchProgressState.currentPhase = 0;
  progressPath.style.strokeDashoffset = 296; // 重置为满偏移
  
  console.log('🔍 开始搜索进度跟踪');
  
  // 在控制台显示进度条 (仅调试用)
  if (window.location.search.includes('debug=1')) {
    window.searchProgressDebug = setInterval(() => {
      console.log(`📊 当前进度: ${Math.round(searchProgressState.current)}%`);
    }, 500);
  }
  
  // 自动推进前几个阶段
  function advanceToPhase(phaseIndex) {
    if (phaseIndex >= searchProgressState.phases.length) return;
    
    const phase = searchProgressState.phases[phaseIndex];
    console.log(`📍 进度阶段: ${phase.name} (${phase.progress}%)`);
    
    updateSearchProgress(phase.progress);
    searchProgressState.currentPhase = phaseIndex;
    
    if (phase.duration > 0 && phaseIndex < 3) { // 前3个阶段自动推进
      setTimeout(() => advanceToPhase(phaseIndex + 1), phase.duration);
    }
  }
  
  // 启动进度序列
  advanceToPhase(0);
}

function setSearchPhase(phaseName) {
  const phase = searchProgressState.phases.find(p => p.name === phaseName);
  if (phase) {
    const phaseIndex = searchProgressState.phases.indexOf(phase);
    searchProgressState.currentPhase = phaseIndex;
    updateSearchProgress(phase.progress);
    
    // 更新按钮文字显示当前阶段
    const searchBtn = document.querySelector('#search-form .primary');
    if (searchBtn && phaseName !== '完成') {
      searchBtn.textContent = `${phaseName}...`;
    }
    
    // 完成时添加特效
    if (phaseName === '完成') {
      const progressPath = document.querySelector('.search-progress-ring .progress');
      if (progressPath) {
        progressPath.classList.add('complete');
        setTimeout(() => {
          progressPath.classList.remove('complete');
        }, 1000);
      }
    }
    
    console.log(`🎯 设置搜索阶段: ${phaseName} (${phase.progress}%)`);
  }
}

function stopSearchProgressAnimation() {
  const searchBtn = document.querySelector('#search-form .primary');
  const progressRing = document.querySelector('.search-progress-ring');
  const progressPath = progressRing?.querySelector('.progress');
  
  // 完成进度条
  updateSearchProgress(100, true);
  
  // 添加完成动画效果
  if (searchBtn) {
    searchBtn.classList.add('completed');
    searchBtn.textContent = '搜索完成';
  }
  
  // 延迟重置，让用户看到完成状态
  setTimeout(() => {
    if (searchBtn) {
      searchBtn.classList.remove('completed');
      searchBtn.classList.remove('searching');
      searchBtn.disabled = false;
      searchBtn.textContent = '搜索';
      searchProgressState.current = 0;
      searchProgressState.currentPhase = 0;
    }
    
    if (progressRing && progressPath) {
      progressRing.classList.remove('active');
      // 重置进度条偏移
      setTimeout(() => {
        progressPath.style.strokeDashoffset = 296;
      }, 300); // 等待淡出动画完成后重置
    }
    
    // 清理调试定时器
    if (window.searchProgressDebug) {
      clearInterval(window.searchProgressDebug);
      window.searchProgressDebug = null;
    }
    
    console.log('✅ 搜索进度完成并重置');
  }, 800); // 增加延迟时间，让完成动画更明显
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
      const apiKeyInput = document.getElementById('mset-webhook-api-key');
      apiKeyInput.value = apiKey || '未生成';
      // 如果已有API Key，则显示输入框
      if (apiKey) apiKeyInput.classList.add('show');
      
      const { value: domain } = await apiFetch('/api/ui/config/webhook_custom_domain');
      document.getElementById('mset-webhook-domain').value = domain || '';
      
      // 如果已有域名，则显示 webhook URL 输入框
      const webhookUrlInput = document.getElementById('mset-webhook-url');
      if (domain) webhookUrlInput.classList.add('show');
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
    const apiKeyInput = document.getElementById('mset-webhook-api-key');
    apiKeyInput.value = value || '';
    apiKeyInput.classList.add('show'); // 显示只读输入框
    updateWebhookUrlPreview();
    alert('已生成新 Key');
  });
  document.getElementById('mset-save-webhook-domain').addEventListener('click', async () => {
    const d = (document.getElementById('mset-webhook-domain').value || '').trim();
    const msg = document.getElementById('mset-webhook-domain-msg');
    msg.textContent = '';
    try { 
      await apiFetch('/api/ui/config/webhook_custom_domain', { method: 'PUT', body: JSON.stringify({ value: d }) }); 
      msg.textContent = '已保存'; 
      // 保存后显示 webhook URL 输入框
      const webhookUrlInput = document.getElementById('mset-webhook-url');
      webhookUrlInput.classList.add('show');
    }
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
    tokens.forEach((t, index) => {
      const li = document.createElement('li');
      li.classList.add('token-list-item');
      li.style.setProperty('--item-index', index);

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

      // 上半部分：信息展示
      const infoSection = document.createElement('div');
      infoSection.className = 'token-info-section';
      infoSection.appendChild(nameCell);
      infoSection.appendChild(statusCell);
      infoSection.appendChild(timeCell);

      // 下半部分：按钮组（两排）
      const actionsSection = document.createElement('div');
      actionsSection.className = 'token-actions-section';
      
      const copyBtn = document.createElement('button'); copyBtn.className = 'token-btn'; copyBtn.textContent = '复制链接';
      copyBtn.addEventListener('click', async () => {
        const domain = (document.getElementById('token-custom-domain-input').value || '').trim();
        const url = domain ? `${domain.replace(/\/$/, '')}/api/${t.token}` : t.token;
        await safeCopy(url);
        alert('已复制');
      });
      
      const logBtn = document.createElement('button'); logBtn.className = 'token-btn'; logBtn.textContent = '访问日志';
      logBtn.addEventListener('click', () => showTokenLog(t.id, t.name));
      
      const toggleBtn = document.createElement('button'); toggleBtn.className = 'token-btn'; toggleBtn.textContent = t.is_enabled ? '禁用' : '启用';
      toggleBtn.addEventListener('click', async () => { await apiFetch(`/api/ui/tokens/${t.id}/toggle`, { method: 'PUT' }); loadTokens(); });
      
      const delBtn = document.createElement('button'); delBtn.className = 'token-btn token-btn-danger'; delBtn.textContent = '删除';
      delBtn.addEventListener('click', async () => { if (!confirm('删除该 Token？')) return; await apiFetch(`/api/ui/tokens/${t.id}`, { method: 'DELETE' }); loadTokens(); });
      
      actionsSection.appendChild(copyBtn);
      actionsSection.appendChild(logBtn);
      actionsSection.appendChild(toggleBtn);
      actionsSection.appendChild(delBtn);

      li.appendChild(infoSection);
      li.appendChild(actionsSection);
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

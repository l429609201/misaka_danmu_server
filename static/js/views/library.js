import { apiFetch } from '../api.js';
import { switchView } from '../ui.js';

// DOM Elements
let libraryTableBody, librarySearchInput;
let animeDetailView, detailViewImg, detailViewTitle, detailViewMeta, sourceDetailTableBody;
let episodeListView, danmakuListView;

// State
let currentEpisodes = [];
let episodeSortOrder = 'asc'; // 'asc' or 'desc'
let currentModalConfirmHandler = null; // 仅用于本模块控制通用模态的“确认”按钮

function initializeElements() {
    libraryTableBody = document.querySelector('#library-table tbody');
    librarySearchInput = document.getElementById('library-search-input');
    
    animeDetailView = document.getElementById('anime-detail-view');
    detailViewImg = document.getElementById('detail-view-img');
    detailViewTitle = document.getElementById('detail-view-title');
    detailViewMeta = document.getElementById('detail-view-meta');
    sourceDetailTableBody = document.getElementById('source-detail-table-body');

    episodeListView = document.getElementById('episode-list-view');
    danmakuListView = document.getElementById('danmaku-list-view');
}

async function loadLibrary() {
    if (!libraryTableBody) return;
    libraryTableBody.innerHTML = '<tr><td colspan="8">加载中...</td></tr>';
    try {
        const data = await apiFetch('/api/ui/library');
        renderLibrary(data.animes);
    } catch (error) {
        libraryTableBody.innerHTML = `<tr><td colspan="8" class="error">加载失败: ${(error.message || error)}</td></tr>`;
    }
}

function renderLibrary(animes) {
    libraryTableBody.innerHTML = '';
    if (animes.length === 0) {
        libraryTableBody.innerHTML = '<tr><td colspan="8">媒体库为空。</td></tr>';
        return;
    }

    animes.forEach(anime => {
        const row = libraryTableBody.insertRow();
        row.dataset.title = anime.title.toLowerCase();

        // 优先使用本地缓存路径，否则回退到原始URL，最后使用占位图
        const imageUrl = anime.local_image_path || anime.imageUrl || '/static/placeholder.png';

        row.innerHTML = `
            <td class="poster-cell"><img src="${imageUrl}" referrerpolicy="no-referrer" alt="${anime.title}"></td>
            <td>${anime.title}</td>
            <td>${{ 'tv_series': '电视节目', 'movie': '电影/剧场版', 'ova': 'OVA', 'other': '其他' }[anime.type] || anime.type}</td>
            <td>${anime.season}</td>
            <td>${anime.episodeCount}</td>
            <td>${anime.sourceCount}</td>
            <td>${new Date(anime.createdAt).toLocaleString()}</td>
            <td class="actions-cell">
                <div class="action-buttons-wrapper">
                    <button class="action-btn" data-action="edit" data-anime-id="${anime.animeId}" title="编辑">✏️</button>
                    <button class="action-btn" data-action="view" data-anime-id="${anime.animeId}" title="查看数据源">📖</button>
                    <button class="action-btn" data-action="delete" data-anime-id="${anime.animeId}" data-anime-title="${anime.title}" title="删除">🗑️</button>
                </div>
            </td>
        `;
    });
}

function handleLibrarySearch() {
    const searchTerm = librarySearchInput.value.toLowerCase();
    const rows = libraryTableBody.querySelectorAll('tr');
    rows.forEach(row => {
        const title = row.dataset.title || '';
        row.style.display = title.includes(searchTerm) ? '' : 'none';
    });
}

async function handleLibraryAction(e) {
    const button = e.target.closest('.action-btn');
    if (!button) return;

    const action = button.dataset.action;
    const animeId = parseInt(button.dataset.animeId, 10);
    const title = button.dataset.animeTitle;

    if (action === 'delete') {
        if (confirm(`您确定要删除作品 '${title}' 吗？\n此操作将在后台提交一个删除任务。`)) {
            try {
                const response = await apiFetch(`/api/ui/library/anime/${animeId}`, { method: 'DELETE' });
                if (confirm((response.message || "删除任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
                    document.querySelector('.nav-link[data-view="task-manager-view"]').click();
                } else {
                    loadLibrary(); // Refresh the library view
                }
            } catch (error) {
                alert(`提交删除任务失败: ${(error.message || error)}`);
            }
        }
    } else if (action === 'edit') {
        document.dispatchEvent(new CustomEvent('show:edit-anime', { detail: { animeId } }));
    } else if (action === 'view') {
        showAnimeDetailView(animeId);
    }
}

function updateSelectAllButtonState() {
    const selectAllBtn = document.getElementById('select-all-sources-btn');
    if (!selectAllBtn) return;

    const allCheckboxes = sourceDetailTableBody.querySelectorAll('.source-checkbox');
    if (allCheckboxes.length === 0) {
        selectAllBtn.textContent = '全选';
        selectAllBtn.disabled = true;
        return;
    }
    const allChecked = Array.from(allCheckboxes).every(cb => cb.checked);
    selectAllBtn.textContent = allChecked ? '取消全选' : '全选';
    selectAllBtn.disabled = false;
}

async function showAnimeDetailView(animeId) {
    switchView('anime-detail-view');
    detailViewTitle.textContent = '加载中...';
    detailViewMeta.textContent = '';
    detailViewImg.src = '/static/placeholder.png';
    sourceDetailTableBody.innerHTML = '';

    try {
        const [fullLibrary, sources] = await Promise.all([
            apiFetch('/api/ui/library'),
            apiFetch(`/api/ui/library/anime/${animeId}/sources`)
        ]);

        const anime = fullLibrary.animes.find(a => a.animeId === animeId);
        if (!anime) throw new Error("找不到该作品的信息。");

        // 同样，在详情页也优先使用本地图片
        detailViewImg.src = anime.local_image_path || anime.imageUrl || '/static/placeholder.png';
        detailViewImg.alt = anime.title;
        detailViewTitle.textContent = anime.title;
        detailViewMeta.textContent = `季: ${anime.season} | 总集数: ${anime.episodeCount || 0} | 已关联 ${sources.length} 个源`;
        
        animeDetailView.dataset.animeId = anime.animeId; // Store for back button

        renderSourceDetailTable(sources, anime);
        updateSelectAllButtonState(); // Initial state update
    } catch (error) {
        detailViewTitle.textContent = '加载详情失败';
        detailViewMeta.textContent = error.message || error;
    }
}

function renderSourceDetailTable(sources, anime) {
    sourceDetailTableBody.innerHTML = '';
    if (sources.length > 0) {
        sources.forEach(source => {
            const row = sourceDetailTableBody.insertRow();
            row.style.cursor = 'pointer';
            row.addEventListener('click', (e) => {
                if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') {
                    const checkbox = row.querySelector('.source-checkbox');
                    if (checkbox) checkbox.click();
                }
            });
            const statusIcons = [];
            if (source.is_favorited) {
                statusIcons.push('<span title="精确标记">🌟</span>');
            }
            if (source.incremental_refresh_enabled) {
                statusIcons.push('<span title="定时追更">⏰</span>');
            }
            row.innerHTML = `
                <td><input type="checkbox" class="source-checkbox" value="${source.source_id}"></td>
                <td>${source.provider_name}</td>
                <td>${source.media_id}</td>
                <td class="status-cell">${statusIcons.join(' ')}</td>
                <td>${new Date(source.created_at).toLocaleString()}</td>
                <td class="actions-cell">
                    <div class="action-buttons-wrapper" data-source-id="${source.source_id}" data-anime-title="${anime.title}" data-anime-id="${anime.animeId}">
                        <button class="action-btn" data-action="favorite" title="精确标记(用于自动匹配)">${source.is_favorited ? '🌟' : '⭐'}</button>
                        <button class="action-btn ${source.incremental_refresh_enabled ? '' : 'disabled-icon'}" data-action="toggle-incremental" title="定时增量更新">⏰</button>
                        <button class="action-btn" data-action="incremental-update" title="手动增量更新 (获取下一集)">⏭️</button>
                        <button class="action-btn" data-action="view_episodes" title="查看/编辑分集">📖</button>
                        <button class="action-btn" data-action="refresh" title="刷新此源">🔄</button>
                        <button class="action-btn" data-action="delete" title="删除此源">🗑️</button>
                    </div>
                </td>
            `;
        });
    } else {

        sourceDetailTableBody.innerHTML = `<tr><td colspan="6">未关联任何数据源。</td></tr>`;
    }
    // Add event listener for individual checkboxes to update the "Select All" button state
    sourceDetailTableBody.querySelectorAll('.source-checkbox').forEach(cb => {
        cb.addEventListener('change', updateSelectAllButtonState);
    });
}

async function handleSourceAction(e) {
    const button = e.target.closest('.action-btn');
    if (!button) return;
    
    const wrapper = button.parentElement;
    const action = button.dataset.action;
    const sourceId = parseInt(wrapper.dataset.sourceId, 10);
    const animeTitle = wrapper.dataset.animeTitle;
    const animeId = parseInt(wrapper.dataset.animeId, 10);

    switch (action) {
        case 'favorite':
            try {
                await apiFetch(`/api/ui/library/source/${sourceId}/favorite`, { method: 'PUT' });
                showAnimeDetailView(animeId);
            } catch (error) {
                alert(`操作失败: ${error.message}`);
            }
            break;
        case 'toggle-incremental':
            try {
                await apiFetch(`/api/ui/library/source/${sourceId}/toggle-incremental-refresh`, { method: 'PUT' });
                showAnimeDetailView(animeId); // Refresh the view to show the new status icon
            } catch (error) {
                alert(`操作失败: ${error.message}`);
            }
            break;
        case 'incremental-update':
            if (confirm(`您确定要为 '${animeTitle}' 的这个数据源执行增量更新吗？\n此操作将尝试获取下一集。`)) {
                apiFetch(`/api/ui/library/source/${sourceId}/incremental-refresh`, { method: 'POST' })
                    .then(response => {
                        if (confirm((response.message || "增量更新任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
                            document.querySelector('.nav-link[data-view="task-manager-view"]').click();
                        }
                    })
                    .catch(error => alert(`启动增量更新任务失败: ${error.message}`));
            }
            break;
        case 'view_episodes':
            showEpisodeListView(sourceId, animeTitle, animeId);
            break;
        case 'refresh':
            if (confirm(`您确定要为 '${animeTitle}' 的这个数据源执行全量刷新吗？`)) {
                apiFetch(`/api/ui/library/source/${sourceId}/refresh`, { method: 'POST' })
                    .then(response => alert(response.message || "刷新任务已开始。"))
                    .catch(error => alert(`启动刷新任务失败: ${error.message}`));
            }
            break;
        case 'delete':
            if (confirm(`您确定要删除这个数据源吗？\n此操作将在后台提交一个删除任务。`)) {
                try {
                    const response = await apiFetch(`/api/ui/library/source/${sourceId}`, { method: 'DELETE' });
                    if (confirm((response.message || "删除任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
                        document.querySelector('.nav-link[data-view="task-manager-view"]').click();
                    } else {
                        showAnimeDetailView(animeId);
                    }
                } catch (error) {
                    alert(`提交删除任务失败: ${error.message}`);
                }
            }
            break;
    }
}

function updateEpisodeSelectAllButtonState() {
    const selectAllBtn = document.getElementById('select-all-episodes-btn');
    if (!selectAllBtn) return;

    const allCheckboxes = episodeListView.querySelectorAll('.episode-checkbox');
    if (allCheckboxes.length === 0) {
        selectAllBtn.textContent = '全选';
        selectAllBtn.disabled = true;
        return;
    }
    const allChecked = Array.from(allCheckboxes).every(cb => cb.checked);
    selectAllBtn.textContent = allChecked ? '取消全选' : '全选';
    selectAllBtn.disabled = false;
}

async function showEpisodeListView(sourceId, animeTitle, animeId) {
    switchView('episode-list-view');
    episodeListView.innerHTML = '<div>加载中...</div>';

    try {
        const episodes = await apiFetch(`/api/ui/library/source/${sourceId}/episodes`);
        currentEpisodes = episodes; // Store the original, unsorted list
        renderEpisodeListView(sourceId, animeTitle, episodes, animeId); // Pass the unsorted list
    } catch (error) {
        episodeListView.innerHTML = `<div class="error">加载分集列表失败: ${(error.message || error)}</div>`;
    }
}

function renderEpisodeListView(sourceId, animeTitle, episodes, animeId) {
    // Sort episodes based on the current sort order
    const sortedEpisodes = [...episodes].sort((a, b) => {
        if (episodeSortOrder === 'desc') {
            return b.episode_index - a.episode_index;
        }
        return a.episode_index - b.episode_index;
    });

    episodeListView.innerHTML = `
        <div class="episode-list-header">
            <h3>分集列表: ${animeTitle}</h3>
            <button id="back-to-detail-view-btn">&lt; 返回作品详情</button>
        </div>
        <div class="episode-list-actions">
            <div class="actions-left">
                <button id="select-all-episodes-btn" class="secondary-btn">全选</button>
                <button id="delete-selected-episodes-btn" class="secondary-btn danger">批量删除选中</button>
                <div class="sort-switch-container">
                    <label for="episode-sort-switch">倒序显示</label>
                    <label class="switch">
                        <input type="checkbox" id="episode-sort-switch" ${episodeSortOrder === 'desc' ? 'checked' : ''}>
                        <span class="slider round"></span>
                    </label>
                </div>
            </div>
            <div class="actions-right">
                <button id="cleanup-by-average-btn" class="secondary-btn danger">正片重整</button>
                <button id="reorder-episodes-btn" class="secondary-btn">重整集数</button>
                <button id="manual-import-btn" class="secondary-btn">手动导入</button>
            </div>
        </div>
        <table id="episode-list-table">
            <thead><tr><th><input type="checkbox" class="hidden"></th><th>ID</th><th>剧集名</th><th>集数</th><th>弹幕数</th><th>采集时间</th><th>官方<br>链接</th><th>剧集操作</th></tr></thead>
            <tbody></tbody>
        </table>
    `;
    episodeListView.dataset.sourceId = sourceId;
    episodeListView.dataset.animeTitle = animeTitle;
    episodeListView.dataset.animeId = animeId;

    const tableBody = episodeListView.querySelector('tbody');
    if (sortedEpisodes.length > 0) {
        sortedEpisodes.forEach(ep => {
            const row = tableBody.insertRow();
            row.style.cursor = 'pointer';
            row.addEventListener('click', (e) => {
                if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A' && e.target.tagName !== 'INPUT') {
                    const checkbox = row.querySelector('.episode-checkbox');
                    if (checkbox) checkbox.click();
                }
            });
            row.innerHTML = `
                <td><input type="checkbox" class="episode-checkbox" value="${ep.id}"></td>
                <td>${ep.id}</td><td>${ep.title}</td><td>${ep.episode_index}</td><td>${ep.comment_count}</td>
                <td>${ep.fetched_at ? new Date(ep.fetched_at).toLocaleString() : 'N/A'}</td>
                <td>${ep.source_url ? `<a href="${ep.source_url}" target="_blank">跳转</a>` : '无'}</td>
                <td class="actions-cell">
                    <div class="action-buttons-wrapper" data-episode-id="${ep.id}" data-episode-title="${ep.title}">
                        <button class="action-btn" data-action="edit" title="编辑剧集">✏️</button>
                        <button class="action-btn" data-action="refresh" title="刷新剧集">🔄</button>
                        <button class="action-btn" data-action="view_danmaku" title="查看具体弹幕">💬</button>
                        <button class="action-btn" data-action="delete" title="删除集">🗑️</button>
                    </div>
                </td>
            `;

        });
    } else {
        tableBody.innerHTML = `<tr><td colspan="8">未找到任何分集数据。</td></tr>`;
    }

    updateEpisodeSelectAllButtonState();
    tableBody.querySelectorAll('.episode-checkbox').forEach(cb => {
        cb.addEventListener('change', updateEpisodeSelectAllButtonState);
    });
    document.getElementById('select-all-episodes-btn').addEventListener('click', handleSelectAllEpisodes);
    document.getElementById('delete-selected-episodes-btn').addEventListener('click', handleDeleteSelectedEpisodes);
    document.getElementById('cleanup-by-average-btn').addEventListener('click', () => handleCleanupByAverage(sourceId, animeTitle));
    document.getElementById('reorder-episodes-btn').addEventListener('click', () => handleReorderEpisodes(sourceId, animeTitle));
    document.getElementById('manual-import-btn').addEventListener('click', () => showManualImportModal(sourceId));
    document.getElementById('back-to-detail-view-btn').addEventListener('click', () => showAnimeDetailView(animeId));
    tableBody.addEventListener('click', handleEpisodeAction);

    // Add event listener for the new sort switch
    document.getElementById('episode-sort-switch').addEventListener('change', (e) => {
        episodeSortOrder = e.target.checked ? 'desc' : 'asc';
        // Re-render with the new sort order. We use `currentEpisodes` which is the original unsorted list.
        renderEpisodeListView(sourceId, animeTitle, currentEpisodes, animeId);
    });
}

function handleSelectAllEpisodes() {
    const allCheckboxes = episodeListView.querySelectorAll('.episode-checkbox');
    const shouldSelectAll = Array.from(allCheckboxes).some(cb => !cb.checked);
    allCheckboxes.forEach(cb => {
        cb.checked = shouldSelectAll;
    });
    updateEpisodeSelectAllButtonState();
}

async function handleDeleteSelectedEpisodes() {
    const selectedCheckboxes = episodeListView.querySelectorAll('.episode-checkbox:checked');
    if (selectedCheckboxes.length === 0) {
        alert('请先选择要删除的分集。');
        return;
    }
    if (!confirm(`您确定要删除选中的 ${selectedCheckboxes.length} 个分集吗？\n此操作将在后台提交一个批量删除任务。`)) return;

    const episodeIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value, 10));
    try {
        await apiFetch('/api/ui/library/episodes/delete-bulk', { method: 'POST', body: JSON.stringify({ episode_ids: episodeIds }) });
        alert('批量删除任务已提交。');
        document.querySelector('.nav-link[data-view="task-manager-view"]').click();
    } catch (error) { alert(`提交批量删除任务失败: ${error.message}`); }
}

async function handleReorderEpisodes(sourceId, animeTitle) {
    if (!confirm(`您确定要为 '${animeTitle}' 的这个数据源重整集数吗？\n\n此操作会按当前顺序将集数重新编号为 1, 2, 3...`)) {
        return;
    }

    try {
        const response = await apiFetch(`/api/ui/library/source/${sourceId}/reorder-episodes`, { method: 'POST' });
        if (confirm((response.message || "重整任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
            document.querySelector('.nav-link[data-view="task-manager-view"]').click();
        }
    } catch (error) {
        alert(`提交重整任务失败: ${error.message}`);
    }
}

// 计算平均弹幕数并删除低于平均值的分集（带预览确认）
async function handleCleanupByAverage(sourceId, animeTitle) {
    const episodes = currentEpisodes || [];
    if (!episodes.length) {
        alert('没有可用的分集数据。');
        return;
    }

    const validCounts = episodes
        .map(ep => Number(ep.comment_count))
        .filter(n => Number.isFinite(n) && n >= 0);
    if (validCounts.length === 0) {
        alert('所有分集的弹幕数不可用。');
        return;
    }

    const average = validCounts.reduce((a, b) => a + b, 0) / validCounts.length;
    const toDelete = episodes.filter(ep => Number(ep.comment_count) < average);
    const toKeep = episodes.filter(ep => Number(ep.comment_count) >= average);

    if (toDelete.length === 0) {
        alert(`未找到低于平均值 (${average.toFixed(2)}) 的分集。`);
        return;
    }

    // 在通用模态中展示“将保留的分集”预览
    const modal = document.getElementById('generic-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const modalSaveBtn = document.getElementById('modal-save-btn');
    const modalCancelBtn = document.getElementById('modal-cancel-btn');
    const modalCloseBtn = document.getElementById('modal-close-btn');

    modalTitle.textContent = `正片重整预览 - ${animeTitle}`;
    modalSaveBtn.textContent = '确认执行';

    const keepPreviewRows = toKeep
        .sort((a, b) => a.episode_index - b.episode_index)
        .slice(0, 80) // 控制渲染数量
        .map(ep => `<tr><td>${ep.episode_index}</td><td>${ep.title}</td><td>${ep.comment_count}</td></tr>`) // 预览保留的集
        .join('');

    const deleteCountText = `<span style="color: var(--error-color); font-weight: 600;">${toDelete.length}</span>`;
    const keepCountText = `<span style="color: var(--success-color); font-weight: 600;">${toKeep.length}</span>`;

    modalBody.innerHTML = `
        <p>将基于平均弹幕数进行正片重整：</p>
        <ul>
            <li>平均弹幕数：<strong>${average.toFixed(2)}</strong></li>
            <li>预计删除分集：${deleteCountText} / ${episodes.length}</li>
            <li>预计保留分集：${keepCountText} / ${episodes.length}</li>
        </ul>
        <div class="form-card">
            <h4 style="margin-top:0">预览将保留的分集（最多显示 80 条）</h4>
            <table class="compact-table">
                <thead><tr><th>集数</th><th>标题</th><th>弹幕数</th></tr></thead>
                <tbody>${keepPreviewRows || '<tr><td colspan="3">无</td></tr>'}</tbody>
            </table>
            <p class="small">确认后：先批量删除低于平均值的分集，然后自动重整集数。</p>
        </div>
    `;

    // 仅绑定一次我们的确认处理器，避免多次触发
    if (currentModalConfirmHandler) {
        modalSaveBtn.removeEventListener('click', currentModalConfirmHandler);
        currentModalConfirmHandler = null;
    }
    currentModalConfirmHandler = async (e) => {
        e.preventDefault();
        try {
            // 1) 提交批量删除
            const episodeIds = toDelete.map(ep => ep.id);
            await apiFetch('/api/ui/library/episodes/delete-bulk', {
                method: 'POST',
                body: JSON.stringify({ episode_ids: episodeIds })
            });
            // 2) 紧接着提交重整集数（队列中会按顺序执行）
            await apiFetch(`/api/ui/library/source/${sourceId}/reorder-episodes`, { method: 'POST' });

            modal.classList.add('hidden');
            alert('已提交：批量删除 + 重整集数 两个任务。');
            document.querySelector('.nav-link[data-view="task-manager-view"]').click();
        } catch (error) {
            alert(`提交任务失败: ${error.message}`);
        }
    };
    modalSaveBtn.addEventListener('click', currentModalConfirmHandler);

    // 关闭时清理本模块的确认处理器引用
    const clearHandlerRef = () => { if (currentModalConfirmHandler) { modalSaveBtn.removeEventListener('click', currentModalConfirmHandler); currentModalConfirmHandler = null; } };
    modalCancelBtn.addEventListener('click', clearHandlerRef, { once: true });
    modalCloseBtn.addEventListener('click', clearHandlerRef, { once: true });

    modal.classList.remove('hidden');
}

async function showManualImportModal(sourceId) {
    try {
        const sourceDetails = await apiFetch(`/api/ui/library/source/${sourceId}/details`);
        const providerName = sourceDetails.provider_name;
        const urlPrefixMap = {
            'bilibili': ['https://www.bilibili.com/video/', 'https://www.bilibili.com/bangumi/play/'],
            'tencent': 'https://v.qq.com/x/cover/',
            'iqiyi': 'https://www.iqiyi.com/v_',
            'youku': 'https://v.youku.com/v_show/',
            'mgtv': 'https://www.mgtv.com/b/',
            'acfun': 'https://www.acfun.cn/v/',
            'renren': 'https://www.rrsp.com.cn/video/'
        };
        const urlValidationPrefix = urlPrefixMap[providerName] || '';
        const urlPlaceholder = Array.isArray(urlValidationPrefix) ? `${urlValidationPrefix[0]}...` : (urlValidationPrefix ? `${urlValidationPrefix}...` : '请输入完整视频链接');

        const modal = document.getElementById('generic-modal');
        const modalTitle = document.getElementById('modal-title');
        const modalBody = document.getElementById('modal-body');
        const modalSaveBtn = document.getElementById('modal-save-btn');
        const modalCancelBtn = document.getElementById('modal-cancel-btn');
        const modalCloseBtn = document.getElementById('modal-close-btn');

        modalTitle.textContent = `手动导入弹幕 (${providerName})`;
        modalSaveBtn.textContent = '开始导入';
        modalBody.innerHTML = `
            <form id="manual-import-form" onsubmit="return false;">
                <div class="form-row"><label for="manual-episode-title">分集标题</label><input type="text" id="manual-episode-title" required></div>
                <div class="form-row"><label for="manual-episode-index">集数</label><input type="number" id="manual-episode-index" min="1" required></div>
                <div class="form-row"><label for="manual-episode-url">视频链接</label><input type="url" id="manual-episode-url" placeholder="${urlPlaceholder}" required></div>
             </form>
        `;

        const handleSave = async () => {
            const payload = {
                title: document.getElementById('manual-episode-title').value,
                episode_index: parseInt(document.getElementById('manual-episode-index').value, 10),
                url: document.getElementById('manual-episode-url').value
            };
            if (!payload.title || !payload.episode_index || !payload.url) { alert('请填写所有字段。'); return; }

            // 新增：前端URL前缀验证，支持多个有效前缀
            if (urlValidationPrefix) {
                const prefixes = Array.isArray(urlValidationPrefix) ? urlValidationPrefix : [urlValidationPrefix];
                if (!prefixes.some(prefix => payload.url.startsWith(prefix))) {
                    const expected = prefixes.map(p => `"${p}"`).join(' 或 ');
                    alert(`URL格式不正确。\n\n当前源为 "${providerName}"，链接应以 ${expected} 开头。`);
                    return;
                }
            }

            modalSaveBtn.disabled = true;
            modalSaveBtn.textContent = '导入中...';
            try {
                const response = await apiFetch(`/api/ui/library/source/${sourceId}/manual-import`, { method: 'POST', body: JSON.stringify(payload) });
                alert(response.message || '手动导入任务已提交。');
                hideScraperConfigModal();
                document.querySelector('.nav-link[data-view="task-manager-view"]').click();
            } catch (error) {
                alert(`导入失败: ${error.message}`);
            } finally {
                modalSaveBtn.disabled = false;
                modalSaveBtn.textContent = '开始导入';
            }
        };
        
        if (currentModalConfirmHandler) modalSaveBtn.removeEventListener('click', currentModalConfirmHandler);
        currentModalConfirmHandler = handleSave;
        modalSaveBtn.addEventListener('click', currentModalConfirmHandler);

        modal.classList.remove('hidden');
    } catch (error) {
        alert(`无法加载源信息: ${error.message}`);
    }
}

function hideScraperConfigModal() {
    const modal = document.getElementById('generic-modal');
    const modalSaveBtn = document.getElementById('modal-save-btn');
    modal.classList.add('hidden');
    if (currentModalConfirmHandler) {
        modalSaveBtn.removeEventListener('click', currentModalConfirmHandler);
        currentModalConfirmHandler = null;
    }
}

async function handleEpisodeAction(e) {
    const button = e.target.closest('.action-btn');
    if (!button) return;

    const wrapper = button.parentElement;
    const action = button.dataset.action;
    const episodeId = parseInt(wrapper.dataset.episodeId, 10);
    const episodeTitle = wrapper.dataset.episodeTitle;
    
    const sourceId = parseInt(episodeListView.dataset.sourceId, 10);
    const animeTitle = episodeListView.dataset.animeTitle;
    const animeId = parseInt(episodeListView.dataset.animeId, 10);

    switch (action) {
        case 'edit':
            const episode = currentEpisodes.find(ep => ep.id === episodeId);
            if (episode) {
                document.dispatchEvent(new CustomEvent('show:edit-episode', { detail: { episode, sourceId, animeTitle, animeId } }));
            }
            break;
        case 'refresh':
            if (!episodeTitle) {
                alert('No episode with that ID found.');
                return;
            }
            if (confirm(`您确定要刷新分集 '${episodeTitle}' 的弹幕吗？`)) {
                apiFetch(`/api/ui/library/episode/${episodeId}/refresh`, { method: 'POST' })
                    .then(response => alert(response.message || "刷新任务已开始。"))
                    .catch(error => alert(`启动刷新任务失败: ${error.message}`));
            }
            break;
        case 'view_danmaku':
            showDanmakuListView(episodeId, episodeTitle, sourceId, animeTitle, animeId);
            break;
        case 'delete':
            if (confirm(`您确定要删除分集 '${episodeTitle}' 吗？\n此操作将在后台提交一个删除任务。`)) {
                try {
                    const response = await apiFetch(`/api/ui/library/episode/${episodeId}`, { method: 'DELETE' });
                    if (confirm((response.message || "删除任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
                        document.querySelector('.nav-link[data-view="task-manager-view"]').click();
                    } else {
                        showEpisodeListView(sourceId, animeTitle, animeId);
                    }
                } catch (error) {
                    alert(`提交删除任务失败: ${error.message}`);
                }
            }
            break;
    }
}

async function showDanmakuListView(episodeId, episodeTitle, sourceId, animeTitle, animeId) {
    switchView('danmaku-list-view');
    danmakuListView.innerHTML = '<div>加载中...</div>';

    try {
        const data = await apiFetch(`/api/ui/comment/${episodeId}`);
        renderDanmakuListView(episodeId, episodeTitle, sourceId, animeTitle, animeId, data.comments);
    } catch (error) {
        danmakuListView.innerHTML = `<div class="error">加载弹幕失败: ${(error.message || error)}</div>`;
    }
}

function renderDanmakuListView(episodeId, episodeTitle, sourceId, animeTitle, animeId, comments) {
    danmakuListView.innerHTML = `
        <div class="episode-list-header">
            <h3>弹幕列表: ${animeTitle} - ${episodeTitle}</h3>
            <button id="back-to-episodes-from-danmaku-btn">&lt; 返回分集列表</button>
        </div>
        <pre id="danmaku-content-pre"></pre>
    `;
    const danmakuContentPre = document.getElementById('danmaku-content-pre');
    danmakuContentPre.textContent = comments.length > 0
        ? comments.map(c => `${c.p} | ${c.m}`).join('\n')
        : '该分集没有弹幕。';

    document.getElementById('back-to-episodes-from-danmaku-btn').addEventListener('click', () => {
        showEpisodeListView(sourceId, animeTitle, animeId);
    });
}

export function setupLibraryEventListeners() {
    initializeElements();
    librarySearchInput.addEventListener('input', handleLibrarySearch);
    libraryTableBody.addEventListener('click', handleLibraryAction);
    document.getElementById('back-to-library-from-detail-btn').addEventListener('click', () => switchView('library-view'));
    sourceDetailTableBody.addEventListener('click', handleSourceAction);

    document.getElementById('reassociate-sources-from-detail-btn').addEventListener('click', () => {
        const animeId = parseInt(animeDetailView.dataset.animeId, 10);
        const animeTitle = document.getElementById('detail-view-title').textContent;
        if (animeId && animeTitle && animeTitle !== '加载中...') {
            document.dispatchEvent(new CustomEvent('show:reassociate-view', { detail: { animeId, animeTitle } }));
        }
    });

    document.getElementById('select-all-sources-btn').addEventListener('click', () => {
        const allCheckboxes = sourceDetailTableBody.querySelectorAll('.source-checkbox');
        const shouldSelectAll = Array.from(allCheckboxes).some(cb => !cb.checked);
        sourceDetailTableBody.querySelectorAll('.source-checkbox').forEach(cb => {
            cb.checked = shouldSelectAll;
        });
        updateSelectAllButtonState();
    });

    document.getElementById('delete-selected-sources-btn').addEventListener('click', async () => {
        const selectedCheckboxes = sourceDetailTableBody.querySelectorAll('.source-checkbox:checked');
        if (selectedCheckboxes.length === 0) {
            alert('请先选择要删除的数据源。');
            return;
        }
        if (!confirm(`您确定要删除选中的 ${selectedCheckboxes.length} 个数据源吗？\n此操作将在后台提交一个批量删除任务。`)) return;

        const sourceIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value, 10));
        const animeId = parseInt(animeDetailView.dataset.animeId, 10);

        try {
            const response = await apiFetch(`/api/ui/library/sources/delete-bulk`, {
                method: 'POST',
                body: JSON.stringify({ source_ids: sourceIds })
            });
            if (confirm((response.message || "批量删除任务已提交。") + "\n\n是否立即跳转到任务管理器查看进度？")) {
                document.querySelector('.nav-link[data-view="task-manager-view"]').click();
            } else if (animeId) {
                showAnimeDetailView(animeId); // Refresh the view
            }
        } catch (error) {
            alert(`提交批量删除任务失败: ${error.message}`);
        }
    });
    
    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'library-view') {
            loadLibrary();
        }
    });

    document.addEventListener('show:episode-list', (e) => {
        // 从事件中获取的值可能是字符串（例如从input.value读取），
        // 需要转换为数字以确保后续比较 (e.g., a.animeId === animeId) 的正确性。
        const sourceId = parseInt(e.detail.sourceId, 10);
        const animeId = parseInt(e.detail.animeId, 10);
        const animeTitle = e.detail.animeTitle;
        showEpisodeListView(sourceId, animeTitle, animeId);
    });

    document.addEventListener('show:anime-detail', (e) => {
        showAnimeDetailView(e.detail.animeId);
    });
}

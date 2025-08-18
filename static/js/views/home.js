import { apiFetch } from '../api.js';
import { toggleLoader, switchView } from '../ui.js';

let logRefreshInterval = null;
let currentSearchData = { results: [], searchSeason: null, keyword: '' };
let itemsForBulkImport = [];

const typeMap = {
    'tv_series': '电视节目',
    'movie': '电影/剧场版'
};

/**
 * A lightweight keyword parser for frontend caching logic.
 * It's not as robust as the backend's but covers common cases.
 * @param {string} keyword The search keyword.
 * @returns {{title: string, season: number|null, episode: number|null}}
 */
function parseSearchKeyword(keyword) {
    keyword = keyword.trim();

    // Pattern for SXXEXX
    let match = keyword.match(/^(.*?)\s*S(\d{1,2})E(\d{1,4})$/i);
    if (match) {
        return { title: match[1].trim(), season: parseInt(match[2], 10), episode: parseInt(match[3], 10) };
    }

    // Pattern for Season XX or SXX
    match = keyword.match(/^(.*?)\s*(?:S|Season)\s*(\d{1,2})$/i);
    if (match) {
        return { title: match[1].trim(), season: parseInt(match[2], 10), episode: null };
    }
    
    const chineseNumMap = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10};
    match = keyword.match(/^(.*?)\s*第\s*([一二三四五六七八九十\d]+)\s*[季部]$/i);
    if (match) {
        const numStr = match[2];
        const season = chineseNumMap[numStr] || parseInt(numStr, 10);
        if (!isNaN(season)) return { title: match[1].trim(), season: season, episode: null };
    }
    return { title: keyword, season: null, episode: null };
}
function setupEventListeners() {
    // Home View
    document.getElementById('search-form').addEventListener('submit', handleSearch);
    document.getElementById('test-match-form').addEventListener('submit', handleTestMatch);
    document.getElementById('clear-cache-btn').addEventListener('click', handleClearCache);
    document.getElementById('bulk-import-btn').addEventListener('click', handleBulkImport);
    document.getElementById('select-all-btn').addEventListener('click', handleSelectAll);
    document.getElementById('filter-btn-movie').addEventListener('click', handleTypeFilterClick);
    document.getElementById('filter-btn-tv_series').addEventListener('click', handleTypeFilterClick);
    document.getElementById('results-filter-input').addEventListener('input', applyFiltersAndRender);
    document.getElementById('edit-import-modal-close-btn').addEventListener('click', () => document.getElementById('edit-import-modal').classList.add('hidden'));
    document.getElementById('edit-import-modal-cancel-btn').addEventListener('click', () => document.getElementById('edit-import-modal').classList.add('hidden'));
    document.getElementById('edit-import-modal-confirm-btn').addEventListener('click', handleConfirmEditImport);

    // --- 电视节目精确搜索逻辑重构 ---
    const searchSeasonInput = document.getElementById('search-season');
    const searchEpisodeInput = document.getElementById('search-episode');

    document.getElementById('enable-episode-search').addEventListener('change', (e) => {
        document.getElementById('episode-search-inputs').classList.toggle('hidden', !e.target.checked);
    });

    // 当季度输入框变化时，控制集数输入框的可用状态
    searchSeasonInput.addEventListener('input', () => {
        const seasonIsEmpty = !searchSeasonInput.value;
        searchEpisodeInput.disabled = seasonIsEmpty;
        if (seasonIsEmpty) {
            searchEpisodeInput.value = ''; // 如果季度被清空，也清空集数
        }
    });
    // 初始化时根据季度输入框的状态设置集数输入框
    searchEpisodeInput.disabled = !searchSeasonInput.value;

    // “插入”按钮的新逻辑
    document.getElementById('insert-episode-btn').addEventListener('click', () => {
        const season = searchSeasonInput.value;
        const episode = searchEpisodeInput.value;
        
        if (!season) {
            alert('请输入季度后再插入。');
            return;
        }

        let formatted = ` S${String(season).padStart(2, '0')}`;
        if (episode) {
            formatted += `E${String(episode).padStart(2, '0')}`;
        }
        insertAtCursor(document.getElementById('search-keyword'), formatted);
    });
    document.querySelectorAll('input[name="bulk-import-mode"]').forEach(radio => {
        radio.addEventListener('change', handleBulkImportModeChange);
    });

    // Bulk Import View
    document.getElementById('cancel-bulk-import-btn').addEventListener('click', () => switchView('home-view'));
    document.getElementById('confirm-bulk-import-btn').addEventListener('click', handleConfirmBulkImport);
    document.getElementById('search-tmdb-for-bulk-btn').addEventListener('click', handleBulkTmdbSearch);

    // Listen to global events
    document.addEventListener('auth:status-changed', (e) => {
        if (e.detail.loggedIn) {
            startLogRefresh();
        } else {
            stopLogRefresh();
        }
    });
    document.addEventListener('tmdb-search:selected-for-bulk', (e) => {
        // Listen for the event dispatched from editAnime.js
        const chineseName = e.detail.aliases_cn && e.detail.aliases_cn.length > 0 ? e.detail.aliases_cn[0] : null;
        document.getElementById('final-import-name').value = chineseName || e.detail.name_en || e.detail.name_jp || '';
        document.getElementById('final-import-tmdb-id').value = e.detail.id || '';
        switchView('bulk-import-view'); // Switch back to the bulk import view
    });

    // 新增：监听视图切换事件，以便在返回主页时恢复上一次的搜索结果
    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'home-view') {
            const lastResultsJSON = sessionStorage.getItem('lastSearchResults');
            if (lastResultsJSON) {
                try {
                    const lastData = JSON.parse(lastResultsJSON);
                    // 恢复上次的搜索结果和关键词
                    displayResults(lastData.results || [], lastData.search_season, lastData.keyword || '');
                } catch (err) {
                    console.error("从 sessionStorage 解析上次搜索结果失败", err);
                    sessionStorage.removeItem('lastSearchResults');
                }
            }
        }
    });
}

function handleBulkImportModeChange(e) {
    const unifiedFields = document.getElementById('unified-import-fields');
    unifiedFields.classList.toggle('hidden', e.target.value !== 'unified');
}

function insertAtCursor(inputField, textToInsert) {
    const startPos = inputField.selectionStart;
    const endPos = inputField.selectionEnd;
    const text = inputField.value;
    inputField.value = text.substring(0, startPos) + textToInsert + text.substring(endPos, text.length);
    inputField.focus();
    inputField.selectionStart = startPos + textToInsert.length;
    inputField.selectionEnd = startPos + textToInsert.length;
}

function startLogRefresh() {
    refreshServerLogs();
    if (logRefreshInterval) clearInterval(logRefreshInterval);
    logRefreshInterval = setInterval(refreshServerLogs, 3000);
}

function stopLogRefresh() {
    if (logRefreshInterval) clearInterval(logRefreshInterval);
    logRefreshInterval = null;
}

async function refreshServerLogs() {
    const logOutput = document.getElementById('log-output');
    if (!localStorage.getItem('danmu_api_token') || !logOutput) return;
    try {
        const logs = await apiFetch('/api/ui/logs');
        logOutput.textContent = logs.join('\n');
    } catch (error) {
        console.error("刷新日志失败:", error.message);
    }
}

async function handleSearch(e) {
    e.preventDefault();
    const keyword = document.getElementById('search-keyword').value.trim();
    if (!keyword) {
        alert('请输入搜索关键词。');
        return;
    }

    // --- 新增：前端缓存逻辑 ---
    const { title: newTitle, season: newSeason, episode: newEpisode } = parseSearchKeyword(keyword);
    const { title: lastTitle, season: lastSeason, episode: lastEpisode } = parseSearchKeyword(currentSearchData.keyword);

    // 如果上次是通用搜索，而这次是针对同一标题的特定分集搜索，则使用缓存
    if (currentSearchData.results.length > 0 && newTitle === lastTitle && newEpisode !== null && lastEpisode === null) {
        console.log("前端缓存命中：正在使用上次的搜索结果。");
        toggleLoader(true);

        const updatedResults = JSON.parse(JSON.stringify(currentSearchData.results));
        updatedResults.forEach(item => {
            item.currentEpisodeIndex = newEpisode;
        });

        const seasonForDisplay = newSeason || lastSeason || 1;
        
        // 更新 sessionStorage 和当前状态
        sessionStorage.setItem('lastSearchResults', JSON.stringify({ results: updatedResults, search_season: seasonForDisplay, keyword: keyword }));
        displayResults(updatedResults, seasonForDisplay, keyword);
        
        toggleLoader(false);
        return; // 避免网络请求
    }
    // --- 缓存逻辑结束 ---

    document.getElementById('results-list').innerHTML = '';
    toggleLoader(true);

    try {
        const data = await apiFetch(`/api/ui/search/provider?keyword=${encodeURIComponent(keyword)}`);
        // 新增：将搜索结果和关键词一同存入 sessionStorage
        sessionStorage.setItem('lastSearchResults', JSON.stringify({ ...data, keyword }));
        const processedResults = data.results || [];
        const searchSeason = data.search_season;
        displayResults(processedResults, searchSeason, keyword);
    } catch (error) {
        alert(`搜索失败: ${(error.message || error)}`);
    } finally {
        toggleLoader(false);
    }
}

function getBaseTitle(title) {
    if (!title) return '';
    // 尝试移除常见的季度/部分标识符以获取基础标题用于排序
    // 例如: "Title Season 2", "Title S02", "Title 第二季", "Title II"
    let baseTitle = title.replace(/\s*Season\s*\d+/i, '')
                         .replace(/\s*S\d+$/i, '')
                         .replace(/\s*第\s*[一二三四五六七八九十\d]+\s*[季部篇章幕]$/, '')
                         .replace(/\s+[IVXLCDMⅠ-Ⅻ]+$/, '');
    return baseTitle.trim();
}

function romanToInt(s) {
    const map = { 'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000 };
    let result = 0;
    for (let i = 0; i < s.length; i++) {
        const current = map[s[i]];
        const next = map[s[i + 1]];
        if (next && current < next) {
            result -= current;
        } else {
            result += current;
        }
    }
    return result;
}

function displayResults(results, searchSeason, keyword) {
    // 智能排序：首先按基础标题分组，然后在组内按季度升序排列
    results.sort((a, b) => {
        const baseTitleA = getBaseTitle(a.title);
        const baseTitleB = getBaseTitle(b.title);

        if (baseTitleA.localeCompare(baseTitleB) !== 0) {
            return baseTitleA.localeCompare(baseTitleB);
        }
        return a.season - b.season;
    });

    // 更新当前搜索状态
    currentSearchData = { results, searchSeason, keyword };

    const resultsFilterControls = document.getElementById('results-filter-controls');
    resultsFilterControls.classList.toggle('hidden', results.length === 0);

    if (results.length > 0) {
        document.getElementById('filter-btn-movie').classList.add('active');
        document.getElementById('filter-btn-movie').querySelector('.status-icon').textContent = '✅';
        document.getElementById('filter-btn-tv_series').classList.add('active');
        document.getElementById('filter-btn-tv_series').querySelector('.status-icon').textContent = '✅';
        document.getElementById('results-filter-input').value = '';
        applyFiltersAndRender();
    } else {
        document.getElementById('results-list').innerHTML = '<li>未找到结果。</li>';
    }
}

function applyFiltersAndRender() {
    if (!currentSearchData || !currentSearchData.results) return;
    const searchSeason = currentSearchData.searchSeason;
    const activeTypes = new Set();
    if (document.getElementById('filter-btn-movie').classList.contains('active')) activeTypes.add('movie');
    if (document.getElementById('filter-btn-tv_series').classList.contains('active')) activeTypes.add('tv_series');
    let filteredResults = currentSearchData.results.filter(item => activeTypes.has(item.type));
    const filterText = document.getElementById('results-filter-input').value.toLowerCase();
    if (filterText) {
        filteredResults = filteredResults.filter(item => item.title.toLowerCase().includes(filterText));
    }
    renderSearchResults(filteredResults, searchSeason);
}

function renderSearchResults(results, searchSeason) {
    const resultsList = document.getElementById('results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>没有符合筛选条件的结果。</li>';
        return;
    }
    results.forEach(item => {
        const li = document.createElement('li');
        const leftContainer = document.createElement('div');
        leftContainer.className = 'result-item-left';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = item.mediaId;
        leftContainer.appendChild(checkbox);
        
        // Add click listener to the entire row (li)
        li.addEventListener('click', (e) => {
            // Only toggle if the click is not on the button or the checkbox itself
            if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'INPUT') {
                checkbox.checked = !checkbox.checked;
            }
        });

        const posterImg = document.createElement('img');
        posterImg.className = 'poster';
        posterImg.src = item.imageUrl || '/static/placeholder.png';
        posterImg.referrerPolicy = 'no-referrer';
        posterImg.alt = item.title;
        leftContainer.appendChild(posterImg);
        const infoDiv = document.createElement('div');
        infoDiv.className = 'info';
        const displayType = typeMap[item.type] || item.type;
        
        const metaParts = [
            `源: ${item.provider}`,
            `类型: ${displayType}`,
            `年份: ${item.year || 'N/A'}`
        ];
        if (item.type === 'tv_series') {
            metaParts.push(`季度: ${String(item.season).padStart(2, '0')}`);
            metaParts.push(`总集数: ${item.episodeCount || '未知'}`);
            if (item.currentEpisodeIndex) {
                // 修正：如果搜索了特定分集，则只追加集数信息
                metaParts.push(`集: ${String(item.currentEpisodeIndex).padStart(2, '0')}`);
            }
        }
        infoDiv.innerHTML = `<p class="title">${item.title}</p><p class="meta">${metaParts.join(' | ')}</p>`;

        leftContainer.appendChild(infoDiv);

        const buttonGroup = document.createElement('div');
        buttonGroup.className = 'result-item-actions';

        const editImportBtn = document.createElement('button');
        editImportBtn.textContent = '编辑导入';
        editImportBtn.className = 'secondary-btn';
        editImportBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            showEditImportModal(item);
        });

        const directImportBtn = document.createElement('button');
        directImportBtn.textContent = '直接导入';
        directImportBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent the li click event from firing
            handleImportClick(directImportBtn, item, searchSeason)
        });
        buttonGroup.appendChild(editImportBtn);
        buttonGroup.appendChild(directImportBtn);
        li.appendChild(leftContainer);
        li.appendChild(buttonGroup);
        resultsList.appendChild(li);
    });
}

async function handleImportClick(button, item, searchSeason) {
    button.disabled = true;
    button.textContent = '导入中...';
    try {
        const data = await apiFetch('/api/ui/import', {
            method: 'POST',
            body: JSON.stringify({
                provider: item.provider,
                media_id: item.mediaId,
                anime_title: item.title,
                type: item.type,
                // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
                // 否则，使用从单个结果中解析出的季度
                season: searchSeason !== null ? searchSeason : item.season,
                image_url: item.imageUrl,
                douban_id: item.douban_id,
                current_episode_index: item.currentEpisodeIndex,
            }),
        });
        alert(data.message);
    } catch (error) {
        alert(`提交导入任务失败: ${(error.message || error)}`);
    } finally {
        button.disabled = false;
        button.textContent = '导入弹幕';
    }
}

async function handleTestMatch(e) {
    e.preventDefault();
    const apiToken = document.getElementById('test-token-input').value.trim();
    const filename = document.getElementById('test-filename-input').value.trim();
    if (!apiToken || !filename) {
        alert('Token和文件名都不能为空。');
        return;
    }
    const testMatchResults = document.getElementById('test-match-results');
    testMatchResults.textContent = '正在测试...';
    const testButton = e.target.querySelector('button');
    testButton.disabled = true;

    try {
        const data = await apiFetch(`/api/${apiToken}/match`, {
            method: 'POST',
            body: JSON.stringify({ fileName: filename })
        });
        if (data.isMatched) {
            const match = data.matches[0];
            testMatchResults.textContent = `[匹配成功]\n番剧: ${match.animeTitle} (ID: ${match.animeId})\n分集: ${match.episodeTitle} (ID: ${match.episodeId})\n类型: ${match.typeDescription}`;
        } else {
            testMatchResults.textContent = '未匹配到任何结果。';
        }
    } catch (error) {
        testMatchResults.textContent = `请求失败: ${(error.message || error)}`;
    } finally {
        testButton.disabled = false;
    }
}

async function handleClearCache() {
    if (confirm("您确定要清除所有缓存吗？\n这将清除所有搜索结果和分集列表的临时缓存，下次访问时需要重新从网络获取。")) {
        try {
            const response = await apiFetch('/api/ui/cache/clear', { method: 'POST' });
            alert(response.message || "缓存已成功清除！");
        } catch (error) {
            alert(`清除缓存失败: ${(error.message || error)}`);
        }
    }
}

async function handleBulkImport() {
    const resultsList = document.getElementById('results-list');
    const selectedCheckboxes = resultsList.querySelectorAll('input[type="checkbox"]:checked');
    if (selectedCheckboxes.length === 0) {
        alert("请选择要导入的媒体。");
        return;
    }

    const selectedMediaIds = new Set(Array.from(selectedCheckboxes).map(cb => cb.value));
    itemsForBulkImport = currentSearchData.results.filter(item => selectedMediaIds.has(item.mediaId));

    // Always show the bulk import view
    _showBulkImportView(itemsForBulkImport);
}

function _showBulkImportView(items) {
    switchView('bulk-import-view');
    const listEl = document.getElementById('bulk-import-list');
    listEl.innerHTML = '';
    items.forEach(item => {
        const li = document.createElement('li');
        const displayType = typeMap[item.type] || item.type;
        li.textContent = `${item.title} (源: ${item.provider}, 类型: ${displayType}, 季: ${item.season})`;
        li.style.cursor = 'pointer';
        li.addEventListener('click', () => {
            document.getElementById('final-import-name').value = item.title;
        });
        listEl.appendChild(li);
    });

    const uniqueTitles = new Set(items.map(item => item.title));
    const unifiedModeRadio = document.querySelector('input[name="bulk-import-mode"][value="unified"]');
    const separateModeRadio = document.querySelector('input[name="bulk-import-mode"][value="separate"]');
    const unifiedFields = document.getElementById('unified-import-fields');
    const infoParagraph = document.querySelector('#bulk-import-view .form-card p');

    if (uniqueTitles.size === 1) {
        infoParagraph.textContent = `您选择了 ${items.length} 个标题相同的条目。请确认导入模式。`;
        unifiedModeRadio.checked = true;
        unifiedFields.classList.remove('hidden');
        document.getElementById('final-import-name').value = items[0].title;
    } else {
        infoParagraph.textContent = `检测到您选择的媒体标题不一致。请指定导入模式。`;
        separateModeRadio.checked = true;
        unifiedFields.classList.add('hidden');
        document.getElementById('final-import-name').value = items[0].title; // Still set a default
    }

    document.getElementById('final-import-tmdb-id').value = '';
}

async function _performSeparateBulkImport(items) {
    const bulkImportBtn = document.getElementById('bulk-import-btn');
    bulkImportBtn.disabled = true;
    bulkImportBtn.textContent = '批量导入中...';

    try {
        for (const item of items) {
            try {
                const data = await apiFetch('/api/ui/import', {
                    method: 'POST',
                    body: JSON.stringify({
                        provider: item.provider, media_id: item.mediaId, anime_title: item.title,
                        type: item.type, season: item.season, image_url: item.imageUrl, douban_id: item.douban_id,
                        current_episode_index: item.currentEpisodeIndex, tmdb_id: null
                    }),
                });
                console.log(`提交独立导入任务 ${item.title} (源: ${item.provider}) 成功: ${data.message}`);
            } catch (error) {
                if (error.message && error.message.includes("该数据源已存在于弹幕库中")) {
                     console.warn(`跳过已存在的源: ${item.title} (源: ${item.provider})`);
                } else {
                    console.error(`提交独立导入任务 ${item.title} (源: ${item.provider}) 失败: ${error.message || error}`);
                }
            }
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        alert("批量导入任务已提交，请在任务管理器中查看进度。");
    } finally {
        bulkImportBtn.disabled = false;
        bulkImportBtn.textContent = '批量导入';
        switchView('home-view');
    }
}

async function _performUnifiedBulkImport(items, finalTitle, finalTmdbId = null) {
    const bulkImportBtn = document.getElementById('bulk-import-btn');
    bulkImportBtn.disabled = true;
    bulkImportBtn.textContent = '批量导入中...';

    try {
        for (const item of items) {
            try {
                const data = await apiFetch('/api/ui/import', {
                    method: 'POST',
                    body: JSON.stringify({
                        provider: item.provider, media_id: item.mediaId, anime_title: finalTitle,
                        type: item.type, season: item.season, image_url: item.imageUrl, douban_id: item.douban_id,
                        current_episode_index: item.currentEpisodeIndex,
                        tmdb_id: finalTmdbId // Pass the final TMDB ID
                    }),
                });
                console.log(`提交导入任务 ${finalTitle} (源: ${item.provider}) 成功: ${data.message}`);
            } catch (error) {
                if (error.message && error.message.includes("该数据源已存在于弹幕库中")) {
                    console.warn(`跳过已存在的源: ${finalTitle} (源: ${item.provider})`);
                } else {
                    console.error(`提交导入任务 ${finalTitle} (源: ${item.provider}) 失败: ${error.message || error}`);
                }
            }
            await new Promise(resolve => setTimeout(resolve, 200));
        }
        alert("批量导入任务已提交，请在任务管理器中查看进度。");
    } finally {
        bulkImportBtn.disabled = false;
        bulkImportBtn.textContent = '批量导入';
        switchView('home-view'); // Go back to home view after completion
    }
}

function handleConfirmBulkImport() {
    const importMode = document.querySelector('input[name="bulk-import-mode"]:checked').value;

    if (importMode === 'unified') {
        const finalTitle = document.getElementById('final-import-name').value.trim();
        const finalTmdbId = document.getElementById('final-import-tmdb-id').value.trim() || null;
        if (!finalTitle) {
            alert("最终导入名称不能为空。");
            return;
        }
        if (itemsForBulkImport.length > 0) {
            _performUnifiedBulkImport(itemsForBulkImport, finalTitle, finalTmdbId);
        }
    } else { // 'separate' mode
        if (itemsForBulkImport.length > 0 && confirm(`确定要将 ${itemsForBulkImport.length} 个条目作为独立作品分别导入吗？`)) {
            _performSeparateBulkImport(itemsForBulkImport);
        }
    }
}

function handleBulkTmdbSearch() {
    const finalName = document.getElementById('final-import-name').value.trim();
    if (finalName) {
        // Dispatch event to be caught by editAnime.js to show the TMDB search view
        document.dispatchEvent(new CustomEvent('show:tmdb-search-for-bulk', { detail: { keyword: finalName } }));
    }
}

function handleSelectAll() {
    const resultsList = document.getElementById('results-list');
    const checkboxes = resultsList.querySelectorAll('input[type="checkbox"]');
    if (checkboxes.length === 0) return;
    const shouldCheckAll = Array.from(checkboxes).some(cb => !cb.checked);
    checkboxes.forEach(cb => { cb.checked = shouldCheckAll; });
}

function handleTypeFilterClick(e) {
    const btn = e.currentTarget;
    btn.classList.toggle('active');
    const icon = btn.querySelector('.status-icon');
    icon.textContent = btn.classList.contains('active') ? '✅' : '❌';
    applyFiltersAndRender();
}

async function showEditImportModal(item) {
    const modal = document.getElementById('edit-import-modal');
    const modalTitle = document.getElementById('edit-import-modal-title');
    const modalBody = document.getElementById('edit-import-modal-body');
    const confirmBtn = document.getElementById('edit-import-modal-confirm-btn');

    modalTitle.textContent = `编辑导入: ${item.title}`;
    modalBody.innerHTML = '<div id="loader"></div>'; // Show loader
    modal.classList.remove('hidden');
    
    // Store item data on the modal for later use
    modal.dataset.item = JSON.stringify(item);

    try {
        const episodes = await apiFetch(`/api/ui/search/episodes?provider=${item.provider}&media_id=${item.mediaId}&media_type=${item.type}`);
        renderEditImportModalContent(item, episodes);
    } catch (error) {
        modalBody.innerHTML = `<p class="error">加载分集列表失败: ${error.message}</p>`;
        confirmBtn.disabled = true;
    }
}

let draggedItem = null; // To keep track of the item being dragged

function renderEditImportModalContent(item, episodes) {
    const modalBody = document.getElementById('edit-import-modal-body');
    const confirmBtn = document.getElementById('edit-import-modal-confirm-btn');
    confirmBtn.disabled = false;

    modalBody.innerHTML = `
        <div class="edit-import-title-section">
            <label for="edit-import-anime-title">作品标题:</label>
            <input type="text" id="edit-import-anime-title" value="${item.title}">
            <button type="button" id="reimport-episodes-btn" class="secondary-btn" title="根据此标题在弹幕库中查找已存在的集数，并从下方列表中移除它们。">重整导入分集</button>
        </div>
        <ul id="edit-import-episodes-list" class="edit-import-episodes-list">
        </ul>
    `;

    document.getElementById('reimport-episodes-btn').addEventListener('click', handleReimportEpisodes);
    const episodeListEl = document.getElementById('edit-import-episodes-list');
    if (episodes.length === 0) {
        episodeListEl.innerHTML = '<li>未找到任何分集。</li>';
        return;
    }

    episodes.forEach(ep => {
        const li = document.createElement('li');
        li.draggable = true; // Make the list item draggable
        li.dataset.episode = JSON.stringify(ep); // Store original episode data
        li.innerHTML = `
            <span class="ep-index">${ep.episodeIndex}.</span>
            <input type="text" class="ep-title-input" value="${ep.title}">
            <button class="ep-delete-btn" title="删除此集">&times;</button>
        `;
        li.querySelector('.ep-delete-btn').addEventListener('click', () => {
            li.remove();
            reindexEpisodes();
        });
        episodeListEl.appendChild(li);
    });

    // Add drag-and-drop event listeners to the list container
    episodeListEl.addEventListener('dragstart', (e) => {
        // We only want to drag 'LI' elements
        if (e.target.tagName === 'LI') {
            draggedItem = e.target;
            // Use a timeout to avoid the drag image disappearing
            setTimeout(() => {
                e.target.classList.add('dragging');
            }, 0);
        }
    });

    episodeListEl.addEventListener('dragover', (e) => {
        e.preventDefault(); // This is necessary to allow dropping
        if (!draggedItem) return;

        const afterElement = getDragAfterElement(episodeListEl, e.clientY);
        if (afterElement == null) {
            episodeListEl.appendChild(draggedItem);
        } else {
            episodeListEl.insertBefore(draggedItem, afterElement);
        }
    });

    episodeListEl.addEventListener('dragend', () => {
        if (draggedItem) {
            draggedItem.classList.remove('dragging');
            draggedItem = null;
            reindexEpisodes(); // Re-number the episodes after the drop is complete
        }
    });
}

function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('li:not(.dragging)')];
    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) { return { offset: offset, element: child }; } 
        else { return closest; }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function reindexEpisodes() {
    const episodeListEl = document.getElementById('edit-import-episodes-list');
    const items = episodeListEl.querySelectorAll('li');
    items.forEach((li, index) => {
        const indexSpan = li.querySelector('.ep-index');
        if (indexSpan) indexSpan.textContent = `${index + 1}.`;
        const epData = JSON.parse(li.dataset.episode);
        epData.episodeIndex = index + 1;
        li.dataset.episode = JSON.stringify(epData);
    });
}

async function handleReimportEpisodes() {
    const titleInput = document.getElementById('edit-import-anime-title');
    const animeTitle = titleInput.value.trim();
    if (!animeTitle) {
        alert('请输入要匹配的作品标题。');
        return;
    }

    const reimportBtn = document.getElementById('reimport-episodes-btn');
    reimportBtn.disabled = true;
    reimportBtn.textContent = '查询中...';

    try {
        const existingIndices = await apiFetch(`/api/ui/library/episodes-by-title?title=${encodeURIComponent(animeTitle)}`);
        
        if (existingIndices.length === 0) {
            alert(`在弹幕库中未找到作品 "${animeTitle}" 或该作品没有任何分集。`);
            return;
        }

        const existingIndicesSet = new Set(existingIndices);
        const episodeListEl = document.getElementById('edit-import-episodes-list');
        const allEpisodeItems = Array.from(episodeListEl.querySelectorAll('li'));
        
        let removedCount = 0;
        allEpisodeItems.forEach(li => {
            const epData = JSON.parse(li.dataset.episode);
            if (existingIndicesSet.has(epData.episodeIndex)) {
                li.remove();
                removedCount++;
            }
        });

        reindexEpisodes(); // Re-number the remaining episodes
        alert(`重整完成！根据库内记录，移除了 ${removedCount} 个已存在的分集。`);

    } catch (error) {
        alert(`查询已存在分集失败: ${error.message}`);
    } finally {
        reimportBtn.disabled = false;
        reimportBtn.textContent = '重整导入分集';
    }
}

async function handleConfirmEditImport() {
    const modal = document.getElementById('edit-import-modal');
    const confirmBtn = document.getElementById('edit-import-modal-confirm-btn');
    const originalItem = JSON.parse(modal.dataset.item);
    const finalAnimeTitle = document.getElementById('edit-import-anime-title').value.trim();
    if (!finalAnimeTitle) { alert('作品标题不能为空。'); return; }

    const finalEpisodes = Array.from(document.querySelectorAll('#edit-import-episodes-list li')).map(li => {
        const originalEpisode = JSON.parse(li.dataset.episode);
        return { ...originalEpisode, title: li.querySelector('.ep-title-input').value.trim(), episodeIndex: parseInt(li.querySelector('.ep-index').textContent, 10) };
    });

    const payload = { ...originalItem, anime_title: finalAnimeTitle, media_type: originalItem.type, episodes: finalEpisodes };
    delete payload.currentEpisodeIndex; // Not needed for this endpoint

    confirmBtn.disabled = true;
    confirmBtn.textContent = '提交中...';
    try {
        const data = await apiFetch('/api/ui/import/edited', { method: 'POST', body: JSON.stringify(payload) });
        alert(data.message || '编辑导入任务已提交。');
        modal.classList.add('hidden');
    } catch (error) {
        alert(`提交导入任务失败: ${error.message}`);
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '确认导入';
    }
}

export { setupEventListeners as setupHomeEventListeners };
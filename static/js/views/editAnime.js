import { apiFetch } from '../api.js';
import { switchView } from '../ui.js';

// --- State ---
let _currentSearchSelectionData = null;

// --- Constants ---
const typeMap = {
    'tv_series': '电视节目',
    'movie': '电影/剧场版',
    'ova': 'OVA',
    'other': '其他'
};

// --- DOM Elements ---
let editAnimeView, editAnimeForm, editAnimeTypeSelect, selectEgidBtn, editAnimeTmdbIdInput;
let bangumiSearchView, tmdbSearchView, doubanSearchView, imdbSearchView, tvdbSearchView, egidView, reassociateView;
let backToEditAnimeFromBgmSearchBtn, backToEditAnimeFromTmdbSearchBtn, backToEditAnimeFromDoubanSearchBtn, backToEditAnimeFromImdbSearchBtn, backToEditAnimeFromTvdbSearchBtn, backToEditFromEgidBtn, backToDetailFromReassociateBtn;
let editEpisodeView, editEpisodeForm;

function initializeElements() {
    editAnimeView = document.getElementById('edit-anime-view');
    editAnimeForm = document.getElementById('edit-anime-form');
    editAnimeTypeSelect = document.getElementById('edit-anime-type');
    selectEgidBtn = document.getElementById('select-egid-btn');
    editAnimeTmdbIdInput = document.getElementById('edit-anime-tmdbid');

    bangumiSearchView = document.getElementById('bangumi-search-view');
    tmdbSearchView = document.getElementById('tmdb-search-view');
    doubanSearchView = document.getElementById('douban-search-view');
    tvdbSearchView = document.getElementById('tvdb-search-view');
    imdbSearchView = document.getElementById('imdb-search-view');
    egidView = document.getElementById('egid-view');
    // 为元数据搜索列表添加特定类，以便应用特定样式
    document.getElementById('bangumi-search-results-list').classList.add('metadata-search-list');
    document.getElementById('douban-search-results-list').classList.add('metadata-search-list');
    document.getElementById('tmdb-search-results-list').classList.add('metadata-search-list');
    document.getElementById('tvdb-search-results-list').classList.add('metadata-search-list');
    document.getElementById('imdb-search-results-list').classList.add('metadata-search-list');
    reassociateView = document.getElementById('reassociate-view');

    backToEditAnimeFromBgmSearchBtn = document.getElementById('back-to-edit-anime-from-bgm-search-btn');
    backToEditAnimeFromTmdbSearchBtn = document.getElementById('back-to-edit-anime-from-tmdb-search-btn');
    backToEditAnimeFromDoubanSearchBtn = document.getElementById('back-to-edit-anime-from-douban-search-btn');
    backToEditAnimeFromTvdbSearchBtn = document.getElementById('back-to-edit-anime-from-tvdb-search-btn');
    backToEditAnimeFromImdbSearchBtn = document.getElementById('back-to-edit-anime-from-imdb-search-btn');
    backToEditFromEgidBtn = document.getElementById('back-to-edit-from-egid-btn');
    backToDetailFromReassociateBtn = document.getElementById('back-to-detail-from-reassociate-btn');

    editEpisodeView = document.getElementById('edit-episode-view');
    editEpisodeForm = document.getElementById('edit-episode-form');
}

async function showEditAnimeView(animeId) {
    switchView('edit-anime-view');
    clearSearchSelectionState();
    editAnimeForm.reset();
    editAnimeForm.querySelector('button[type="submit"]').disabled = true;

    try {
        const details = await apiFetch(`/api/ui/library/anime/${animeId}/details`);
        populateEditForm(details);
    } catch (error) {
        alert(`加载编辑信息失败: ${error.message}`);
        switchView('library-view');
    } finally {
        editAnimeForm.querySelector('button[type="submit"]').disabled = false;
    }
}

function populateEditForm(details) {
    document.getElementById('edit-anime-id').value = details.anime_id;
    document.getElementById('edit-anime-title').value = details.title;
    editAnimeTypeSelect.value = details.type;
    document.getElementById('edit-anime-season').value = details.season;
    document.getElementById('edit-anime-episode-count').value = details.episode_count || '';
    editAnimeTmdbIdInput.value = details.tmdb_id || '';
    document.getElementById('edit-anime-egid').value = details.tmdb_episode_group_id || '';
    document.getElementById('edit-anime-bgmid').value = details.bangumi_id || '';
    document.getElementById('edit-anime-tvdbid').value = details.tvdb_id || '';
    document.getElementById('edit-anime-doubanid').value = details.douban_id || '';
    document.getElementById('edit-anime-imdbid').value = details.imdb_id || '';
    document.getElementById('edit-anime-name-en').value = details.name_en || '';
    document.getElementById('edit-anime-name-jp').value = details.name_jp || '';
    document.getElementById('edit-anime-name-romaji').value = details.name_romaji || '';
    document.getElementById('edit-anime-alias-cn-1').value = details.alias_cn_1 || '';
    document.getElementById('edit-anime-alias-cn-2').value = details.alias_cn_2 || '';
    document.getElementById('edit-anime-alias-cn-3').value = details.alias_cn_3 || '';
    handleAnimeTypeChange();
    updateEgidSelectButtonState();
}

async function handleEditAnimeSave(e) {
    e.preventDefault();
    const animeId = document.getElementById('edit-anime-id').value;
    const payload = {
        title: document.getElementById('edit-anime-title').value,
        type: document.getElementById('edit-anime-type').value,
        season: parseInt(document.getElementById('edit-anime-season').value, 10),
        episode_count: document.getElementById('edit-anime-episode-count').value ? parseInt(document.getElementById('edit-anime-episode-count').value, 10) : null,
        tmdb_id: document.getElementById('edit-anime-tmdbid').value || null,
        tmdb_episode_group_id: document.getElementById('edit-anime-egid').value || null,
        bangumi_id: document.getElementById('edit-anime-bgmid').value || null,
        tvdb_id: document.getElementById('edit-anime-tvdbid').value || null,
        douban_id: document.getElementById('edit-anime-doubanid').value || null,
        imdb_id: document.getElementById('edit-anime-imdbid').value || null,
        name_en: document.getElementById('edit-anime-name-en').value || null,
        name_jp: document.getElementById('edit-anime-name-jp').value || null,
        name_romaji: document.getElementById('edit-anime-name-romaji').value || null,
        alias_cn_1: document.getElementById('edit-anime-alias-cn-1').value || null,
        alias_cn_2: document.getElementById('edit-anime-alias-cn-2').value || null,
        alias_cn_3: document.getElementById('edit-anime-alias-cn-3').value || null,
    };

    const saveButton = editAnimeForm.querySelector('button[type="submit"]');
    saveButton.disabled = true;
    saveButton.textContent = '保存中...';

    try {
        await apiFetch(`/api/ui/library/anime/${animeId}`, { method: 'PUT', body: JSON.stringify(payload) });
        alert("信息更新成功！");
        document.getElementById('back-to-library-from-edit-btn').click();
    } catch (error) {
        alert(`更新失败: ${(error.message || error)}`);
    } finally {
        saveButton.disabled = false;
        saveButton.textContent = '保存更改';
    }
}

function handleAnimeTypeChange() {
    const isMovie = editAnimeTypeSelect.value === 'movie';

    // --- Season ---
    const seasonInput = document.getElementById('edit-anime-season');
    seasonInput.disabled = isMovie;
    if (isMovie) seasonInput.value = 1;

    // --- Episode Count ---
    const episodeCountInput = document.getElementById('edit-anime-episode-count');
    episodeCountInput.disabled = isMovie;
    if (isMovie) episodeCountInput.value = 1;

    // --- Episode Group ID ---
    const egidInput = document.getElementById('edit-anime-egid');
    const egidWrapper = egidInput.closest('.input-with-icon');
    egidInput.disabled = isMovie;
    egidWrapper.classList.toggle('disabled', isMovie);
    if (isMovie) egidInput.value = '';

    updateEgidSelectButtonState();
}

function updateEgidSelectButtonState() {
    const tmdbId = editAnimeTmdbIdInput.value.trim();
    const isMovie = editAnimeTypeSelect.value === 'movie';
    selectEgidBtn.disabled = !tmdbId || isMovie;
}

function clearSearchSelectionState() {
    _currentSearchSelectionData = null;
    const applyBtns = document.querySelectorAll('#edit-anime-form .apply-btn');
    applyBtns.forEach(btn => btn.remove());
}

function _applyAliases(aliases, mainTitle) {
    // 过滤掉与主标题相同以及为空的别名
    const filteredAliases = (aliases || []).filter(alias => alias && alias !== mainTitle);
    // 应用最多前三个别名
    updateFieldWithApplyLogic('edit-anime-alias-cn-1', filteredAliases[0]);
    updateFieldWithApplyLogic('edit-anime-alias-cn-2', filteredAliases[1]);
    updateFieldWithApplyLogic('edit-anime-alias-cn-3', filteredAliases[2]);
}

function applySearchSelectionData() {
    if (!_currentSearchSelectionData) return;
    const data = _currentSearchSelectionData;

    // 辅助函数：检查字符串是否包含日文字符（平假名、片假名、汉字）
    const containsJapanese = (str) => {
        if (!str) return false;
        // 此正则表达式匹配日文假名和常见的CJK统一表意文字
        return /[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]/.test(str);
    };

    switch (data._source) {
        case 'bangumi':
            const mainTitleBgm = data.name;
            document.getElementById('edit-anime-bgmid').value = data.id || '';
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            updateFieldWithApplyLogic('edit-anime-name-romaji', data.name_romaji);
            _applyAliases(data.aliases_cn, mainTitleBgm);
            break;
        case 'tmdb':
            const mainTitleTmdb = data.main_title_from_search;
            document.getElementById('edit-anime-tmdbid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-tvdbid', data.tvdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            updateFieldWithApplyLogic('edit-anime-name-romaji', data.name_romaji);
            _applyAliases(data.aliases_cn, mainTitleTmdb);
            break;
        case 'imdb':
            const mainTitleImdb = data.name_en;
            document.getElementById('edit-anime-imdbid').value = data.id || '';
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            _applyAliases(data.aliases_cn, mainTitleImdb);
            break;
        case 'tvdb':
            const mainTitleTvdb = data.name_en;
            document.getElementById('edit-anime-tvdbid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            _applyAliases(data.aliases_cn, mainTitleTvdb);
            break;
        case 'douban':
            const mainTitleDouban = (data.aliases_cn && data.aliases_cn.length > 0) ? data.aliases_cn[0] : '';
            document.getElementById('edit-anime-doubanid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            _applyAliases(data.aliases_cn, mainTitleDouban);
            break;
    }
}

function updateFieldWithApplyLogic(fieldId, newValue) {
    const input = document.getElementById(fieldId);
    if (!input) return;
    const wrapper = input.parentElement;
    let applyBtn = wrapper.querySelector('.apply-btn');
    const normalizedNewValue = (newValue === null || newValue === undefined) ? '' : String(newValue).trim();
    if (normalizedNewValue === '') {
        if (applyBtn) applyBtn.remove();
        return;
    }
    const currentValue = input.value.trim();
    if (currentValue === '' || currentValue === normalizedNewValue) {
        input.value = normalizedNewValue;
        if (applyBtn) applyBtn.remove();
    } else {
        if (!applyBtn) {
            applyBtn = document.createElement('button');
            applyBtn.type = 'button';
            applyBtn.className = 'apply-btn';
            applyBtn.title = '应用搜索结果';
            applyBtn.textContent = '↵';
            wrapper.appendChild(applyBtn);
        }
        applyBtn.dataset.newValue = normalizedNewValue;
    }
}

function handleSearchBgmId() {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    bangumiSearchView.dataset.returnToAnimeId = animeId;
    switchView('bangumi-search-view');
    document.getElementById('bangumi-search-keyword').value = title;
    document.getElementById('bangumi-search-view-title').textContent = `为 "${title}" 搜索 Bangumi ID`;
    document.getElementById('bangumi-search-results-list').innerHTML = '';
}

function handleBackToEditAnime() {
    switchView('edit-anime-view');
}

async function handleBangumiSearchSubmit(e) {
    e.preventDefault();
    const keyword = document.getElementById('bangumi-search-keyword').value.trim();
    if (!keyword) return;
    const resultsList = document.getElementById('bangumi-search-results-list');
    resultsList.innerHTML = '<li>正在搜索...</li>';
    const searchButton = e.target.querySelector('button[type="submit"]');
    searchButton.disabled = true;
    try {
        const results = await apiFetch(`/api/bgm/search?keyword=${encodeURIComponent(keyword)}`);
        renderBangumiSearchResults(results);
    } catch (error) {
        resultsList.innerHTML = `<li class="error">搜索失败: ${error.message}</li>`;
    } finally {
        searchButton.disabled = false;
    }
}

function renderBangumiSearchResults(results) {
    const resultsList = document.getElementById('bangumi-search-results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>未找到匹配项。</li>';
        return;
    }
    results.forEach(result => {
        const li = document.createElement('li');
        li.innerHTML = `
            <div class="result-item-left">
                <img class="poster" src="${result.image_url || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.name}">
                <div class="info">
                    <div class="title-container">
                        <span class="id-tag">ID: ${result.id}</span>
                        <p class="title">${result.name}</p>
                    </div>
                    <p class="meta" title="${result.details || ''}">${result.details || ''}</p>
                </div>
            </div>
            <button class="select-btn">选择</button>
        `;
        li.querySelector('.select-btn').addEventListener('click', () => {
            result._source = 'bangumi';
            _currentSearchSelectionData = result;
            handleBackToEditAnime();
            setTimeout(applySearchSelectionData, 50);
        });
        resultsList.appendChild(li);
    });
}

function handleSearchTmdbId() {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    tmdbSearchView.dataset.returnToAnimeId = animeId;
    switchView('tmdb-search-view');
    document.getElementById('tmdb-search-keyword').value = title;
    document.getElementById('tmdb-search-view-title').textContent = `为 "${title}" 搜索 TMDB ID`;
    document.getElementById('tmdb-search-results-list').innerHTML = '';
}

async function handleTmdbSearchSubmit(e) {
    e.preventDefault();
    const keyword = document.getElementById('tmdb-search-keyword').value.trim();
    if (!keyword) return;
    const resultsList = document.getElementById('tmdb-search-results-list');
    resultsList.innerHTML = '<li>正在搜索...</li>';
    const searchButton = e.target.querySelector('button[type="submit"]');
    searchButton.disabled = true;
    try {
        const mediaType = document.getElementById('edit-anime-type').value === 'movie' ? 'movie' : 'tv';
        const searchUrl = `/api/tmdb/search/${mediaType}`;
        const results = await apiFetch(`${searchUrl}?keyword=${encodeURIComponent(keyword)}`);
        renderTmdbSearchResults(results);
    } catch (error) {
        resultsList.innerHTML = `<li class="error">搜索失败: ${error.message}</li>`;
    } finally {
        searchButton.disabled = false;
    }
}

function renderTmdbSearchResults(results) {
    const resultsList = document.getElementById('tmdb-search-results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>未找到匹配项。</li>';
        return;
    }
    results.forEach(result => {
        const li = document.createElement('li');
        li.innerHTML = `
            <div class="result-item-left">
                <img class="poster" src="${result.image_url || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.name}">
                <div class="info">
                    <div class="title-container">
                        <span class="id-tag">ID: ${result.id}</span>
                        <p class="title">${result.name}</p>
                    </div>
                    <p class="meta"></p> <!-- Add for consistent height -->
                </div>
            </div>
            <button class="select-btn">选择</button>
        `;
        li.querySelector('.select-btn').addEventListener('click', async () => {
            const mediaType = document.getElementById('edit-anime-type').value === 'movie' ? 'movie' : 'tv';
            try {
                const details = await apiFetch(`/api/tmdb/details/${mediaType}/${result.id}`);
                details._source = 'tmdb';
                details.main_title_from_search = result.name; // 将搜索时的主标题传递给详情对象
                if (tmdbSearchView.dataset.source === 'bulk-import') {
                    // If the search was triggered from the bulk import view
                    document.dispatchEvent(new CustomEvent('tmdb-search:selected-for-bulk', { detail: details }));
                } else {
                    // Default behavior for editing a single anime
                    _currentSearchSelectionData = details;
                    handleBackToEditAnime();
                    setTimeout(applySearchSelectionData, 50);
                }
            } catch (error) {
                alert(`获取TMDB详情失败: ${error.message}`);
            }
        });
        resultsList.appendChild(li);
    });
}

function handleSearchDoubanId() {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    doubanSearchView.dataset.returnToAnimeId = animeId;
    switchView('douban-search-view');
    document.getElementById('douban-search-keyword').value = title;
    document.getElementById('douban-search-view-title').textContent = `为 "${title}" 搜索 豆瓣 ID`;
    document.getElementById('douban-search-results-list').innerHTML = '';
}

async function handleDoubanSearchSubmit(e) {
    e.preventDefault();
    const keyword = document.getElementById('douban-search-keyword').value.trim();
    if (!keyword) return;
    const resultsList = document.getElementById('douban-search-results-list');
    resultsList.innerHTML = '<li>正在搜索...</li>';
    const searchButton = e.target.querySelector('button[type="submit"]');
    searchButton.disabled = true;
    try {
        const results = await apiFetch(`/api/douban/search?keyword=${encodeURIComponent(keyword)}`);
        renderDoubanSearchResults(results);
    } catch (error) {
        resultsList.innerHTML = `<li class="error">搜索失败: ${error.message}</li>`;
    } finally {
        searchButton.disabled = false;
    }
}

function renderDoubanSearchResults(results) {
    const resultsList = document.getElementById('douban-search-results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>未找到匹配项。</li>';
        return;
    }
    results.forEach(result => {
        const li = document.createElement('li');
        li.innerHTML = `
            <div class="result-item-left">
                <img class="poster" src="${result.image_url || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
                <div class="info">
                    <div class="title-container">
                        <span class="id-tag">ID: ${result.id}</span>
                        <p class="title">${result.title}</p>
                    </div>
                    <p class="meta" title="${result.details || ''}">${result.details}</p>
                </div>
            </div>
            <button class="select-btn">选择</button>
        `;
        li.querySelector('.select-btn').addEventListener('click', async () => {
            try {
                const details = await apiFetch(`/api/douban/details/${result.id}`);
                details._source = 'douban';
                _currentSearchSelectionData = details;
                handleBackToEditAnime();
                setTimeout(applySearchSelectionData, 50);
            } catch (error) {
                alert(`获取豆瓣详情失败: ${error.message}`);
            }
        });
        resultsList.appendChild(li);
    });
}

function handleSearchImdbId() {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    imdbSearchView.dataset.returnToAnimeId = animeId;
    switchView('imdb-search-view');
    document.getElementById('imdb-search-keyword').value = title;
    document.getElementById('imdb-search-view-title').textContent = `为 "${title}" 搜索 IMDb ID`;
    document.getElementById('imdb-search-results-list').innerHTML = '';
}

async function handleImdbSearchSubmit(e) {
    e.preventDefault();
    const keyword = document.getElementById('imdb-search-keyword').value.trim();
    if (!keyword) return;
    const resultsList = document.getElementById('imdb-search-results-list');
    resultsList.innerHTML = '<li>正在搜索...</li>';
    const searchButton = e.target.querySelector('button[type="submit"]');
    searchButton.disabled = true;
    try {
        const results = await apiFetch(`/api/imdb/search?keyword=${encodeURIComponent(keyword)}`);
        renderImdbSearchResults(results);
    } catch (error) {
        resultsList.innerHTML = `<li class="error">搜索失败: ${error.message}</li>`;
    } finally {
        searchButton.disabled = false;
    }
}

function renderImdbSearchResults(results) {
    const resultsList = document.getElementById('imdb-search-results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>未找到匹配项。</li>';
        return;
    }
    results.forEach(result => {
        const li = document.createElement('li');
        // Re-use the same structure as Douban/Bangumi
        li.innerHTML = `
            <div class="result-item-left">
                <img class="poster" src="${result.image_url || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
                <div class="info">
                    <div class="title-container">
                        <span class="id-tag">ID: ${result.id}</span>
                        <p class="title">${result.title}</p>
                    </div>
                    <p class="meta" title="${result.details || ''}">${result.details}</p>
                </div>
            </div>
            <button class="select-btn">选择</button>
        `;
        li.querySelector('.select-btn').addEventListener('click', async () => {
            try {
                const details = await apiFetch(`/api/imdb/details/${result.id}`);
                details._source = 'imdb';
                _currentSearchSelectionData = details;
                handleBackToEditAnime();
                setTimeout(applySearchSelectionData, 50);
            } catch (error) {
                alert(`获取 IMDb 详情失败: ${error.message}`);
            }
        });
        resultsList.appendChild(li);
    });
}

function handleSearchTvdbId() {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    tvdbSearchView.dataset.returnToAnimeId = animeId;
    switchView('tvdb-search-view');
    document.getElementById('tvdb-search-keyword').value = title;
    document.getElementById('tvdb-search-view-title').textContent = `为 "${title}" 搜索 TVDB ID`;
    document.getElementById('tvdb-search-results-list').innerHTML = '';
}

async function handleTvdbSearchSubmit(e) {
    e.preventDefault();
    const keyword = document.getElementById('tvdb-search-keyword').value.trim();
    if (!keyword) return;
    const resultsList = document.getElementById('tvdb-search-results-list');
    resultsList.innerHTML = '<li>正在搜索...</li>';
    const searchButton = e.target.querySelector('button[type="submit"]');
    searchButton.disabled = true;
    try {
        const results = await apiFetch(`/api/tvdb/search?keyword=${encodeURIComponent(keyword)}`);
        renderTvdbSearchResults(results);
    } catch (error) {
        resultsList.innerHTML = `<li class="error">搜索失败: ${error.message}</li>`;
    } finally {
        searchButton.disabled = false;
    }
}

function renderTvdbSearchResults(results) {
    const resultsList = document.getElementById('tvdb-search-results-list');
    resultsList.innerHTML = '';
    if (results.length === 0) {
        resultsList.innerHTML = '<li>未找到匹配项。</li>';
        return;
    }
    results.forEach(result => {
        const li = document.createElement('li');
        li.innerHTML = `
            <div class="result-item-left">
                <img class="poster" src="${result.image_url || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
                <div class="info">
                    <div class="title-container">
                        <span class="id-tag">ID: ${result.id}</span>
                        <p class="title">${result.title}</p>
                    </div>
                    <p class="meta" title="${result.details || ''}">${result.details}</p>
                </div>
            </div>
            <button class="select-btn">选择</button>
        `;
        li.querySelector('.select-btn').addEventListener('click', async () => {
            try {
                const details = await apiFetch(`/api/tvdb/details/${result.id}`);
                details._source = 'tvdb';
                _currentSearchSelectionData = details;
                handleBackToEditAnime();
                setTimeout(applySearchSelectionData, 50);
            } catch (error) {
                alert(`获取 TVDB 详情失败: ${error.message}`);
            }
        });
        resultsList.appendChild(li);
    });
}

async function handleSelectEgidBtnClick() {
    const tmdbId = editAnimeTmdbIdInput.value.trim();
    const animeTitle = document.getElementById('edit-anime-title').value.trim();
    if (!tmdbId) return;
    switchView('egid-view');
    egidView.dataset.tmdbId = tmdbId;
    document.getElementById('egid-view-title').textContent = `为 "${animeTitle}" 选择剧集组`;
    await loadAndRenderEpisodeGroups(tmdbId);
}

async function loadAndRenderEpisodeGroups(tmdbId) {
    const container = document.getElementById('egid-content-container');
    container.innerHTML = '<p>正在加载剧集组...</p>';
    try {
        const groups = await apiFetch(`/api/tmdb/tv/${tmdbId}/episode_groups`);
        if (groups.length === 0) {
            container.innerHTML = '<p>未找到任何剧集组。</p>';
            return;
        }
        const ul = document.createElement('ul');
        ul.className = 'results-list-style egid-group-list';
        groups.forEach(group => {
            const li = document.createElement('li');
            li.innerHTML = `
                <div class="info">
                    <p class="title">${group.name} (${group.group_count} 组, ${group.episode_count} 集)</p>
                    <p class="meta">${group.description || '无描述'}</p>
                </div>
                <div class="actions">
                    <button class="apply-group-btn">应用此组</button>
                    <button class="view-episodes-btn">查看分集</button>
                </div>
            `;
            li.querySelector('.apply-group-btn').addEventListener('click', () => {
                document.getElementById('edit-anime-egid').value = group.id;
                backToEditFromEgidBtn.click();
            });
            li.querySelector('.view-episodes-btn').addEventListener('click', () => {
                loadAndRenderEpisodeGroupDetails(group.id, group.name);
            });
            ul.appendChild(li);
        });
        container.innerHTML = '';
        container.appendChild(ul);
    } catch (error) {
        container.innerHTML = `<p class="error">加载剧集组失败: ${error.message}</p>`;
    }
}

async function loadAndRenderEpisodeGroupDetails(groupId, groupName) {
    const container = document.getElementById('egid-content-container');
    container.innerHTML = '<p>正在加载分集详情...</p>';
    document.getElementById('egid-view-title').textContent = `分集详情: ${groupName}`;
    const tmdbId = egidView.dataset.tmdbId;
    if (!tmdbId) {
        container.innerHTML = `<p class="error">错误：无法获取关联的 TMDB ID。</p>`;
        return;
    }
    try {
        const details = await apiFetch(`/api/tmdb/episode_group/${groupId}?tv_id=${tmdbId}`);
        const backBtn = document.createElement('button');
        backBtn.textContent = '< 返回剧集组列表';
        backBtn.addEventListener('click', () => {
            const animeTitle = document.getElementById('edit-anime-title').value.trim();
            document.getElementById('egid-view-title').textContent = `为 "${animeTitle}" 选择剧集组`;
            loadAndRenderEpisodeGroups(tmdbId);
        });
        backBtn.style.marginBottom = '20px';
        const ul = document.createElement('ul');
        ul.className = 'egid-detail-list';
        details.groups.forEach(season => {
            const seasonHeader = document.createElement('li');
            seasonHeader.className = 'season-header';
            seasonHeader.textContent = `${season.name} (Order: ${season.order})`;
            ul.appendChild(seasonHeader);
            season.episodes.forEach(ep => {
                const epItem = document.createElement('li');
                epItem.className = 'episode-item';
                epItem.innerHTML = `第${ep.order + 1}集（绝对：S${String(ep.season_number).padStart(2, '0')}E${String(ep.episode_number).padStart(2, '0')}）| ${ep.name || '无标题'}`;
                ul.appendChild(epItem);
            });
        });
        container.innerHTML = '';
        container.appendChild(backBtn);
        container.appendChild(ul);
    } catch (error) {
        container.innerHTML = `<p class="error">加载分集详情失败: ${error.message}</p>`;
    }
}

async function handleReassociateSourcesClick({ animeId, animeTitle }) {
    const sourceAnimeId = animeId;
    const sourceAnimeTitle = animeTitle;
    if (!sourceAnimeId) {
        alert("无法获取当前作品ID。");
        return;
    }
    switchView('reassociate-view');
    reassociateView.dataset.sourceAnimeId = sourceAnimeId;
    document.getElementById('reassociate-view-title').textContent = `为 "${sourceAnimeTitle}" 调整关联`;
    document.getElementById('reassociate-info-text').textContent = `此操作会将 "${sourceAnimeTitle}" (ID: ${sourceAnimeId}) 下的所有数据源移动到您选择的另一个作品条目下，然后删除原条目。`;
    const tableBody = document.querySelector('#reassociate-target-table tbody');
    tableBody.innerHTML = '<tr><td colspan="2">加载中...</td></tr>';
    try {
        const data = await apiFetch('/api/ui/library');
        renderReassociateTargets(data.animes, parseInt(sourceAnimeId, 10));
    } catch (error) {
        tableBody.innerHTML = `<tr><td colspan="2" class="error">加载目标列表失败: ${error.message}</td></tr>`;
    }
}

function updateReassociateButtonState() {
    const confirmBtn = document.getElementById('confirm-reassociation-btn');
    const selectedRow = document.querySelector('#reassociate-target-table tbody tr.selected');
    confirmBtn.disabled = !selectedRow;
}

function renderReassociateTargets(animes, sourceAnimeId) {
    const tableBody = document.querySelector('#reassociate-target-table tbody');
    tableBody.innerHTML = '';
    const potentialTargets = animes.filter(anime => anime.animeId !== sourceAnimeId);    if (potentialTargets.length === 0) {
        tableBody.innerHTML = '<tr><td>没有其他可用的目标作品。</td></tr>';
        return;
    }
    potentialTargets.forEach(anime => {
        const row = tableBody.insertRow();
        row.dataset.title = anime.title.toLowerCase();
        row.dataset.targetId = anime.animeId;
        row.dataset.targetTitle = anime.title;
        row.style.cursor = 'pointer';

        const displayType = typeMap[anime.type] || anime.type;

        row.innerHTML = `
            <td><strong>${anime.title}</strong> (ID: ${anime.animeId}, 季: ${anime.season}, 类型: ${displayType})</td>
        `;
    });
}

function handleReassociateSearch() {
    const searchTerm = document.getElementById('reassociate-search-input').value.toLowerCase();
    const rows = document.querySelectorAll('#reassociate-target-table tbody tr');
    rows.forEach(row => {
        const title = row.dataset.title || '';
        row.style.display = title.includes(searchTerm) ? '' : 'none';
    });
}

async function handleReassociateConfirm() {
    const selectedRow = document.querySelector('#reassociate-target-table tbody tr.selected');
    if (!selectedRow) {
        alert('请选择一个目标作品。');
        return;
    }
    const sourceAnimeId = parseInt(reassociateView.dataset.sourceAnimeId, 10);
    const targetAnimeId = parseInt(selectedRow.dataset.targetId, 10);
    const targetAnimeTitle = selectedRow.dataset.targetTitle;

    if (confirm(`您确定要将当前作品的所有数据源关联到 "${targetAnimeTitle}" (ID: ${targetAnimeId}) 吗？\n\n此操作不可撤销！`)) {
        try {
            await apiFetch(`/api/ui/library/anime/${sourceAnimeId}/reassociate`, {
                method: 'POST',
                body: JSON.stringify({ target_anime_id: targetAnimeId })
            });
            alert("关联成功！");
            document.querySelector('.nav-link[data-view="library-view"]').click();
        } catch (error) {
            alert(`关联失败: ${error.message}`);
        }
    }
}

function showEditEpisodeView({ episode, sourceId, animeTitle, animeId }) {
    switchView('edit-episode-view');
    document.getElementById('edit-episode-id').value = episode.id;
    document.getElementById('edit-episode-title').value = episode.title;
    document.getElementById('edit-episode-index').value = episode.episode_index;
    document.getElementById('edit-episode-url').value = episode.source_url || '';
    document.getElementById('edit-episode-source-id').value = sourceId;
    document.getElementById('edit-episode-anime-title').value = animeTitle;
    document.getElementById('edit-episode-anime-id').value = animeId;
}

async function handleEditEpisodeSave(e) {
    e.preventDefault();
    const episodeId = document.getElementById('edit-episode-id').value;
    const payload = {
        title: document.getElementById('edit-episode-title').value,
        episode_index: parseInt(document.getElementById('edit-episode-index').value, 10),
        source_url: document.getElementById('edit-episode-url').value
    };
    const saveButton = e.target.querySelector('button[type="submit"]');
    saveButton.disabled = true;
    saveButton.textContent = '保存中...';
    try {
        await apiFetch(`/api/ui/library/episode/${episodeId}`, { method: 'PUT', body: JSON.stringify(payload) });
        alert("分集信息更新成功！");
        document.getElementById('back-to-episodes-from-edit-btn').click();
    } catch (error) {
        alert(`更新失败: ${(error.message || error)}`);
    } finally {
        saveButton.disabled = false;
        saveButton.textContent = '保存更改';
    }
}

export function setupEditAnimeEventListeners() {
    initializeElements();
    document.addEventListener('show:edit-anime', (e) => showEditAnimeView(e.detail.animeId));
    document.addEventListener('show:reassociate-view', (e) => handleReassociateSourcesClick(e.detail));
    document.addEventListener('show:edit-episode', (e) => showEditEpisodeView(e.detail));

    // Listen for search request from bulk import view
    document.addEventListener('show:tmdb-search-for-bulk', (e) => {
        switchView('tmdb-search-view');
        tmdbSearchView.dataset.source = 'bulk-import'; // Set context
        document.getElementById('tmdb-search-keyword').value = e.detail.keyword;
        document.getElementById('tmdb-search-view-title').textContent = `为批量导入搜索 TMDB ID`;
    });

    editAnimeForm.addEventListener('submit', handleEditAnimeSave);
    editAnimeTypeSelect.addEventListener('change', handleAnimeTypeChange);
    editAnimeTmdbIdInput.addEventListener('input', updateEgidSelectButtonState);
    document.getElementById('back-to-library-from-edit-btn').addEventListener('click', () => {
        switchView('library-view');
        // After going back, we should refresh the library list
        document.dispatchEvent(new CustomEvent('viewchange', { detail: { viewId: 'library-view' } }));
    });
    document.getElementById('search-bgmid-btn').addEventListener('click', handleSearchBgmId);
    document.getElementById('search-tmdbid-btn').addEventListener('click', handleSearchTmdbId);
    document.getElementById('search-doubanid-btn').addEventListener('click', handleSearchDoubanId);
    document.getElementById('search-tvdbid-btn').addEventListener('click', handleSearchTvdbId);
    document.getElementById('search-imdbid-btn').addEventListener('click', handleSearchImdbId);
    document.getElementById('select-egid-btn').addEventListener('click', handleSelectEgidBtnClick);

    backToEditAnimeFromBgmSearchBtn.addEventListener('click', handleBackToEditAnime);
    document.getElementById('bangumi-search-form').addEventListener('submit', handleBangumiSearchSubmit);
    backToEditAnimeFromDoubanSearchBtn.addEventListener('click', handleBackToEditAnime);
    document.getElementById('douban-search-form').addEventListener('submit', handleDoubanSearchSubmit);
    backToEditAnimeFromTmdbSearchBtn.addEventListener('click', handleBackToEditAnime);
    document.getElementById('tmdb-search-form').addEventListener('submit', handleTmdbSearchSubmit);
    backToEditAnimeFromTvdbSearchBtn.addEventListener('click', handleBackToEditAnime);
    document.getElementById('tvdb-search-form').addEventListener('submit', handleTvdbSearchSubmit);
    backToEditAnimeFromImdbSearchBtn.addEventListener('click', handleBackToEditAnime);
    document.getElementById('imdb-search-form').addEventListener('submit', handleImdbSearchSubmit);
    backToEditFromEgidBtn.addEventListener('click', () => switchView('edit-anime-view'));

    backToDetailFromReassociateBtn.addEventListener('click', () => {
        const sourceAnimeId = parseInt(reassociateView.dataset.sourceAnimeId, 10);
        if (sourceAnimeId) {
            document.dispatchEvent(new CustomEvent('show:anime-detail', { detail: { animeId: sourceAnimeId } }));
        } else {
            switchView('library-view');
        }
    });
    document.getElementById('confirm-reassociation-btn').addEventListener('click', handleReassociateConfirm);
    const reassociateTableBody = document.querySelector('#reassociate-target-table tbody');
    reassociateTableBody.addEventListener('click', (e) => {
        const clickedRow = e.target.closest('tr');
        if (!clickedRow || !clickedRow.dataset.targetId) return;
        // A more standard single-select list behavior
        reassociateTableBody.querySelectorAll('tr.selected').forEach(r => r.classList.remove('selected'));
        clickedRow.classList.add('selected');
        updateReassociateButtonState();
    });
    document.getElementById('reassociate-search-input').addEventListener('input', handleReassociateSearch);

    editEpisodeForm.addEventListener('submit', handleEditEpisodeSave);
    document.getElementById('back-to-episodes-from-edit-btn').addEventListener('click', () => {
        const sourceId = document.getElementById('edit-episode-source-id').value;
        const animeTitle = document.getElementById('edit-episode-anime-title').value;
        const animeId = document.getElementById('edit-episode-anime-id').value;
        // Dispatch an event that library.js can listen to
        document.dispatchEvent(new CustomEvent('show:episode-list', { detail: { sourceId, animeTitle, animeId } }));
    });

    editAnimeForm.addEventListener('click', (e) => {
        if (e.target.classList.contains('apply-btn')) {
            const wrapper = e.target.parentElement;
            const input = wrapper.querySelector('input');
            if (input) {
                input.value = e.target.dataset.newValue || '';
                e.target.remove();
            }
        }
    });
}
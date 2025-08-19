import { apiFetch } from '../api.js';
import { switchView } from '../ui.js';

// --- State ---
let _currentSearchSelectionData = null;
let _initialAnimeDetails = null; // Store initial data to check for changes

// --- Constants ---
const typeMap = {
    'tv_series': '电视节目',
    'movie': '电影/剧场版',
    'ova': 'OVA',
    'other': '其他'
};

// --- DOM Elements ---
let editAnimeView, editAnimeForm, editAnimeTypeSelect, selectEgidBtn, editAnimeTmdbIdInput;
let bangumiSearchView, tmdbSearchView, doubanSearchView, imdbSearchView, tvdbSearchView, egidView, reassociateView, saveAndContinueBtn;
let backToEditAnimeFromBgmSearchBtn, backToEditAnimeFromTmdbSearchBtn, backToEditAnimeFromDoubanSearchBtn, backToEditAnimeFromImdbSearchBtn, backToEditAnimeFromTvdbSearchBtn, backToEditFromEgidBtn, backToDetailFromReassociateBtn;
let editEpisodeView, editEpisodeForm;

function initializeElements() {
    editAnimeView = document.getElementById('edit-anime-view');
    editAnimeForm = document.getElementById('edit-anime-form');
    editAnimeTypeSelect = document.getElementById('edit-anime-type');
    selectEgidBtn = document.getElementById('select-egid-btn');
    editAnimeTmdbIdInput = document.getElementById('edit-anime-tmdbid');
    saveAndContinueBtn = document.getElementById('save-and-continue-btn');

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
        _initialAnimeDetails = details; // Store initial state
    } catch (error) {
        alert(`加载编辑信息失败: ${error.message}`);
        switchView('library-view');
    } finally {
        editAnimeForm.querySelector('button[type="submit"]').disabled = false;
    }
}

function populateEditForm(details) {
    document.getElementById('edit-anime-id').value = details.animeId;
    document.getElementById('edit-anime-title').value = details.title;
    editAnimeTypeSelect.value = details.type;
    document.getElementById('edit-anime-poster-url').value = details.imageUrl || '';
    document.getElementById('edit-anime-season').value = details.season;
    document.getElementById('edit-anime-episode-count').value = details.episodeCount || '';
    editAnimeTmdbIdInput.value = details.tmdbId || '';
    document.getElementById('edit-anime-egid').value = details.tmdbEpisodeGroupId || '';
    document.getElementById('edit-anime-bgmid').value = details.bangumiId || '';
    document.getElementById('edit-anime-tvdbid').value = details.tvdbId || '';
    document.getElementById('edit-anime-doubanid').value = details.doubanId || '';
    document.getElementById('edit-anime-imdbid').value = details.imdbId || '';
    document.getElementById('edit-anime-name-en').value = details.nameEn || '';
    document.getElementById('edit-anime-name-jp').value = details.nameJp || '';
    document.getElementById('edit-anime-name-romaji').value = details.nameRomaji || '';
    document.getElementById('edit-anime-alias-cn-1').value = details.aliasCn1 || '';
    document.getElementById('edit-anime-alias-cn-2').value = details.aliasCn2 || '';
    document.getElementById('edit-anime-alias-cn-3').value = details.aliasCn3 || '';
    handleAnimeTypeChange();
    updateEgidSelectButtonState();
}

async function handleEditAnimeSave(e, navigateBackOnSuccess = true) {
    e.preventDefault();
    const animeId = document.getElementById('edit-anime-id').value;
    const payload = {
        title: document.getElementById('edit-anime-title').value,
        type: document.getElementById('edit-anime-type').value,
        season: parseInt(document.getElementById('edit-anime-season').value, 10),
        imageUrl: document.getElementById('edit-anime-poster-url').value.trim() || null,
        episodeCount: document.getElementById('edit-anime-episode-count').value ? parseInt(document.getElementById('edit-anime-episode-count').value, 10) : null,
        tmdbId: document.getElementById('edit-anime-tmdbid').value || null,
        tmdbEpisodeGroupId: document.getElementById('edit-anime-egid').value || null,
        bangumiId: document.getElementById('edit-anime-bgmid').value || null,
        tvdbId: document.getElementById('edit-anime-tvdbid').value || null,
        doubanId: document.getElementById('edit-anime-doubanid').value || null,
        imdbId: document.getElementById('edit-anime-imdbid').value || null,
        nameEn: document.getElementById('edit-anime-name-en').value || null,
        nameJp: document.getElementById('edit-anime-name-jp').value || null,
        nameRomaji: document.getElementById('edit-anime-name-romaji').value || null,
        aliasCn1: document.getElementById('edit-anime-alias-cn-1').value || null,
        aliasCn2: document.getElementById('edit-anime-alias-cn-2').value || null,
        aliasCn3: document.getElementById('edit-anime-alias-cn-3').value || null,
    };

    const saveButton = e.target;
    saveButton.disabled = true;
    saveButton.textContent = '保存中...';

    try {
        await apiFetch(`/api/ui/library/anime/${animeId}`, { method: 'PUT', body: JSON.stringify(payload) });
        alert("信息更新成功！");
        if (navigateBackOnSuccess) {
            document.getElementById('back-to-library-from-edit-btn').click();
        }
        return true; // Indicate success
    } catch (error) {
        alert(`更新失败: ${(error.message || error)}`);
        return false; // Indicate failure
    } finally {
        saveButton.disabled = false;
        // Restore original button text
        if (saveButton.id === 'save-and-return-btn') saveButton.textContent = '保存后返回';
        else if (saveButton.id === 'save-and-continue-btn') saveButton.textContent = '保存后继续';
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
    const egidWrapper = egidInput.closest('.input-with-icons');
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
            document.getElementById('edit-anime-tmdbid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-tvdbid', data.tvdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            updateFieldWithApplyLogic('edit-anime-name-romaji', data.name_romaji);
            _applyAliases(data.aliases_cn, data.main_title_from_search);
            break;
        case 'imdb':
            document.getElementById('edit-anime-imdbid').value = data.id || '';
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            _applyAliases(data.aliases_cn, data.name_en);
            break;
        case 'tvdb':
            document.getElementById('edit-anime-tvdbid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            _applyAliases(data.aliases_cn, data.name_en);
            break;
        case 'douban':
            document.getElementById('edit-anime-doubanid').value = data.id || '';
            updateFieldWithApplyLogic('edit-anime-imdbid', data.imdb_id);
            updateFieldWithApplyLogic('edit-anime-name-en', data.name_en);
            if (containsJapanese(data.name_jp)) {
                updateFieldWithApplyLogic('edit-anime-name-jp', data.name_jp);
            }
            _applyAliases(data.aliases_cn, (data.aliases_cn && data.aliases_cn.length > 0) ? data.aliases_cn[0] : '');
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

async function handleDirectSearch(source) {
    const inputId = `edit-anime-${source}id`;
    const input = document.getElementById(inputId);
    const currentId = input.value.trim();

    if (!currentId) {
        alert(`请输入 ${source.toUpperCase()} ID 后再进行直搜。`);
        return;
    }

    const initialId = _initialAnimeDetails ? String(_initialAnimeDetails[`${source}Id`] || '') : '';
    if (currentId !== initialId) {
        alert(`ID 已更改但尚未保存。\n请先点击“保存后继续”以保存更改，然后再进行直搜。`);
        return;
    }

    const detailsUrlMap = {
        bangumi: `/api/ui/bangumi/subjects/${currentId}`,
        douban: `/api/ui/douban/details/${currentId}`,
        tvdb: `/api/ui/tvdb/details/${currentId}`,
        imdb: `/api/ui/imdb/details/${currentId}`
    };

    let detailsUrl = detailsUrlMap[source];
    if (source === 'tmdb') {
        const mediaType = document.getElementById('edit-anime-type').value === 'movie' ? 'movie' : 'tv';
        detailsUrl = `/api/ui/tmdb/details/${mediaType}/${currentId}`;
    }

    if (!detailsUrl) {
        alert(`不支持对 ${source} 进行直搜。`);
        return;
    }

    try {
        const details = await apiFetch(detailsUrl);
        details._source = source;
        _currentSearchSelectionData = details;
        applySearchSelectionData();
        alert(`${source.toUpperCase()} 信息获取成功，请检查并应用建议的别名。`);
    } catch (error) {
        alert(`获取 ${source.toUpperCase()} 详情失败: ${error.message}`);
    }
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
                <img class="poster" src="${result.imageUrl || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.name}">
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
                <img class="poster" src="${result.imageUrl || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.name}">
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
            const seasonInput = document.getElementById('edit-anime-season');
            const season = seasonInput.value ? parseInt(seasonInput.value, 10) : null;
            try {
                let apiUrl = `/api/tmdb/details/${mediaType}/${result.id}`;
                if (season) {
                    apiUrl += `?season=${season}`;
                }
                const details = await apiFetch(apiUrl);
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
                <img class="poster" src="${result.imageUrl || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
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
                <img class="poster" src="${result.imageUrl || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
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
                <img class="poster" src="${result.imageUrl || '/static/placeholder.png'}" referrerpolicy="no-referrer" alt="${result.title}">
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

function handleSearchAction(source) {
    const title = document.getElementById('edit-anime-title').value;
    const animeId = document.getElementById('edit-anime-id').value;
    let searchView, keywordInput, viewTitle;

    switch (source) {
        case 'bangumi':
            searchView = bangumiSearchView;
            keywordInput = document.getElementById('bangumi-search-keyword');
            viewTitle = document.getElementById('bangumi-search-view-title');
            break;
        case 'tmdb':
            searchView = tmdbSearchView;
            keywordInput = document.getElementById('tmdb-search-keyword');
            viewTitle = document.getElementById('tmdb-search-view-title');
            break;
        // ... add cases for douban, tvdb, imdb
        case 'douban': searchView = doubanSearchView; keywordInput = document.getElementById('douban-search-keyword'); viewTitle = document.getElementById('douban-search-view-title'); break;
        case 'tvdb': searchView = tvdbSearchView; keywordInput = document.getElementById('tvdb-search-keyword'); viewTitle = document.getElementById('tvdb-search-view-title'); break;
        case 'imdb': searchView = imdbSearchView; keywordInput = document.getElementById('imdb-search-keyword'); viewTitle = document.getElementById('imdb-search-view-title'); break;
        default: return;
    }

    searchView.dataset.returnToAnimeId = animeId;
    switchView(searchView.id);
    keywordInput.value = title;
    viewTitle.textContent = `为 "${title}" 搜索 ${source.toUpperCase()} ID`;
    document.getElementById(`${source}-search-results-list`).innerHTML = '';
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
                body: JSON.stringify({ targetAnimeId: targetAnimeId })
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
    document.getElementById('edit-episode-id').value = episode.episodeId;
    document.getElementById('edit-episode-title').value = episode.title;
    document.getElementById('edit-episode-original-index').value = episode.episodeIndex;
    document.getElementById('edit-episode-index').value = episode.episodeIndex;
    document.getElementById('edit-episode-url').value = episode.sourceUrl || '';
    document.getElementById('edit-episode-source-id').value = sourceId;
    document.getElementById('edit-episode-anime-title').value = animeTitle;
    document.getElementById('edit-episode-anime-id').value = animeId;
}

async function handleEditEpisodeSave(e) {
    e.preventDefault();
    const episodeId = document.getElementById('edit-episode-id').value;
    const payload = {
        title: document.getElementById('edit-episode-title').value,
        episodeIndex: parseInt(document.getElementById('edit-episode-index').value, 10),
        sourceUrl: document.getElementById('edit-episode-url').value,
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

async function handleRefreshPoster() {
    const animeId = document.getElementById('edit-anime-id').value;
    const newUrl = document.getElementById('edit-anime-poster-url').value.trim();
    if (!animeId || !newUrl) {
        alert("请输入有效的海报URL。");
        return;
    }
    const refreshBtn = document.getElementById('refresh-poster-btn');
    refreshBtn.textContent = '...';
    refreshBtn.disabled = true;
    try {
        await apiFetch(`/api/ui/library/anime/${animeId}/refresh-poster`, {
            method: 'POST', body: JSON.stringify({ image_url: newUrl })
        });
        alert("海报已刷新并缓存成功！");
    } catch (error) {
        alert(`刷新海报失败: ${error.message}`);
    } finally {
        refreshBtn.textContent = '🔄';
        refreshBtn.disabled = false;
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
    document.getElementById('refresh-poster-btn').addEventListener('click', handleRefreshPoster);

    editAnimeForm.addEventListener('click', async (e) => {
        const button = e.target.closest('.icon-btn');
        if (!button) return;

        const action = button.dataset.action;
        const source = button.dataset.source;

        if (action === 'direct-search') {
            await handleDirectSearch(source);
        } else if (action === 'search') {
            handleSearchAction(source);
        } else if (action === 'select-egid') {
            const tmdbId = editAnimeTmdbIdInput.value.trim();
            const animeTitle = document.getElementById('edit-anime-title').value.trim();
            if (!tmdbId) return;
            switchView('egid-view');
            egidView.dataset.tmdbId = tmdbId;
            document.getElementById('egid-view-title').textContent = `为 "${animeTitle}" 选择剧集组`;
            await loadAndRenderEpisodeGroups(tmdbId);
        }
    });

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

    saveAndContinueBtn.addEventListener('click', async (e) => {
        const success = await handleEditAnimeSave(e, false); // Pass false to not navigate away
        if (success) {
            const animeId = document.getElementById('edit-anime-id').value;
            // Reload the view to get fresh data and update the initial state
            await showEditAnimeView(animeId);
        }
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
import { apiFetch } from '../api.js';

// Helper function to normalize image URLs to HTTPS, which is more robust for cross-origin images.
function normalizeImageUrl(url) {
    if (!url) {
        return '/static/placeholder.png';
    }
    let newUrl = String(url);
    if (newUrl.startsWith('//')) {
        newUrl = 'https:' + newUrl;
    }
    return newUrl.replace(/^http:/, 'https:');
}

// DOM Elements
let sourcesSubNav, sourcesSubViews;
let danmakuSourcesList, saveDanmakuSourcesBtn, toggleDanmakuSourceBtn, moveDanmakuSourceUpBtn, moveDanmakuSourceDownBtn;
let metadataSourcesList, saveMetadataSourcesBtn, moveMetadataSourceUpBtn, moveMetadataSourceDownBtn;

function initializeElements() {
    sourcesSubNav = document.querySelector('#sources-view .settings-sub-nav');
    sourcesSubViews = document.querySelectorAll('#sources-view .settings-subview');

    danmakuSourcesList = document.getElementById('danmaku-sources-list');
    saveDanmakuSourcesBtn = document.getElementById('save-danmaku-sources-btn');
    toggleDanmakuSourceBtn = document.getElementById('toggle-danmaku-source-btn');
    moveDanmakuSourceUpBtn = document.getElementById('move-danmaku-source-up-btn');
    moveDanmakuSourceDownBtn = document.getElementById('move-danmaku-source-down-btn');

    metadataSourcesList = document.getElementById('metadata-sources-list');
    saveMetadataSourcesBtn = document.getElementById('save-metadata-sources-btn');
    moveMetadataSourceUpBtn = document.getElementById('move-metadata-source-up-btn');
    moveMetadataSourceDownBtn = document.getElementById('move-metadata-source-down-btn');
}

function handleSourcesSubNav(e) {
    const subNavBtn = e.target.closest('.sub-nav-btn');
    if (!subNavBtn) return;

    const subViewId = subNavBtn.getAttribute('data-subview');
    if (!subViewId) return;

    sourcesSubNav.querySelectorAll('.sub-nav-btn').forEach(btn => btn.classList.remove('active'));
    subNavBtn.classList.add('active');

    sourcesSubViews.forEach(view => view.classList.add('hidden'));
    const targetSubView = document.getElementById(subViewId);
    if (targetSubView) targetSubView.classList.remove('hidden');

    if (subViewId === 'danmaku-sources-subview') loadDanmakuSources();
    if (subViewId === 'metadata-sources-subview') loadMetadataSources();
}

async function loadDanmakuSources() {
    if (!danmakuSourcesList) return;
    danmakuSourcesList.innerHTML = '<li>加载中...</li>';
    try {
        const settings = await apiFetch('/api/ui/scrapers');
        renderDanmakuSources(settings);
        // 渲染后，如果Bilibili源存在，则更新其登录状态
        if (document.getElementById('bili-status-on-source-list')) {
            updateBiliStatusOnSourcesView();
        }
    } catch (error) {
        danmakuSourcesList.innerHTML = `<li class="error">加载失败: ${(error.message || error)}</li>`;
    }
}

function renderDanmakuSources(settings) {
    danmakuSourcesList.innerHTML = '';
    settings.forEach(setting => {
        const li = document.createElement('li');
        li.dataset.providerName = setting.provider_name;
        li.dataset.isEnabled = setting.is_enabled;

        const nameSpan = document.createElement('span');
        nameSpan.className = 'source-name';
        nameSpan.textContent = setting.provider_name;
        li.appendChild(nameSpan);

        // 新增：为Bilibili源添加一个专门的状态显示区域
        if (setting.provider_name === 'bilibili') {
            const biliStatusDiv = document.createElement('div');
            biliStatusDiv.id = 'bili-status-on-source-list';
            biliStatusDiv.className = 'source-login-status';
            biliStatusDiv.textContent = '正在检查...';
            li.appendChild(biliStatusDiv);
        }

        // 如果源有可配置字段或支持日志记录，则显示配置按钮
        if ((setting.configurable_fields && Object.keys(setting.configurable_fields).length > 0) || setting.is_loggable) {
            const configBtn = document.createElement('button');
            configBtn.className = 'action-btn config-btn';
            configBtn.title = `配置 ${setting.provider_name}`;
            configBtn.textContent = '⚙️';
            configBtn.dataset.action = 'configure';
            configBtn.dataset.providerName = setting.provider_name;
            // 将字段信息存储为JSON字符串以便后续使用
            configBtn.dataset.fields = JSON.stringify(setting.configurable_fields);
            configBtn.dataset.isLoggable = setting.is_loggable;
            li.appendChild(configBtn);
        }

        const statusIcon = document.createElement('span');
        statusIcon.className = 'status-icon';
        statusIcon.textContent = setting.is_enabled ? '✅' : '❌';
        li.appendChild(statusIcon);

        li.addEventListener('click', (e) => {
            // 如果点击的是配置按钮，则不触发选中事件
            if (e.target.closest('.config-btn')) return;
            danmakuSourcesList.querySelectorAll('li').forEach(item => item.classList.remove('selected'));
            li.classList.add('selected');
        });
        danmakuSourcesList.appendChild(li);
    });
}

async function handleSaveDanmakuSources() {
    const settingsToSave = [];
    danmakuSourcesList.querySelectorAll('li').forEach((li, index) => {
        settingsToSave.push({
            provider_name: li.dataset.providerName,
            is_enabled: li.dataset.isEnabled === 'true',
            display_order: index + 1,
        });
    });
    try {
        saveDanmakuSourcesBtn.disabled = true;
        saveDanmakuSourcesBtn.textContent = '保存中...';
        await apiFetch('/api/ui/scrapers', {
            method: 'PUT',
            body: JSON.stringify(settingsToSave),
        });
        alert('搜索源设置已保存！');
        loadDanmakuSources();
    } catch (error) {
        alert(`保存失败: ${(error.message || error)}`);
    } finally {
        saveDanmakuSourcesBtn.disabled = false;
        saveDanmakuSourcesBtn.textContent = '保存设置';
    }
}

function handleToggleDanmakuSource() {
    const selected = danmakuSourcesList.querySelector('li.selected');
    if (!selected) return;
    const isEnabled = selected.dataset.isEnabled === 'true';
    selected.dataset.isEnabled = !isEnabled;
    selected.querySelector('.status-icon').textContent = !isEnabled ? '✅' : '❌';
}

function handleMoveDanmakuSource(direction) {
    const selected = danmakuSourcesList.querySelector('li.selected');
    if (!selected) return;
    if (direction === 'up' && selected.previousElementSibling) {
        danmakuSourcesList.insertBefore(selected, selected.previousElementSibling);
    } else if (direction === 'down' && selected.nextElementSibling) {
        danmakuSourcesList.insertBefore(selected.nextElementSibling, selected);
    }
}

async function loadMetadataSources() {
    if (!metadataSourcesList) return;
    metadataSourcesList.innerHTML = '<li>加载中...</li>';
    try {
        const sources = await apiFetch('/api/ui/metadata-sources');
        renderMetadataSources(sources);
    } catch (error) {
        metadataSourcesList.innerHTML = `<li class="error">加载失败: ${(error.message || error)}</li>`;
    }
}

function renderMetadataSources(sources) {
    metadataSourcesList.innerHTML = '';
    sources.forEach(setting => {
        const li = document.createElement('li');
        li.dataset.providerName = setting.provider_name;
        li.dataset.isEnabled = setting.is_enabled;
        li.dataset.isAuxSearchEnabled = setting.is_aux_search_enabled;

        // Auxiliary Search Checkbox
        const auxSearchCheckbox = document.createElement('input');
        auxSearchCheckbox.type = 'checkbox';
        auxSearchCheckbox.className = 'aux-search-checkbox';
        auxSearchCheckbox.checked = setting.is_aux_search_enabled;
        auxSearchCheckbox.title = '启用作为辅助搜索源';
        if (setting.provider_name === 'tmdb') {
            auxSearchCheckbox.disabled = true;
            auxSearchCheckbox.title = 'TMDB 是必需的辅助搜索源';
        }
        auxSearchCheckbox.addEventListener('change', (e) => {
            li.dataset.isAuxSearchEnabled = e.target.checked;
        });
        li.appendChild(auxSearchCheckbox);

        const nameSpan = document.createElement('span');
        nameSpan.className = 'source-name';
        nameSpan.textContent = setting.provider_name.toUpperCase();
        li.appendChild(nameSpan);

        const statusText = document.createElement('span');
        statusText.className = 'source-status-text';
        statusText.textContent = setting.status;
        li.appendChild(statusText);

        const enabledIcon = document.createElement('span');
        enabledIcon.className = 'status-icon';
        enabledIcon.textContent = setting.is_enabled ? '✅' : '❌';
        // 新增：让状态图标本身可点击以切换状态，更直观
        enabledIcon.style.cursor = 'pointer';
        enabledIcon.title = '点击切换启用/禁用状态';
        enabledIcon.addEventListener('click', (e) => {
            e.stopPropagation(); // 防止触发li的选中事件
            const isEnabled = li.dataset.isEnabled === 'true';
            li.dataset.isEnabled = !isEnabled;
            enabledIcon.textContent = !isEnabled ? '✅' : '❌';
        });
        li.appendChild(enabledIcon);

        li.addEventListener('click', (e) => {
            if (e.target === enabledIcon) return; // 如果点击的是图标，则不执行选中
            metadataSourcesList.querySelectorAll('li').forEach(item => item.classList.remove('selected'));
            li.classList.add('selected');
        });
        metadataSourcesList.appendChild(li);
    });
}

function handleMetadataSourceAction(direction) {
    const selected = metadataSourcesList.querySelector('li.selected');
    if (!selected) return;
    if (direction === 'up' && selected.previousElementSibling) {
        metadataSourcesList.insertBefore(selected, selected.previousElementSibling);
    } else if (direction === 'down' && selected.nextElementSibling) {
        metadataSourcesList.insertBefore(selected.nextElementSibling, selected);
    }
}

async function handleSaveMetadataSources() {
    const settingsToSave = [];
    metadataSourcesList.querySelectorAll('li').forEach((li, index) => {
        settingsToSave.push({
            provider_name: li.dataset.providerName,
            is_enabled: li.dataset.isEnabled === 'true',
            is_aux_search_enabled: li.dataset.isAuxSearchEnabled === 'true',
            display_order: index + 1,
        });
    });
    try {
        saveMetadataSourcesBtn.disabled = true;
        saveMetadataSourcesBtn.textContent = '保存中...';
        await apiFetch('/api/ui/metadata-sources', { method: 'PUT', body: JSON.stringify(settingsToSave) });
        alert('元信息搜索源设置已保存！');
    } catch (error) {
        alert(`保存失败: ${(error.message || error)}`);
    } finally {
        saveMetadataSourcesBtn.disabled = false;
        saveMetadataSourcesBtn.textContent = '保存设置';
    }
}

async function handleDanmakuSourceAction(e) {
    const button = e.target.closest('.config-btn');
    if (!button || button.dataset.action !== 'configure') return;

    const providerName = button.dataset.providerName;
    const isLoggable = button.dataset.isLoggable === 'true';
    const fields = JSON.parse(button.dataset.fields);
    
    showScraperConfigModal(providerName, fields, isLoggable);
}

let currentProviderForModal = null;

function showScraperConfigModal(providerName, fields, isLoggable) {
    currentProviderForModal = providerName;
    const modal = document.getElementById('generic-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');

    modalTitle.textContent = `配置: ${providerName}`;
    modalBody.innerHTML = '<p>加载中...</p>';
    modal.classList.remove('hidden');

    apiFetch(`/api/ui/scrapers/${providerName}/config`)
        .then(currentConfig => {
            modalBody.innerHTML = ''; // 清空加载提示

            // 渲染文本字段（如果存在）
            if (fields && Object.keys(fields).length > 0) {
                const helpText = document.createElement('p');
                helpText.className = 'modal-help-text';
                if (providerName === 'gamer') {
                    helpText.innerHTML = `仅当无法正常搜索时才需要填写。请先尝试清空配置并保存，如果问题依旧，再从 <a href="https://ani.gamer.com.tw/" target="_blank" rel="noopener noreferrer">巴哈姆特动画疯</a> 获取最新的 User-Agent 和 Cookie。`;
                } else {
                    helpText.textContent = `请为 ${providerName} 源填写以下配置信息。`;
                }
                modalBody.appendChild(helpText);

                Object.entries(fields).forEach(([key, label]) => {
                    const value = currentConfig[key] || '';
                    const formRow = document.createElement('div');
                    formRow.className = 'form-row';
                    
                    const labelEl = document.createElement('label');
                    labelEl.htmlFor = `config-input-${key}`;
                    labelEl.textContent = label;
                    
                    const isCookie = key.toLowerCase().includes('cookie');
                    const inputEl = document.createElement(isCookie ? 'textarea' : 'input');
                    if (!isCookie) inputEl.type = 'text';
                    inputEl.id = `config-input-${key}`;
                    inputEl.name = key;
                    inputEl.value = value;
                    if (isCookie) inputEl.rows = 4;
                    
                    formRow.appendChild(labelEl);
                    formRow.appendChild(inputEl);
                    modalBody.appendChild(formRow);
                });
            }

            // 渲染日志开关（如果支持）
            if (isLoggable) {
                const logKey = `scraper_${providerName}_log_responses`;
                const isEnabled = currentConfig[logKey] === 'true';

                const logSection = document.createElement('div');
                logSection.className = 'form-row';
                logSection.style.marginTop = '20px';
                logSection.style.paddingTop = '15px';
                logSection.style.borderTop = '1px solid var(--border-color)';

                const labelEl = document.createElement('label');
                labelEl.htmlFor = 'config-input-log-responses';
                labelEl.textContent = '记录原始响应';
                
                const inputEl = document.createElement('input');
                inputEl.type = 'checkbox';
                inputEl.id = 'config-input-log-responses';
                inputEl.name = logKey;
                inputEl.checked = isEnabled;
                
                const helpText = document.createElement('p');
                helpText.className = 'modal-help-text';
                helpText.style.margin = '0 0 0 15px';
                helpText.style.padding = '5px 10px';
                helpText.textContent = '启用后，此源的所有API请求的原始响应将被记录到 config/logs/scraper_responses.log 文件中，用于调试。';

                logSection.appendChild(labelEl);
                logSection.appendChild(inputEl);
                logSection.appendChild(helpText);
                modalBody.appendChild(logSection);
            }

            // 如果是Bilibili，添加登录部分
            if (providerName === 'bilibili') {
                // 修正：调整HTML结构以实现垂直居中布局
                // 新增：为登录后的用户 profile 创建专用容器
                const biliLoginSectionHTML = `
                    <div id="bili-login-section">
                        <div id="bili-user-profile" class="hidden">
                            <img id="bili-user-avatar" src="/static/placeholder.png" alt="avatar" referrerpolicy="no-referrer">
                            <div id="bili-user-info">
                                <span id="bili-user-nickname"></span>
                                <span id="bili-user-vip-status"></span>
                            </div>
                        </div>
                        <div id="bili-login-status">正在检查登录状态...</div>
                        <div id="bili-login-controls">
                            <button type="button" id="bili-login-btn" class="secondary-btn">扫码登录</button>
                        </div>
                    </div>
                `;
                modalBody.insertAdjacentHTML('beforeend', biliLoginSectionHTML);
            }
        })
        .catch(error => {
            modalBody.innerHTML = `<p class="error">加载配置失败: ${error.message}</p>`;
        })
        .finally(() => {
            // 在所有内容加载完毕后，为Bilibili登录按钮绑定事件
            if (providerName === 'bilibili') setupBiliLoginListeners();
        });
}

function hideScraperConfigModal() {
    document.getElementById('generic-modal').classList.add('hidden');
    currentProviderForModal = null;
}

async function handleSaveScraperConfig() {
    if (!currentProviderForModal) return;
    const payload = {};
    // 获取文本字段的值
    document.getElementById('modal-body').querySelectorAll('input[type="text"], textarea').forEach(input => {
        payload[input.name] = input.value.trim();
    });
    // 获取日志开关的值
    const logCheckbox = document.getElementById('config-input-log-responses');
    if (logCheckbox) {
        payload[logCheckbox.name] = logCheckbox.checked ? 'true' : 'false';
    }

    await apiFetch(`/api/ui/scrapers/${currentProviderForModal}/config`, { method: 'PUT', body: JSON.stringify(payload) });
    hideScraperConfigModal();
    alert('配置已保存！');
}

async function updateBiliStatusOnSourcesView() {
    const statusDiv = document.getElementById('bili-status-on-source-list');
    if (!statusDiv) return;

    try {
        const info = await apiFetch('/api/ui/scrapers/bilibili/actions/get_login_info', { method: 'POST' });
        if (info.isLogin) {
            let vipText = '';
            if (info.vipStatus === 1) {
                vipText = info.vipType === 2 ? '<span class="bili-list-vip annual">年度大会员</span>' : '<span class="bili-list-vip">大会员</span>';
            }
            statusDiv.innerHTML = `
                <img src="${normalizeImageUrl(info.face)}" alt="avatar" class="bili-list-avatar" referrerpolicy="no-referrer">
                <span class="bili-list-uname">${info.uname}</span>
                ${vipText}
            `;
        } else {
            statusDiv.textContent = '未登录';
        }
    } catch (error) {
        statusDiv.textContent = '状态检查失败';
    }
}

let biliPollInterval = null;
let biliLoginPopup = null; // 引用弹出的登录窗口

function stopBiliPolling() {
    if (biliPollInterval) {
        clearInterval(biliPollInterval);
        biliPollInterval = null;
    }
    // 新增：如果弹窗存在且未关闭，则关闭它
    if (biliLoginPopup && !biliLoginPopup.closed) {
        biliLoginPopup.close();
        biliLoginPopup = null;
    }
}

async function checkBiliLoginStatus() {
    const profileDiv = document.getElementById('bili-user-profile');
    const avatarImg = document.getElementById('bili-user-avatar');
    const nicknameSpan = document.getElementById('bili-user-nickname');
    const vipSpan = document.getElementById('bili-user-vip-status');
    const statusDiv = document.getElementById('bili-login-status');
    const loginBtn = document.getElementById('bili-login-btn');

    if (!profileDiv || !statusDiv || !loginBtn) return;

    statusDiv.textContent = '正在检查登录状态...';
    statusDiv.className = '';

    try {
        const info = await apiFetch('/api/ui/scrapers/bilibili/actions/get_login_info', { method: 'POST' });
        if (info.isLogin) {
            profileDiv.classList.remove('hidden');
            statusDiv.classList.add('hidden');
            avatarImg.src = normalizeImageUrl(info.face);
            nicknameSpan.textContent = `${info.uname} (Lv.${info.level})`;
            if (info.vipStatus === 1) {
                let vipText = '大会员';
                if (info.vipType === 2) {
                    vipText = '年度大会员';
                }
                const dueDate = new Date(info.vipDueDate).toLocaleDateString();
                vipSpan.textContent = `${vipText} (到期: ${dueDate})`;
                vipSpan.className = 'vip';
            } else {
                vipSpan.textContent = '';
                vipSpan.className = '';
            }
            loginBtn.textContent = '注销';
        } else {
            profileDiv.classList.add('hidden');
            statusDiv.classList.remove('hidden');
            statusDiv.textContent = '当前未登录。';
            loginBtn.textContent = '扫码登录';
        }
    } catch (error) {
        statusDiv.textContent = `检查状态失败: ${error.message}`;
        statusDiv.classList.add('error');
    }
}

async function handleBiliLoginClick() {
    const loginBtn = document.getElementById('bili-login-btn');
    const statusDiv = document.getElementById('bili-login-status');
    if (!loginBtn || !statusDiv) return;

    // 检查当前是登录还是注销
    if (loginBtn.textContent === '注销') {
        if (confirm('确定要注销当前的Bilibili登录吗？')) {
            await apiFetch('/api/ui/scrapers/bilibili/actions/logout', { method: 'POST' });
            await checkBiliLoginStatus(); // 刷新状态
            loadDanmakuSources(); // 重新加载源列表以更新主视图中的状态
        }
        return;
    }

    // --- 以下是登录流程 ---
    stopBiliPolling();
    loginBtn.disabled = true;
    statusDiv.textContent = '正在获取二维码...';

    try {
        const qrData = await apiFetch('/api/ui/scrapers/bilibili/actions/generate_qrcode', { method: 'POST' });

        // 在新窗口中打开一个干净的二维码页面，不通过URL传递数据
        const popupUrl = `/static/bili_qr.html`;
        const width = 350, height = 400;
        const left = (window.screen.width / 2) - (width / 2);
        const top = (window.screen.height / 2) - (height / 2);
        biliLoginPopup = window.open(popupUrl, 'BiliLogin', `width=${width},height=${height},top=${top},left=${left}`);

        // 使用 postMessage 将二维码 URL 安全地传递给弹窗，避免在地址栏暴露
        // 设置一个短暂的延迟以确保弹窗中的脚本已加载并准备好接收消息
        setTimeout(() => {
            if (biliLoginPopup && !biliLoginPopup.closed) {
                biliLoginPopup.postMessage({ type: 'BILI_QR_URL', url: qrData.url }, window.location.origin);
            }
        }, 200);

        statusDiv.textContent = '已打开新窗口，请在新窗口中扫码。';

        biliPollInterval = setInterval(async () => {
            // 如果用户手动关闭了弹窗，则停止轮询
            if (biliLoginPopup && biliLoginPopup.closed) {
                stopBiliPolling();
                statusDiv.textContent = '登录已取消。';
                return;
            }

            try {
                const pollPayload = { qrcode_key: qrData.qrcode_key };
                const pollRes = await apiFetch('/api/ui/scrapers/bilibili/actions/poll_login', { method: 'POST', body: JSON.stringify(pollPayload) });
                if (pollRes.code === 0) { // 登录成功
                    stopBiliPolling();
                    statusDiv.textContent = '登录成功！';
                    statusDiv.classList.add('success');
                    // 成功后自动关闭弹窗和模态框
                    setTimeout(() => {
                        hideScraperConfigModal();
                        loadDanmakuSources(); // 刷新主界面状态
                    }, 1500);
                } else if (pollRes.code === 86038) { // 二维码失效
                    stopBiliPolling();
                    statusDiv.textContent = '二维码已失效，请重新获取。';
                    statusDiv.classList.add('error');
                } else if (pollRes.code === 86090) { // 已扫描，待确认
                    statusDiv.textContent = '已扫描，请在手机上确认登录。';
                }
            } catch (pollError) {
                stopBiliPolling();
                statusDiv.textContent = `轮询失败: ${pollError.message}`;
                statusDiv.classList.add('error');
            }
        }, 2000);
    } catch (error) {
        statusDiv.textContent = `获取二维码失败: ${error.message}`;
        statusDiv.classList.add('error');
    } finally {
        loginBtn.disabled = false;
    }
}

function setupBiliLoginListeners() {
    const loginBtn = document.getElementById('bili-login-btn');
    if (loginBtn) {
        loginBtn.addEventListener('click', handleBiliLoginClick);
        checkBiliLoginStatus();
    }
    // 确保在模态框关闭时停止轮询
    document.getElementById('modal-close-btn').addEventListener('click', stopBiliPolling);
    document.getElementById('modal-cancel-btn').addEventListener('click', stopBiliPolling);
}

export function setupSourcesEventListeners() {
    initializeElements();
    sourcesSubNav.addEventListener('click', handleSourcesSubNav);

    danmakuSourcesList.addEventListener('click', handleDanmakuSourceAction);
    saveDanmakuSourcesBtn.addEventListener('click', handleSaveDanmakuSources);
    toggleDanmakuSourceBtn.addEventListener('click', handleToggleDanmakuSource);
    moveDanmakuSourceUpBtn.addEventListener('click', () => handleMoveDanmakuSource('up'));
    moveDanmakuSourceDownBtn.addEventListener('click', () => handleMoveDanmakuSource('down'));

    saveMetadataSourcesBtn.addEventListener('click', handleSaveMetadataSources);
    moveMetadataSourceUpBtn.addEventListener('click', () => handleMetadataSourceAction('up'));
    moveMetadataSourceDownBtn.addEventListener('click', () => handleMetadataSourceAction('down'));

    // Modal event listeners
    document.getElementById('modal-close-btn').addEventListener('click', hideScraperConfigModal);
    document.getElementById('modal-cancel-btn').addEventListener('click', hideScraperConfigModal);
    document.getElementById('modal-save-btn').addEventListener('click', handleSaveScraperConfig);

    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'sources-view') {
            const firstSubNavBtn = sourcesSubNav.querySelector('.sub-nav-btn');
            if (firstSubNavBtn) firstSubNavBtn.click();
        }
    });
}
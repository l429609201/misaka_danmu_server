import { apiFetch } from '../api.js';

// DOM Elements
let settingsSubNav, settingsSubViews;
// Account
let changePasswordForm, passwordChangeMessage;
let proxySettingsForm, proxyTestResults, proxySaveMessage, testProxyBtn;
// Webhook
let webhookApiKeyInput, regenerateWebhookKeyBtn, webhookCustomDomainInput, saveWebhookDomainBtn, webhookDomainSaveMessage;
let webhookServiceSelect, webhookGeneratedUrlInput, copyWebhookUrlBtn;
// Bangumi
let bangumiSettingsForm, bangumiSaveMessage;
let bangumiLoginBtn, bangumiLogoutBtn, bangumiAuthStateAuthenticated, bangumiAuthStateUnauthenticated;
let bangumiUserNickname, bangumiUserId, bangumiAuthorizedAt, bangumiExpiresAt, bangumiUserAvatar;
// TMDB
let tmdbSettingsForm, tmdbSaveMessage;
// Douban
let doubanSettingsForm, doubanSaveMessage;
// TVDB
let tvdbSettingsForm, tvdbSaveMessage;

// A popup window reference for OAuth
let oauthPopup = null;

function initializeElements() {
    settingsSubNav = document.querySelector('#settings-view .settings-sub-nav');
    settingsSubViews = document.querySelectorAll('#settings-view .settings-subview');

    // Account
    changePasswordForm = document.getElementById('change-password-form');
    passwordChangeMessage = document.getElementById('password-change-message');

    // Proxy
    proxySettingsForm = document.getElementById('proxy-settings-form');
    proxyTestResults = document.getElementById('proxy-test-results');
    proxySaveMessage = document.getElementById('proxy-save-message');
    testProxyBtn = document.getElementById('test-proxy-btn');

    // Webhook
    webhookApiKeyInput = document.getElementById('webhook-api-key');
    regenerateWebhookKeyBtn = document.getElementById('regenerate-webhook-key-btn');
    webhookCustomDomainInput = document.getElementById('webhook-custom-domain-input');
    saveWebhookDomainBtn = document.getElementById('save-webhook-domain-btn');
    webhookDomainSaveMessage = document.getElementById('webhook-domain-save-message');
    webhookServiceSelect = document.getElementById('webhook-service-select');
    webhookGeneratedUrlInput = document.getElementById('webhook-generated-url');
    copyWebhookUrlBtn = document.getElementById('copy-webhook-url-btn');

    // Bangumi
    bangumiSettingsForm = document.getElementById('bangumi-settings-form');
    bangumiSaveMessage = document.getElementById('bangumi-save-message');
    bangumiLoginBtn = document.getElementById('bangumi-login-btn');
    bangumiLogoutBtn = document.getElementById('bangumi-logout-btn');
    bangumiAuthStateAuthenticated = document.getElementById('bangumi-auth-state-authenticated');
    bangumiAuthStateUnauthenticated = document.getElementById('bangumi-auth-state-unauthenticated');
    bangumiUserNickname = document.getElementById('bangumi-user-nickname');
    bangumiUserId = document.getElementById('bangumi-user-id');
    bangumiAuthorizedAt = document.getElementById('bangumi-authorized-at');
    bangumiExpiresAt = document.getElementById('bangumi-expires-at');
    bangumiUserAvatar = document.getElementById('bangumi-user-avatar');

    // TMDB
    tmdbSettingsForm = document.getElementById('tmdb-settings-form');
    tmdbSaveMessage = document.getElementById('tmdb-save-message');

    // Douban
    doubanSettingsForm = document.getElementById('douban-settings-form');
    doubanSaveMessage = document.getElementById('douban-save-message');

    // TVDB
    tvdbSettingsForm = document.getElementById('tvdb-settings-form');
    tvdbSaveMessage = document.getElementById('tvdb-save-message');
}

function handleSettingsSubNav(e) {
    const subNavBtn = e.target.closest('.sub-nav-btn');
    if (!subNavBtn) return;

    const subViewId = subNavBtn.getAttribute('data-subview');
    if (!subViewId) return;

    settingsSubNav.querySelectorAll('.sub-nav-btn').forEach(btn => btn.classList.remove('active'));
    subNavBtn.classList.add('active');

    settingsSubViews.forEach(view => view.classList.add('hidden'));
    const targetSubView = document.getElementById(subViewId);
    if (targetSubView) targetSubView.classList.remove('hidden');

    // Load data for the selected subview
    switch (subViewId) {
        case 'account-settings-subview':
            // No data to load initially
            break;
        case 'proxy-settings-subview':
            loadProxySettings();
            break;
        case 'webhook-settings-subview':
            loadWebhookSettings();
            break;
        case 'bangumi-settings-subview':
            loadBangumiSettings();
            loadBangumiAuthState();
            break;
        case 'tmdb-settings-subview':
            loadTmdbSettings();
            break;
        case 'douban-settings-subview':
            loadDoubanSettings();
            break;
        case 'tvdb-settings-subview':
            loadTvdbSettings();
            break;
    }
}

// --- Account Settings ---
async function handleChangePassword(e) {
    e.preventDefault();
    passwordChangeMessage.textContent = '';
    passwordChangeMessage.className = 'message';

    const oldPassword = document.getElementById('old-password').value;
    const newPassword = document.getElementById('new-password').value;
    const confirmPassword = document.getElementById('confirm-password').value;

    if (newPassword.length < 8) {
        passwordChangeMessage.textContent = '新密码至少需要8位。';
        passwordChangeMessage.classList.add('error');
        return;
    }

    if (newPassword !== confirmPassword) {
        passwordChangeMessage.textContent = '新密码和确认密码不匹配。';
        passwordChangeMessage.classList.add('error');
        return;
    }

    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;

    try {
        await apiFetch('/api/ui/auth/users/me/password', {
            method: 'PUT',
            body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
        });
        passwordChangeMessage.textContent = '密码修改成功！';
        passwordChangeMessage.classList.add('success');
        changePasswordForm.reset();
    } catch (error) {
        passwordChangeMessage.textContent = `修改失败: ${error.message}`;
        passwordChangeMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
}

// --- Proxy Settings ---
async function loadProxySettings() {
    proxySaveMessage.textContent = '';
    proxyTestResults.classList.add('hidden');
    try {
        const data = await apiFetch('/api/ui/config/proxy');
        document.getElementById('proxy-protocol').value = data.proxyProtocol || 'http';
        document.getElementById('proxy-host').value = data.proxyHost || '';
        document.getElementById('proxy-port').value = data.proxyPort || '';
        document.getElementById('proxy-username').value = data.proxyUsername || '';
        document.getElementById('proxy-password').value = data.proxyPassword || '';
        document.getElementById('proxy-enabled').checked = data.proxyEnabled;
    } catch (error) {
        proxySaveMessage.textContent = `加载代理配置失败: ${error.message}`;
        proxySaveMessage.classList.add('error');
    }
}

async function handleSaveProxySettings(e) {
    e.preventDefault();
    const payload = {
        proxyProtocol: document.getElementById('proxy-protocol').value,
        proxyHost: document.getElementById('proxy-host').value.trim(),
        proxyPort: document.getElementById('proxy-port').value ? parseInt(document.getElementById('proxy-port').value, 10) : null,
        proxyUsername: document.getElementById('proxy-username').value.trim(),
        proxyPassword: document.getElementById('proxy-password').value, // Don't trim password
        proxyEnabled: document.getElementById('proxy-enabled').checked,
    };
    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;
    proxySaveMessage.textContent = '保存中...';
    proxySaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/proxy', { method: 'PUT', body: JSON.stringify(payload) });
        proxySaveMessage.textContent = '代理配置保存成功！';
        proxySaveMessage.classList.add('success');
    } catch (error) {
        proxySaveMessage.textContent = `保存失败: ${error.message}`;
        proxySaveMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
}

function _buildProxyUrlFromForm() {
    const protocol = document.getElementById('proxy-protocol').value;
    const host = document.getElementById('proxy-host').value.trim();
    const port = document.getElementById('proxy-port').value.trim();
    const username = document.getElementById('proxy-username').value.trim();
    const password = document.getElementById('proxy-password').value;

    if (!host || !port) {
        return "";
    }

    let url = `${protocol}://`;
    if (username) {
        url += `${encodeURIComponent(username)}`;
        if (password) {
            url += `:${encodeURIComponent(password)}`;
        }
        url += "@";
    }
    url += `${host}:${port}`;
    return url;
}

async function handleTestProxy() {
    proxyTestResults.classList.remove('hidden');
    proxyTestResults.textContent = '正在测试...';
    testProxyBtn.disabled = true;

    const proxyUrl = _buildProxyUrlFromForm();
    const payload = { proxy_url: proxyUrl };

    try {
        const response = await apiFetch('/api/ui/proxy/test', {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        let resultsText = "--- 代理服务器连通性测试 ---\n";
        const connectivity = response.proxy_connectivity;
        if (!connectivity) {
            resultsText += `❌ 代理服务器连接测试失败: 后端未返回有效结果。\n`;
        } else if (connectivity.status === 'success') {
            resultsText += `✅ 代理服务器连接正常，延迟: ${connectivity.latency.toFixed(0)} ms\n`;
        } else if (connectivity.status === 'skipped') {
            resultsText += `⚪️ ${connectivity.error || '未配置代理，跳过测试'}\n`;
        } else { // 'failure'
            resultsText += `❌ 代理服务器连接失败: ${connectivity.error || '无响应或格式错误'}\n`;
        }

        resultsText += "\n--- 目标站点可用性测试 ---\n";
        for (const [domain, result] of Object.entries(response.target_sites || {})) {
            const friendlyDomain = new URL(domain).hostname;
            if (result && result.status === 'success') {
                resultsText += `✅ ${friendlyDomain}: 可达, 延迟: ${result.latency.toFixed(0)} ms\n`;
            } else {
                resultsText += `❌ ${friendlyDomain}: 无法访问 (${result ? result.error : '未知错误'})\n`;
            }
        }

        proxyTestResults.textContent = resultsText;
    } catch (error) {
        proxyTestResults.textContent = `测试请求失败: ${error.message}`;
    } finally {
        testProxyBtn.disabled = false;
    }
}

// --- Webhook Settings ---
async function loadWebhookSettings() {
    webhookApiKeyInput.value = '加载中...';
    webhookCustomDomainInput.value = '加载中...';
    webhookServiceSelect.innerHTML = '<option>加载中...</option>';
    webhookGeneratedUrlInput.value = '';
    webhookDomainSaveMessage.textContent = '';
    try {
        const [keyData, domainData, handlersData] = await Promise.all([
            apiFetch('/api/ui/config/webhook_api_key'),
            apiFetch('/api/ui/config/webhook_custom_domain'),
            apiFetch('/api/ui/webhooks/available')
        ]);
        const apiKey = keyData.value || '未生成';
        const customDomain = domainData.value || '';
        webhookApiKeyInput.value = apiKey;
        webhookCustomDomainInput.value = customDomain;
        
        webhookServiceSelect.innerHTML = '';
        if (handlersData.length > 0) {
            handlersData.forEach(handler => {
                const option = document.createElement('option');
                option.value = handler;
                option.textContent = handler;
                webhookServiceSelect.appendChild(option);
            });
            webhookServiceSelect.disabled = false;
            copyWebhookUrlBtn.disabled = false;
        } else {
            const option = document.createElement('option');
            option.textContent = '无可用服务';
            webhookServiceSelect.appendChild(option);
            webhookServiceSelect.disabled = true;
            copyWebhookUrlBtn.disabled = true;
        }
        // Trigger change event to populate the URL for the first item
        updateWebhookUrl();
    } catch (error) {
        webhookApiKeyInput.value = '加载失败';
        webhookCustomDomainInput.value = '加载失败';
        webhookServiceSelect.innerHTML = `<option>加载失败</option>`;
        webhookServiceSelect.disabled = true;
        copyWebhookUrlBtn.disabled = true;
    }
}

async function handleRegenerateWebhookKey() {
    if (!confirm('确定要重新生成Webhook API Key吗？旧的Key将立即失效。')) return;
    try {
        const data = await apiFetch('/api/ui/config/webhook_api_key/regenerate', { method: 'POST' });
        webhookApiKeyInput.value = data.value;
        updateWebhookUrl(); // Update the URL with the new key
        alert('新的Webhook API Key已生成！');
    } catch (error) {
        alert(`生成失败: ${error.message}`);
    }
}

async function handleSaveWebhookDomain() {
    const domain = webhookCustomDomainInput.value.trim();
    const cleanedDomain = domain.endsWith('/') ? domain.slice(0, -1) : domain;

    webhookDomainSaveMessage.textContent = '';
    webhookDomainSaveMessage.className = 'message';
    saveWebhookDomainBtn.disabled = true;
    saveWebhookDomainBtn.textContent = '保存中...';

    try {
        await apiFetch('/api/ui/config/webhook_custom_domain', {
            method: 'PUT',
            body: JSON.stringify({ value: cleanedDomain })
        });
        webhookDomainSaveMessage.textContent = '域名保存成功！';
        webhookDomainSaveMessage.classList.add('success');
        webhookCustomDomainInput.value = cleanedDomain;
        await loadWebhookSettings();
    } catch (error) {
        webhookDomainSaveMessage.textContent = `保存失败: ${(error.message || error)}`;
        webhookDomainSaveMessage.classList.add('error');
    } finally {
        saveWebhookDomainBtn.disabled = false;
        saveWebhookDomainBtn.textContent = '保存域名';
    }
}

function updateWebhookUrl() {
    const selectedService = webhookServiceSelect.value;
    if (!selectedService || webhookServiceSelect.disabled) {
        webhookGeneratedUrlInput.value = '';
        return;
    }

    const apiKey = webhookApiKeyInput.value;
    const customDomain = webhookCustomDomainInput.value;
    const baseUrl = customDomain || window.location.origin;
    
    const fullUrl = `${baseUrl}/api/webhook/${selectedService}?api_key=${apiKey}`;
    webhookGeneratedUrlInput.value = fullUrl;
}

async function handleCopyWebhookUrl() {
    const urlToCopy = webhookGeneratedUrlInput.value;
    if (!urlToCopy || copyWebhookUrlBtn.disabled) return;
    
    // Helper function to update button state after copy
    const showSuccessOnButton = () => {
        const originalContent = copyWebhookUrlBtn.innerHTML;
        copyWebhookUrlBtn.innerHTML = '✅';
        copyWebhookUrlBtn.disabled = true;
        setTimeout(() => {
            copyWebhookUrlBtn.innerHTML = originalContent;
            copyWebhookUrlBtn.disabled = false;
        }, 2000);
    };

    // Use the modern, secure Clipboard API if available (HTTPS or localhost)
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(urlToCopy);
            showSuccessOnButton();
        } catch (err) {
            alert(`复制失败: ${err}`);
        }
    } else {
        // Fallback for non-secure contexts (e.g., http://192.168.x.x) or older browsers
        const textArea = document.createElement("textarea");
        textArea.value = urlToCopy;
        textArea.style.position = "fixed";
        textArea.style.top = "-9999px";
        textArea.style.left = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        try {
            document.execCommand('copy');
            showSuccessOnButton();
        } catch (err) {
            alert('复制失败，请手动复制。');
        }
        document.body.removeChild(textArea);
    }
}

// --- Bangumi Settings ---
async function loadBangumiSettings() {
    bangumiSaveMessage.textContent = '';
    try {
        const data = await apiFetch('/api/ui/config/provider/bangumi');
        document.getElementById('bangumi-client-id').value = data.bangumi_client_id || '';
        document.getElementById('bangumi-client-secret').value = data.bangumi_client_secret || '';
    } catch (error) {
        bangumiSaveMessage.textContent = `加载Bangumi配置失败: ${error.message}`;
        bangumiSaveMessage.classList.add('error');
    }
}

async function handleSaveBangumiSettings(e) {
    e.preventDefault();
    const payload = {
        bangumi_client_id: document.getElementById('bangumi-client-id').value.trim(),
        bangumi_client_secret: document.getElementById('bangumi-client-secret').value.trim(),
    };
    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;
    bangumiSaveMessage.textContent = '保存中...';
    bangumiSaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/provider/bangumi', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        bangumiSaveMessage.textContent = 'Bangumi 配置保存成功！';
        bangumiSaveMessage.classList.add('success');
    } catch (error) {
        bangumiSaveMessage.textContent = `保存失败: ${error.message}`;
        bangumiSaveMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
        saveBtn.textContent = '保存';
}

async function loadBangumiAuthState() {
    try {
        const state = await apiFetch('/api/bgm/auth/state');
        updateBangumiAuthStateUI(state);
    } catch (error) {
        console.error("加载Bangumi授权状态失败:", error);
        updateBangumiAuthStateUI({ is_authenticated: false });
    }
}

function updateBangumiAuthStateUI(state) {
    const isAuthenticated = state.is_authenticated;
    bangumiAuthStateAuthenticated.classList.toggle('hidden', !isAuthenticated);
    bangumiAuthStateUnauthenticated.classList.toggle('hidden', isAuthenticated);
    bangumiLoginBtn.classList.toggle('hidden', isAuthenticated);
    bangumiLogoutBtn.classList.toggle('hidden', !isAuthenticated);

    if (isAuthenticated) {
        bangumiUserNickname.textContent = state.nickname;
        bangumiUserId.textContent = state.bangumi_user_id;
        bangumiAuthorizedAt.textContent = new Date(state.authorized_at).toLocaleString();
        bangumiExpiresAt.textContent = new Date(state.expires_at).toLocaleString();
        bangumiUserAvatar.src = state.avatar_url || '/static/placeholder.png';
    }
}

async function handleBangumiLogin() {
    try {
        const { url } = await apiFetch('/api/bgm/auth/url');
        if (oauthPopup && !oauthPopup.closed) {
            oauthPopup.focus();
        } else {
            const width = 600, height = 700;
            const left = (window.screen.width / 2) - (width / 2);
            const top = (window.screen.height / 2) - (height / 2);
            oauthPopup = window.open(url, 'BangumiAuth', `width=${width},height=${height},top=${top},left=${left}`);
        }
    } catch (error) {
        alert(`获取授权链接失败: ${error.message}`);
    }
}

async function handleBangumiLogout() {
    if (!confirm('确定要注销Bangumi授权吗？')) return;
    try {
        await apiFetch('/api/bgm/auth', { method: 'DELETE' });
        loadBangumiAuthState();
    } catch (error) {
        alert(`注销失败: ${error.message}`);
    }
}

// --- TMDB Settings ---
async function loadTmdbSettings() {
    tmdbSaveMessage.textContent = '';
    try {
        const data = await apiFetch('/api/ui/config/provider/tmdb');
        document.getElementById('tmdb-api-key').value = data.tmdb_api_key || '';
        document.getElementById('tmdb-api-base-url').value = data.tmdb_api_base_url || '';
        document.getElementById('tmdb-image-base-url').value = data.tmdb_image_base_url || '';
    } catch (error) {
        tmdbSaveMessage.textContent = `加载TMDB配置失败: ${error.message}`;
        tmdbSaveMessage.classList.add('error');
    }
}

async function handleSaveTmdbSettings(e) {
    e.preventDefault();
    const payload = {
        tmdb_api_key: document.getElementById('tmdb-api-key').value.trim(),
        tmdb_api_base_url: document.getElementById('tmdb-api-base-url').value.trim(),
        tmdb_image_base_url: document.getElementById('tmdb-image-base-url').value.trim(),
    };
    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;
    tmdbSaveMessage.textContent = '保存中...';
    tmdbSaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/provider/tmdb', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        tmdbSaveMessage.textContent = 'TMDB 配置保存成功！';
        tmdbSaveMessage.classList.add('success');
    } catch (error) {
        tmdbSaveMessage.textContent = `保存失败: ${error.message}`;
        tmdbSaveMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
}

// --- Douban Settings ---
async function loadDoubanSettings() {
    doubanSaveMessage.textContent = '';
    try {
        const data = await apiFetch('/api/ui/config/douban_cookie');
        document.getElementById('douban-cookie').value = data.value || '';
    } catch (error) {
        doubanSaveMessage.textContent = `加载豆瓣配置失败: ${error.message}`;
        doubanSaveMessage.classList.add('error');
    }
}

async function handleSaveDoubanSettings(e) {
    e.preventDefault();
    const payload = {
        value: document.getElementById('douban-cookie').value.trim(),
    };
    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;
    doubanSaveMessage.textContent = '保存中...';
    doubanSaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/douban_cookie', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        doubanSaveMessage.textContent = '豆瓣 Cookie 保存成功！';
        doubanSaveMessage.classList.add('success');
    } catch (error) {
        doubanSaveMessage.textContent = `保存失败: ${error.message}`;
        doubanSaveMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
}

// --- TVDB Settings ---
async function loadTvdbSettings() {
    tvdbSaveMessage.textContent = '';
    tvdbSaveMessage.className = 'message';
    try {
        const data = await apiFetch('/api/ui/config/tvdb_api_key');
        document.getElementById('tvdb-api-key').value = data.value || '';
    } catch (error) {
        tvdbSaveMessage.textContent = `加载TVDB配置失败: ${error.message}`;
        tvdbSaveMessage.classList.add('error');
    }
}

async function handleSaveTvdbSettings(e) {
    e.preventDefault();
    const payload = {
        value: document.getElementById('tvdb-api-key').value.trim(),
    };
    const saveBtn = e.target.querySelector('button[type="submit"]');
    saveBtn.disabled = true;
    tvdbSaveMessage.textContent = '保存中...';
    tvdbSaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/tvdb_api_key', {
            method: 'PUT',
            body: JSON.stringify(payload)
        });
        tvdbSaveMessage.textContent = 'TVDB 配置保存成功！';
        tvdbSaveMessage.classList.add('success');
    } catch (error) {
        tvdbSaveMessage.textContent = `保存失败: ${error.message}`;
        tvdbSaveMessage.classList.add('error');
    } finally {
        saveBtn.disabled = false;
    }
}

// --- Main Setup ---
export function setupSettingsEventListeners() {
    initializeElements();

    settingsSubNav.addEventListener('click', handleSettingsSubNav);

    // Account
    changePasswordForm.addEventListener('submit', handleChangePassword);

    // Proxy
    proxySettingsForm.addEventListener('submit', handleSaveProxySettings);
    testProxyBtn.addEventListener('click', handleTestProxy);

    // Webhook
    regenerateWebhookKeyBtn.addEventListener('click', handleRegenerateWebhookKey);
    saveWebhookDomainBtn.addEventListener('click', handleSaveWebhookDomain);
    webhookServiceSelect.addEventListener('change', updateWebhookUrl);
    copyWebhookUrlBtn.addEventListener('click', handleCopyWebhookUrl);

    // Bangumi
    bangumiSettingsForm.addEventListener('submit', handleSaveBangumiSettings);
    bangumiLoginBtn.addEventListener('click', handleBangumiLogin);
    bangumiLogoutBtn.addEventListener('click', handleBangumiLogout);
    window.addEventListener('message', (event) => {
        if (event.data === 'BANGUMI-OAUTH-COMPLETE') {
            if (oauthPopup) oauthPopup.close();
            loadBangumiAuthState();
        }
    });

    // TMDB
    tmdbSettingsForm.addEventListener('submit', handleSaveTmdbSettings);

    // Douban
    doubanSettingsForm.addEventListener('submit', handleSaveDoubanSettings);

    // TVDB
    tvdbSettingsForm.addEventListener('submit', handleSaveTvdbSettings);

    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'settings-view') {
            // Automatically click the first sub-nav button to load the default view
            const firstSubNavBtn = settingsSubNav.querySelector('.sub-nav-btn');
            if (firstSubNavBtn) firstSubNavBtn.click();
        }
    });
}
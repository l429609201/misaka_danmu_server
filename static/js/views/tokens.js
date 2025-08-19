import { apiFetch } from '../api.js';
import { switchView } from '../ui.js';

// DOM Elements
// Token Management
let tokenManagerView, tokenTableBody, addTokenBtn, addTokenView, addTokenForm;
let customDomainInput, saveDomainBtn, domainSaveMessage;
let uaFilterModeSelect, saveUaModeBtn, manageUaListBtn, uaModeSaveMessage;
let uaSettingsView, uaRulesTableBody, addUaRuleForm;
let tokenLogView, tokenLogTableBody, tokenLogViewTitle;
// Danmaku Control
let danmakuOutputForm, limitInput, aggregationToggle, saveMessage;
// Sub-navigation
let tokenManagerSubNav, tokenManagerSubViews;

function initializeElements() {
    // Sub-navigation
    tokenManagerSubNav = document.querySelector('#token-manager-view .settings-sub-nav');
    tokenManagerSubViews = document.querySelectorAll('#token-manager-view .settings-subview');

    // Token Management
    tokenManagerView = document.getElementById('token-manager-view');
    tokenTableBody = document.querySelector('#token-table tbody');
    addTokenBtn = document.getElementById('add-token-btn');
    addTokenView = document.getElementById('add-token-view');
    addTokenForm = document.getElementById('add-token-form');

    customDomainInput = document.getElementById('custom-domain-input');
    saveDomainBtn = document.getElementById('save-domain-btn');
    domainSaveMessage = document.getElementById('domain-save-message');

    uaFilterModeSelect = document.getElementById('ua-filter-mode');
    saveUaModeBtn = document.getElementById('save-ua-mode-btn');
    manageUaListBtn = document.getElementById('manage-ua-list-btn');
    uaModeSaveMessage = document.getElementById('ua-mode-save-message');

    uaSettingsView = document.getElementById('ua-settings-view');
    uaRulesTableBody = document.querySelector('#ua-rules-table tbody');
    addUaRuleForm = document.getElementById('add-ua-rule-form');

    tokenLogView = document.getElementById('token-log-view');
    tokenLogTableBody = document.querySelector('#token-log-table tbody');
    tokenLogViewTitle = document.getElementById('token-log-view-title');

    // Danmaku Control
    danmakuOutputForm = document.getElementById('danmaku-output-form');
    limitInput = document.getElementById('danmaku-limit-input');
    aggregationToggle = document.getElementById('danmaku-aggregation-toggle');
    saveMessage = document.getElementById('danmaku-control-save-message');
}

async function loadAndRenderTokens() {
    if (!tokenTableBody) return;
    tokenTableBody.innerHTML = '<tr><td colspan="6">加载中...</td></tr>';
    try {
        const tokens = await apiFetch('/api/ui/tokens');
        renderTokens(tokens);
    } catch (error) {
        tokenTableBody.innerHTML = `<tr class="error"><td colspan="6">加载失败: ${(error.message || error)}</td></tr>`;
    }
}

function renderTokens(tokens) {
    tokenTableBody.innerHTML = '';
    if (tokens.length === 0) {
        tokenTableBody.innerHTML = '<tr><td colspan="6">没有创建任何Token。</td></tr>';
        return;
    }

    tokens.forEach(token => {
        const row = tokenTableBody.insertRow();

        const createdDate = new Date(token.createdAt);
        const createdHtml = `${createdDate.toLocaleDateString()}<br><span class="time-part">${createdDate.toLocaleTimeString()}</span>`;

        const expiresHtml = token.expiresAt 
            ? `${new Date(token.expiresAt).toLocaleDateString()}<br><span class="time-part">${new Date(token.expiresAt).toLocaleTimeString()}</span>`
            : '永久有效';
        
        const hiddenTokenText = '*'.repeat(token.token.length);
        const enabledText = token.is_enabled ? '禁用' : '启用';

        row.innerHTML = `
            <td class="token-name-cell" title="${token.name}">${token.name}</td>
            <td>
                <span class="token-value">
                    <span class="token-text token-hidden" data-token-value="${token.token}">${hiddenTokenText}</span>
                    <span class="token-visibility-toggle" data-action="toggle-visibility" title="显示/隐藏">👁️</span>
                </span>
            </td>
            <td class="token-status ${token.isEnabled ? '' : 'disabled'}">${token.isEnabled ? '✅' : '❌'}</td>
            <td class="date-cell">${createdHtml}</td>
            <td class="date-cell">${expiresHtml}</td>
            <td class="actions-cell">
                <div class="action-buttons-wrapper">
                    <button class="action-btn" data-action="copy" data-token-id="${token.id}" data-token-value="${token.token}" title="复制链接">📋</button>
                    <button class="action-btn" data-action="view-log" data-token-id="${token.id}" data-token-name="${token.name}" title="查看日志">📜</button>
                    <button class="action-btn" data-action="toggle" data-token-id="${token.id}" title="${enabledText}">${token.isEnabled ? '⏸️' : '▶️'}</button>
                    <button class="action-btn" data-action="delete" data-token-id="${token.id}" title="删除">🗑️</button>
                </div>
            </td>
        `;
    });
}

async function handleTokenAction(e) {
    const actionElement = e.target.closest('[data-action]');
    if (!actionElement) return;

    const action = actionElement.dataset.action;

    // Handle visibility toggle separately as it's not a button
    if (action === 'toggle-visibility') {
        const tokenTextSpan = actionElement.previousElementSibling;
        if (tokenTextSpan && tokenTextSpan.classList.contains('token-text')) {
            if (tokenTextSpan.classList.contains('token-hidden')) {
                tokenTextSpan.textContent = tokenTextSpan.dataset.tokenValue;
                tokenTextSpan.classList.remove('token-hidden');
            } else {
                tokenTextSpan.textContent = '*'.repeat(tokenTextSpan.dataset.tokenValue.length);
                tokenTextSpan.classList.add('token-hidden');
            }
        }
        return; // Exit after handling visibility
    }

    const button = actionElement; // For all other actions, it should be a button
    const tokenId = parseInt(button.dataset.tokenId, 10);
    const tokenValue = button.dataset.tokenValue || button.closest('tr').querySelector('.token-text').dataset.tokenValue;

    if (action === 'copy') {
        const domain = customDomainInput.value.trim();
        const textToCopy = domain ? `${domain}/api/v1/${tokenValue}` : tokenValue;

        // 优先使用现代的、安全的剪贴板API
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(textToCopy).then(() => {
                alert(`已复制到剪贴板: ${textToCopy}`);
            }, (err) => {
                alert(`复制失败: ${err}。请手动复制。`);
            });
        } else {
            // 为 HTTP 或旧版浏览器提供后备方案
            const textArea = document.createElement("textarea");
            textArea.value = textToCopy;
            textArea.style.position = "fixed";
            textArea.style.top = "-9999px";
            textArea.style.left = "-9999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                document.execCommand('copy');
                alert(`已复制到剪贴板: ${textToCopy}`);
            } catch (err) {
                alert('复制失败，请手动复制。');
            }
            document.body.removeChild(textArea);
        }
    } else if (action === 'toggle') {
        try {
            await apiFetch(`/api/ui/tokens/${tokenId}/toggle`, { method: 'PUT' });
            loadAndRenderTokens();
        } catch (error) {
            alert(`操作失败: ${error.message}`);
        }
    } else if (action === 'delete') {
        if (confirm("您确定要删除这个Token吗？此操作不可恢复。")) {
            try {
                await apiFetch(`/api/ui/tokens/${tokenId}`, { method: 'DELETE' });
                loadAndRenderTokens();
            } catch (error) {
                alert(`删除失败: ${error.message}`);
            }
        }
    } else if (action === 'view-log') {
        const tokenName = button.dataset.tokenName;
        showTokenLogView(tokenId, tokenName);
    }
}

async function handleAddTokenSave(e) {
    e.preventDefault();
    const nameInput = document.getElementById('add-token-name');
    const name = nameInput.value.trim();
    const validity = document.getElementById('add-token-validity').value;
    if (!name) {
        alert('名称不能为空。');
        return;
    }

    const saveButton = addTokenForm.querySelector('button[type="submit"]');
    saveButton.disabled = true;
    saveButton.textContent = '保存中...';

    try {
        await apiFetch('/api/ui/tokens', {
            method: 'POST',
            body: JSON.stringify({ name: name, validity_period: validity }),
        });
        document.getElementById('back-to-tokens-from-add-btn').click();
        loadAndRenderTokens();
    } catch (error) {
        alert(`添加失败: ${(error.message || error)}`);
    } finally {
        saveButton.disabled = false;
        saveButton.textContent = '保存';
    }
}

async function loadCustomDomain() {
    domainSaveMessage.textContent = '';
    domainSaveMessage.className = 'message';
    try {
        const data = await apiFetch('/api/ui/config/custom_api_domain');
        customDomainInput.value = data.value || '';
    } catch (error) {
        domainSaveMessage.textContent = `加载域名失败: ${(error.message || error)}`;
        domainSaveMessage.classList.add('error');
    }
}

async function handleSaveDomain() {
    const domain = customDomainInput.value.trim();
    const cleanedDomain = domain.endsWith('/') ? domain.slice(0, -1) : domain;
    
    domainSaveMessage.textContent = '';
    domainSaveMessage.className = 'message';
    saveDomainBtn.disabled = true;
    saveDomainBtn.textContent = '保存中...';

    try {
        await apiFetch('/api/ui/config/custom_api_domain', {
            method: 'PUT',
            body: JSON.stringify({ value: cleanedDomain })
        });
        domainSaveMessage.textContent = '域名保存成功！';
        domainSaveMessage.classList.add('success');
        customDomainInput.value = cleanedDomain;
    } catch (error) {
        domainSaveMessage.textContent = `保存失败: ${(error.message || error)}`;
        domainSaveMessage.classList.add('error');
    } finally {
        saveDomainBtn.disabled = false;
        saveDomainBtn.textContent = '保存域名';
    }
}

async function loadUaFilterMode() {
    uaModeSaveMessage.textContent = '';
    try {
        const data = await apiFetch('/api/ui/config/ua_filter_mode');
        uaFilterModeSelect.value = data.value || 'off';
    } catch (error) {
        uaModeSaveMessage.textContent = `加载UA过滤模式失败: ${error.message}`;
    }
}

async function handleSaveUaMode() {
    const mode = uaFilterModeSelect.value;
    uaModeSaveMessage.textContent = '保存中...';
    uaModeSaveMessage.className = 'message';
    try {
        await apiFetch('/api/ui/config/ua_filter_mode', {
            method: 'PUT',
            body: JSON.stringify({ value: mode })
        });
        uaModeSaveMessage.textContent = '模式保存成功！';
        uaModeSaveMessage.classList.add('success');
    } catch (error) {
        uaModeSaveMessage.textContent = `保存失败: ${error.message}`;
        uaModeSaveMessage.classList.add('error');
    }
}

async function loadAndRenderUaRules() {
    uaRulesTableBody.innerHTML = '<tr><td colspan="3">加载中...</td></tr>';
    try {
        const rules = await apiFetch('/api/ui/ua-rules');
        uaRulesTableBody.innerHTML = '';
        if (rules.length === 0) {
            uaRulesTableBody.innerHTML = '<tr><td colspan="3">名单为空。</td></tr>';
            return;
        }
        rules.forEach(rule => {
            const row = uaRulesTableBody.insertRow();
            row.innerHTML = `
                <td>${rule.uaString}</td>
                <td>${new Date(rule.createdAt).toLocaleString()}</td>
                <td class="actions-cell">
                    <button class="action-btn" data-rule-id="${rule.id}" title="删除">🗑️</button>
                </td>
            `;
        });
    } catch (error) {
        uaRulesTableBody.innerHTML = `<tr class="error"><td colspan="3">加载失败: ${error.message}</td></tr>`;
    }
}

async function handleAddUaRule(e) {
    e.preventDefault();
    const input = document.getElementById('add-ua-string');
    const uaString = input.value.trim();
    if (!uaString) return;
    try {
        await apiFetch('/api/ui/ua-rules', {
            method: 'POST',
            body: JSON.stringify({ uaString: uaString })
        });
        input.value = '';
        loadAndRenderUaRules();
    } catch (error) {
        alert(`添加失败: ${error.message}`);
    }
}

async function handleDeleteUaRule(e) {
    const button = e.target.closest('.action-btn');
    if (!button) return;
    const ruleId = parseInt(button.dataset.ruleId, 10);
    if (confirm('确定要删除这条UA规则吗？')) {
        try {
            await apiFetch(`/api/ui/ua-rules/${ruleId}`, { method: 'DELETE' });
            loadAndRenderUaRules();
        } catch (error) {
            alert(`删除失败: ${error.message}`);
        }
    }
}

async function showTokenLogView(tokenId, tokenName) {
    switchView('token-log-view');
    tokenLogViewTitle.textContent = `Token访问日志: ${tokenName}`;
    tokenLogTableBody.innerHTML = '<tr><td colspan="5">加载中...</td></tr>';
    try {
        const logs = await apiFetch(`/api/ui/tokens/${tokenId}/logs`);
        tokenLogTableBody.innerHTML = '';
        if (logs.length === 0) {
            tokenLogTableBody.innerHTML = '<tr><td colspan="5">此Token没有访问记录。</td></tr>';
            return;
        }
        logs.forEach(log => {
            const row = tokenLogTableBody.insertRow();
            row.innerHTML = `
                <td>${new Date(log.accessTime).toLocaleString()}</td>
                <td>${log.ipAddress}</td>
                <td>${log.status}</td>
                <td>${log.path || ''}</td>
                <td>${log.userAgent}</td>
            `;
        });
    } catch (error) {
        tokenLogTableBody.innerHTML = `<tr class="error"><td colspan="5">加载日志失败: ${error.message}</td></tr>`;
    }
}

async function loadDanmakuSettings() {
    if (!saveMessage || !limitInput || !aggregationToggle) {
        console.error("Cannot load danmaku settings: required elements are not available.");
        return;
    }
    saveMessage.textContent = '';
    saveMessage.className = 'message';

    try {
        const [limitData, aggregationData] = await Promise.all([
            apiFetch('/api/ui/config/danmaku_output_limit_per_source'),
            apiFetch('/api/ui/config/danmaku_aggregation_enabled')
        ]);
        limitInput.value = limitData.value ?? '-1';
        aggregationToggle.checked = (aggregationData.value ?? 'true').toLowerCase() === 'true';
    } catch (error) {
        saveMessage.textContent = `加载设置失败: ${error.message}`;
        saveMessage.classList.add('error');
    }
}

async function handleSaveDanmakuSettings(e) {
    e.preventDefault();
    if (!danmakuOutputForm || !saveMessage || !limitInput || !aggregationToggle) {
        console.error("Cannot save danmaku settings: required elements are not available.");
        return;
    }
    saveMessage.textContent = '保存中...';
    saveMessage.className = 'message';

    const saveBtn = danmakuOutputForm.querySelector('button[type="submit"]');
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = '保存中...';
    }

    try {
        const limitPayload = { value: limitInput.value };
        await apiFetch('/api/ui/config/danmaku_output_limit_per_source', { method: 'PUT', body: JSON.stringify(limitPayload) });

        const aggregationPayload = { value: aggregationToggle.checked.toString() };
        await apiFetch('/api/ui/config/danmaku_aggregation_enabled', { method: 'PUT', body: JSON.stringify(aggregationPayload) });
        
        saveMessage.textContent = '设置已成功保存！';
        saveMessage.classList.add('success');
    } catch (error) {
        saveMessage.textContent = `保存失败: ${error.message}`;
        saveMessage.classList.add('error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = '保存设置';
        }
    }
}

function handleSubNavClick(e) {
    const subNavBtn = e.target.closest('.sub-nav-btn');
    if (!subNavBtn) return;

    const subViewId = subNavBtn.getAttribute('data-subview');
    if (!subViewId) return;

    tokenManagerSubNav.querySelectorAll('.sub-nav-btn').forEach(btn => btn.classList.remove('active'));
    subNavBtn.classList.add('active');

    tokenManagerSubViews.forEach(view => view.classList.add('hidden'));
    const targetSubView = document.getElementById(subViewId);
    if (targetSubView) targetSubView.classList.remove('hidden');

    if (subViewId === 'token-management-subview') {
        loadAndRenderTokens();
        loadCustomDomain();
        loadUaFilterMode();
    } else if (subViewId === 'danmaku-output-subview') {
        loadDanmakuSettings();
    }
}

export function setupTokensEventListeners() {
    initializeElements();

    tokenManagerSubNav.addEventListener('click', handleSubNavClick);
    danmakuOutputForm.addEventListener('submit', handleSaveDanmakuSettings);

    addTokenBtn.addEventListener('click', () => {
        switchView('add-token-view');
        addTokenForm.reset();
    });
    document.getElementById('back-to-tokens-from-add-btn').addEventListener('click', () => switchView('token-manager-view'));
    document.getElementById('back-to-tokens-from-ua-btn').addEventListener('click', () => switchView('token-manager-view'));
    document.getElementById('back-to-tokens-from-log-btn').addEventListener('click', () => switchView('token-manager-view'));

    addTokenForm.addEventListener('submit', handleAddTokenSave);
    saveDomainBtn.addEventListener('click', handleSaveDomain);
    tokenTableBody.addEventListener('click', handleTokenAction);
    saveUaModeBtn.addEventListener('click', handleSaveUaMode);
    manageUaListBtn.addEventListener('click', () => {
        switchView('ua-settings-view');
        loadAndRenderUaRules();
    });
    addUaRuleForm.addEventListener('submit', handleAddUaRule);
    uaRulesTableBody.addEventListener('click', handleDeleteUaRule);

    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'token-manager-view') {
            const firstSubNavBtn = tokenManagerSubNav.querySelector('.sub-nav-btn');
            if (firstSubNavBtn) firstSubNavBtn.click();
        }
    });
}
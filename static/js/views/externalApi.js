import { apiFetch } from '../api.js';

let externalApiSubNav, externalApiSubViews;
let apiKeyInput, regenerateKeyBtn;
let logTableBody;
let logRefreshInterval = null;

function initializeElements() {
    externalApiSubNav = document.querySelector('#external-api-view .settings-sub-nav');
    externalApiSubViews = document.querySelectorAll('#external-api-view .settings-subview');
    apiKeyInput = document.getElementById('external-api-key-input');
    regenerateKeyBtn = document.getElementById('regenerate-external-key-btn');
    logTableBody = document.querySelector('#external-api-log-table tbody');
}

function handleSubNavClick(e) {
    const subNavBtn = e.target.closest('.sub-nav-btn');
    if (!subNavBtn) return;

    const subViewId = subNavBtn.getAttribute('data-subview');
    if (!subViewId) return;

    externalApiSubNav.querySelectorAll('.sub-nav-btn').forEach(btn => btn.classList.remove('active'));
    subNavBtn.classList.add('active');

    externalApiSubViews.forEach(view => view.classList.add('hidden'));
    const targetSubView = document.getElementById(subViewId);
    if (targetSubView) {
        targetSubView.classList.remove('hidden');
    }

    if (subViewId === 'external-api-key-subview') {
        loadApiKey();
        stopLogRefresh();
    } else if (subViewId === 'external-api-logs-subview') {
        startLogRefresh();
    }
}

async function loadApiKey() {
    apiKeyInput.value = '加载中...';
    try {
        const data = await apiFetch('/api/ui/config/external_api_key');
        apiKeyInput.value = data.value || '未生成，请点击右侧按钮生成。';
    } catch (error) {
        apiKeyInput.value = `加载失败: ${error.message}`;
    }
}

async function handleRegenerateKey() {
    if (!confirm('您确定要重新生成外部API密钥吗？旧的密钥将立即失效。')) return;

    regenerateKeyBtn.disabled = true;
    try {
        const data = await apiFetch('/api/ui/config/external_api_key/regenerate', { method: 'POST' });
        apiKeyInput.value = data.value;
        alert('新的API密钥已生成！');
    } catch (error) {
        alert(`生成失败: ${error.message}`);
    } finally {
        regenerateKeyBtn.disabled = false;
    }
}

async function loadApiLogs() {
    if (!logTableBody) return;
    try {
        const logs = await apiFetch('/api/ui/external-logs');
        logTableBody.innerHTML = '';
        if (logs.length === 0) {
            logTableBody.innerHTML = '<tr><td colspan="5">暂无访问记录。</td></tr>';
            return;
        }
        logs.forEach(log => {
            const row = logTableBody.insertRow();
            const statusClass = log.status_code >= 400 ? 'error' : 'success';
            row.innerHTML = `
                <td>${new Date(log.access_time).toLocaleString()}</td>
                <td>${log.ip_address}</td>
                <td>${log.endpoint}</td>
                <td class="${statusClass}">${log.status_code}</td>
                <td>${log.message || ''}</td>
            `;
        });
    } catch (error) {
        console.error("加载外部API日志失败:", error);
        logTableBody.innerHTML = `<tr><td colspan="5" class="error">加载日志失败: ${error.message}</td></tr>`;
    }
}

function startLogRefresh() {
    stopLogRefresh(); // Ensure no multiple intervals are running
    loadApiLogs();
    logRefreshInterval = setInterval(loadApiLogs, 5000); // Refresh every 5 seconds
}

function stopLogRefresh() {
    if (logRefreshInterval) {
        clearInterval(logRefreshInterval);
        logRefreshInterval = null;
    }
}

export function setupExternalApiEventListeners() {
    initializeElements();
    externalApiSubNav.addEventListener('click', handleSubNavClick);
    regenerateKeyBtn.addEventListener('click', handleRegenerateKey);

    document.addEventListener('viewchange', (e) => {
        if (e.detail.viewId === 'external-api-view') {
            const firstSubNavBtn = externalApiSubNav.querySelector('.sub-nav-btn');
            if (firstSubNavBtn) firstSubNavBtn.click();
        } else {
            stopLogRefresh();
        }
    });
}

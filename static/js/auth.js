import { apiFetch } from './api.js';
import { switchView, setActiveSidebar } from './ui.js';

let token = localStorage.getItem('danmu_api_token');
let logRefreshInterval = null;

function showAuthView(show) {
    document.getElementById('auth-view').classList.toggle('hidden', !show);
    document.getElementById('main-view').classList.toggle('hidden', show);
}

async function handleLogin(e) {
    e.preventDefault();
    const authError = document.getElementById('auth-error');
    authError.textContent = '';
    const username = document.getElementById('login-username').value;
    const password = document.getElementById('login-password').value;

    const formData = new URLSearchParams();
    formData.append('username', username);
    formData.append('password', password);

    try {
        const response = await fetch('/api/ui/auth/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData,
        });

        if (!response.ok) {
            let errorDetail = '用户名或密码错误';
            try {
                const errorData = await response.json();
                errorDetail = errorData.detail || errorDetail;
            } catch (jsonError) { /* ignore */ }
            throw new Error(errorDetail);
        }

        const data = await response.json();
        token = data.access_token;
        localStorage.setItem('danmu_api_token', token);
        document.getElementById('login-form').reset();
        await checkLogin();
    } catch (error) {
        authError.textContent = `登录失败: ${(error.message || error)}`;
    }
}

async function logout() {
    try {
        await apiFetch('/api/ui/auth/logout', { method: 'POST' });
    } catch (error) {
        console.error("Logout API call failed:", error.message);
    } finally {
        token = null;
        localStorage.removeItem('danmu_api_token');
        document.dispatchEvent(new CustomEvent('auth:status-changed', { detail: { loggedIn: false } }));
        showAuthView(true);
    }
}

async function checkLogin() {
    token = localStorage.getItem('danmu_api_token');
    if (!token) {
        showAuthView(true);
        return;
    }
    try {
        const user = await apiFetch('/api/ui/auth/users/me');
        if (!user || !user.username) {
            throw new Error('未能获取到有效的用户信息。');
        }
        document.getElementById('current-user').textContent = `用户: ${user.username}`;
        document.dispatchEvent(new CustomEvent('auth:status-changed', { detail: { loggedIn: true } }));
        showAuthView(false);
        // Trigger initial view load
        document.dispatchEvent(new CustomEvent('viewchange', { detail: { viewId: 'home-view' } }));
    } catch (error) {
        console.error(`自动登录失败: ${error.message}`);
        logout();
    }
}

export function setupAuthEventListeners() {
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    document.getElementById('logout-btn').addEventListener('click', logout);
}

export { checkLogin, logout };
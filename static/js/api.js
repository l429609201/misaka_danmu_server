export async function apiFetch(url, options = {}) {
    const token = localStorage.getItem('danmu_api_token');
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(url, { ...options, headers });

    if (response.status === 401) {
        // This should trigger a logout in the caller
        throw new Error("会话已过期或无效，请重新登录。");
    }

    if (!response.ok) {
        let errorMessage = `HTTP error! status: ${response.status}`;
        try {
            const errorData = await response.json();
            errorMessage = errorData.detail || JSON.stringify(errorData);
        } catch (e) {
            errorMessage = await response.text().catch(() => errorMessage);
        }
        throw new Error(errorMessage);
    }
    
    if (response.status === 204) {
        return {};
    }
    
    const responseText = await response.text();
    return responseText ? JSON.parse(responseText) : {};
}
import api from './fetch'

// 获取可用的发送范围
export const getNotificationScopes = () => api.get('/api/ui/notification/templates/scopes')

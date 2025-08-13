import api from './fetch'

/** 登录 */
export const login = data =>
  api.post('/api/ui/auth/token', data, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

/** 退出登录 */
export const logout = () => api.post('/api/ui/auth/logout')

/** 获取用户信息 */
export const getUserInfo = (options = {}) =>
  api.get('/api/ui/auth/users/me', null, {
    ...options,
  })

/** 获取日志 */
export const getLogs = (options = {}) =>
  api.get('/api/ui/logs', null, {
    ...options,
  })

/** 匹配测试 */
export const getMatchTest = data =>
  api.post(
    `/api/${data.apiToken}/match`,
    JSON.stringify({ fileName: data.fileName })
  )

/** 清除搜索缓存 */
export const clearSearchCache = () => api.post('/api/ui/cache/clear')

/** 搜索结果 */
export const getSearchResult = data =>
  api.get('/api/ui/search/provider', {
    keyword: data.keyword,
  })

/** 导入弹幕  */
export const importDanmu = data => api.post('/api/ui/import', data)

/** 搜索tmdb */
export const getTmdbSearch = data =>
  api.get(`/api/tmdb/search/${data.mediaType}`, {
    keyword: data.keyword,
  })

/** 任务列表 */
export const getTaskList = data => api.get('/api/ui/tasks', data)
/** 暂停任务 */
export const pauseTask = data => api.post('/api/ui/tasks/pause', data)
/** 继续任务 */
export const resumeTask = data => api.post('/api/ui/tasks/resume', data)
/** 删除任务 */
export const deleteTask = data => api.delete(`/api/ui/tasks/${data.taskId}`)

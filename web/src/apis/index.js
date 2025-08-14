import api from './fetch'

/** ----------------------------用户相关开始------------------------- */
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

/** ----------------------------首页接口------------------------- */
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

/** ----------------------------任务相关开始------------------------- */
/** 任务列表 */
export const getTaskList = data => api.get('/api/ui/tasks', data)
/** 暂停任务 */
export const pauseTask = data => api.post('/api/ui/tasks/pause', data)
/** 继续任务 */
export const resumeTask = data => api.post('/api/ui/tasks/resume', data)
/** 删除任务 */
export const deleteTask = data => api.delete(`/api/ui/tasks/${data.taskId}`)
/** 定时任务列表 */
export const getScheduledTaskList = data =>
  api.get('/api/ui/scheduled-tasks', data)
/** 添加定时任务 */
export const addScheduledTask = data =>
  api.post('/api/ui/scheduled-tasks', data)
/** 编辑定时任务 */
export const editScheduledTask = data =>
  api.put(`/api/ui/scheduled-tasks/${data.id}`, data)
/** 删除定时任务 */
export const deleteScheduledTask = data =>
  api.delete(`/api/ui/scheduled-tasks/${data.id}`)
/** 运行任务 */
export const runTask = data =>
  api.post(`/api/ui/scheduled-tasks/${data.id}/run`)

// cron_expression
// :
// "0 2 * * *"
// is_enabled
// :
// true
// job_type
// :
// "tmdb_auto_map"
// name
// :
// "22222222222"

/** ----------------------------token相关开始------------------------- */
/** 获取token列表 */
export const getTokenList = () => api.get('/api/ui/tokens')
/** 增加token */
export const addToken = data => api.post('/api/ui/tokens', data)
/** 获取ua配置 */
export const getUaMode = () => api.get('/api/ui/config/ua_filter_mode')
/** 获取ua配置 */
export const setUaMode = data => api.put('/api/ui/config/ua_filter_mode', data)
/** 获取自定义域名 */
export const getCustomDomain = () => api.get('/api/ui/config/custom_api_domain')
/** 设置自定义域名 */
export const setCustomDomain = data =>
  api.put('/api/ui/config/custom_api_domain', data)
/** token请求日志 */
export const getTokenLog = data =>
  api.get(`/api/ui/tokens/${data.tokenId}/logs`)
/** 切换token可用状态 */
export const toggleTokenStatus = data =>
  api.put(`api/ui/tokens/${data.tokenId}/toggle`)
/** 删除token */
export const deleteToken = data => api.delete(`/api/ui/tokens/${data.tokenId}`)
/** 获取ua规则 */
export const getUaRules = () => api.get('/api/ui/ua-rules')
/** 添加ua规则 */
export const addUaRule = data => api.post('/api/ui/ua-rules', data)
/** 删除ua规则 */
export const deleteUaRule = data => api.delete(`/api/ui/ua-rules/${data.id}`)

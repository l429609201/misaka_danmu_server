import api from './fetch'

/** -------------------------------------------------用户相关开始------------------------------------------------- */
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

/** 修改密码 */
export const changePassword = data =>
  api.put(
    '/api/ui/auth/users/me/password',
    JSON.stringify({
      old_password: data.oldPassword,
      new_password: data.newPassword,
    })
  )

/** ---------------------------------------------------首页接口------------------------------------------------ */
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

/** ---------------------------------------------------任务相关开始------------------------------------------------ */
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

/** ---------------------------------------------------token相关开始------------------------------------------------ */
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

/** ---------------------------------------------- webhook ----------------------------------------------*/
/** 获取webhook apikey */
export const getWebhookApikey = () => api.get('/api/ui/config/webhook_api_key')
/** 刷新webhookapi key */
export const refreshWebhookApikey = () =>
  api.post('/api/ui/config/webhook_api_key/regenerate')
/** 获取webhook 域名 */
export const getWebhookDomain = () =>
  api.get('/api/ui/config/webhook_custom_domain')
/** 设置webhook自定义域名 value */
export const setWebhookApikey = data =>
  api.put('/api/ui/config/webhook_custom_domain', data)
/** webhook可用服务 */
export const getWebhookServices = () => api.get('/api/ui/webhooks/available')

/** ---------------------------------------------- Bangumi  ----------------------------------------------*/
/** 获取bangumi api配置 */
export const getBangumiConfig = () => api.get('/api/ui/config/bangumi')
/** 设置bangumi api配置
 * bangumi_client_id
 * bangumi_client_secret
 */
export const setBangumiConfig = data => api.put('/api/ui/config/bangumi', data)
/** 获取授权信息 */
export const getBangumiAuth = () => api.get('/api/bgm/auth/state')
/** 获取授权链接 */
export const getBangumiAuthUrl = () => api.get('/api/bgm/auth/url')
/** 注销授权 */
export const logoutBangumiAuth = () => api.delete('/api/bgm/auth')

/** ---------------------------------------------- 豆瓣、tmdb、tvdb配置----------------------------------------------  */
/** 获取tmdb配置 */
export const getTmdbConfig = () => api.get('/api/ui/config/tmdb')
/** 设置tmdb配置 */
export const setTmdbConfig = data => api.put('/api/ui/config/tmdb', data)
/** 获取豆瓣配置 */
export const getDoubanConfig = () => api.get('/api/ui/config/douban_cookie')
/** 设置豆瓣配置 */
export const setDoubanConfig = data =>
  api.put('/api/ui/config/douban_cookie', data)
/** 获取tvdb配置 */
export const getTvdbConfig = () => api.get('/api/ui/config/tvdb_api_key')
/** 设置tvdb配置 */
export const setTvdbConfig = data =>
  api.put('/api/ui/config/tvdb_api_key', data)

/** ---------------------------------------------- 搜索源配置----------------------------------------------  */
/** 获取刮削器配置 */
export const getScrapers = () => api.get('/api/ui/scrapers')
/** 保存刮削器状态（排序/开启状态） */
export const setScrapers = data => api.put('/api/ui/scrapers', data)
/** 设置单个刮削器配置 */
export const setSingleScraper = data =>
  api.put(`/api/ui/scrapers/${data.name}/config`, data)
/** 获取单个刮削器配置 */
export const getSingleScraper = data =>
  api.get(`/api/ui/scrapers/${data.name}/config`)

/** 获取元信息搜索 配置 */
export const getMetaData = () => api.get('/api/ui/metadata-sources')
/** 设置元数据 配置 */
export const setMetaData = data => api.put('/api/ui/metadata-sources', data)

/** 获取bi站登录信息 */
export const getbiliUserinfo = () =>
  api.post('/api/ui/scrapers/bilibili/actions/get_login_info')
/** bilibili 登录二维码 */
export const getbiliLoginQrcode = () =>
  api.post('/api/ui/scrapers/bilibili/actions/generate_qrcode')
/** 轮训bili登录 */
export const pollBiliLogin = data =>
  api.post('/api/ui/scrapers/bilibili/actions/poll_login', data)
/** 注销bili登录 */
export const biliLogout = () =>
  api.post('/api/ui/scrapers/bilibili/actions/logout')

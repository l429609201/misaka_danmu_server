import api from './fetch'

/** 获取应用版本号 */
export const getVersion = () => api.get('/api/ui/version')

/** 检查应用更新 */
export const checkAppUpdate = (forceRefresh = false) =>
  api.get(`/api/ui/version/check?force_refresh=${forceRefresh}`)

/** 获取历史版本列表 */
export const getReleaseHistory = (limit = 10) =>
  api.get(`/api/ui/version/releases?limit=${limit}`)

/** 获取 Docker 状态 */
export const getDockerStatus = () => api.get('/api/ui/docker/status')

/** 获取容器资源使用统计 */
export const getDockerStats = () => api.get('/api/ui/docker/stats')

/** 重启服务 */
export const restartService = () => api.post('/api/ui/restart')

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
      oldPassword: data.oldPassword,
      newPassword: data.newPassword,
    })
  )

/** 获取当前用户所有会话 */
export const getUserSessions = () => api.get('/api/ui/auth/sessions')

/** 踢出指定会话 */
export const revokeSession = sessionId =>
  api.delete(`/api/ui/auth/sessions/${sessionId}`)

/** 踢出所有其他会话 */
export const revokeOtherSessions = () =>
  api.delete('/api/ui/auth/sessions/others/all')

/** ---------------------------------------------------首页接口------------------------------------------------ */
/** 获取日志 */
export const getLogs = (options = {}) =>
  api.get('/api/ui/logs', null, {
    ...options,
  })

/** 获取日志文件列表 */
export const getLogFiles = () => api.get('/api/ui/logs/files')

/** 获取指定日志文件内容 */
export const getLogFileContent = (filename, tail = 500) =>
  api.get(`/api/ui/logs/files/${encodeURIComponent(filename)}`, { tail })

/** 匹配测试 */
export const getMatchTest = data =>
  api.post(
    `/api/v1/${data.apiToken}/match`,
    JSON.stringify({ fileName: data.fileName })
  )

/** 搜索分集测试 */
export const searchEpisodesTest = data =>
  api.get(
    `/api/v1/${data.apiToken}/search/episodes?anime=${encodeURIComponent(data.anime)}${data.episode ? `&episode=${encodeURIComponent(data.episode)}` : ''}`
  )

/** 搜索作品测试 */
export const searchAnimeTest = data =>
  api.get(
    `/api/v1/${data.apiToken}/search/anime?keyword=${encodeURIComponent(data.keyword)}`
  )

/** 番剧详情测试 */
export const getBangumiDetailTest = data =>
  api.get(`/api/v1/${data.apiToken}/bangumi/${data.bangumiId}`)

/** 弹幕获取测试 */
export const getCommentTest = data =>
  api.get(`/api/v1/${data.apiToken}/comment/${data.episodeId}`)

/** 文件名识别测试 */
export const parseFilenameTest = data =>
  api.post('/api/ui/tools/parse-filename', { fileName: data.fileName })

/** 清除搜索缓存 */
export const clearSearchCache = () => api.post('/api/ui/cache/clear')

/** 搜索结果 */
export const getSearchResult = (data, onProgress) => {
  const params = {
    keyword: data.keyword,
    page: data.page || 1,
    pageSize: data.pageSize || 10,
  }
  // 添加可选的过滤参数
  if (data.typeFilter && data.typeFilter !== 'all') {
    params.typeFilter = data.typeFilter
  }
  if (data.yearFilter && data.yearFilter !== 'all') {
    params.yearFilter = data.yearFilter
  }
  if (data.providerFilter && data.providerFilter !== 'all') {
    params.providerFilter = data.providerFilter
  }
  if (data.titleFilter) {
    params.titleFilter = data.titleFilter
  }
  return api.get(
    '/api/ui/search/provider',
    params,
    {
      onDownloadProgress: onProgress,
    }
  )
}

/** 导入弹幕  */
export const importDanmu = data => api.post('/api/ui/import', data)

/** 编辑导入获取集 */
export const getEditEpisodes = data => api.get('/api/ui/search/episodes', data)
/** 获取已存在集数 */
export const getInLibraryEpisodes = data =>
  api.get('/api/ui/library/episodes-by-title', {
    title: data.title,
    season: data.season,
  })

/** 提交编辑导入 */
export const importEdit = data => api.post('/api/ui/import/edited', data)

/** 搜索tmdb */
export const getTmdbSearch = data =>
  api.get(
    `/api/ui/metadata/tmdb/search?keyword=${encodeURIComponent(data.keyword)}&mediaType=${data.mediaType}`
  )
/** 搜索tvdb */
export const getTvdbSearch = data =>
  api.get(
    `/api/ui/metadata/tvdb/search?keyword=${encodeURIComponent(data.keyword)}&mediaType=${data.mediaType}`
  )
/** 搜索tmdb剧集组 */
export const getEgidSearch = data =>
  api.post(`/api/ui/metadata/tmdb/actions/get_episode_groups`, {
    tmdbId: data.tmdbId,
  })

/** 查看所有分集 */
export const getAllEpisode = data =>
  api.post(`/api/ui/metadata/tmdb/actions/get_all_episodes`, {
    egid: data.egid,
    tmdbId: data.tmdbId,
  })

/** 获取本地剧集组JSON（支持URL和本地路径） */
export const fetchLocalEpisodeGroupUrl = data =>
  api.post(`/api/ui/local-episode-group/fetch`, { url: data.url })

/** 应用本地剧集组映射 */
export const applyLocalEpisodeGroup = data =>
  api.post(`/api/ui/local-episode-group/apply`, {
    tmdbId: data.tmdbId,
    localEpisodeGroup: data.localEpisodeGroup,
  })

/** 获取已保存的剧集组详情（从数据库读取） */
export const getEpisodeGroupDetail = (groupId) =>
  api.get(`/api/ui/local-episode-group/detail?groupId=${encodeURIComponent(groupId)}`)

/** 搜索BGM */
export const getBgmSearch = data =>
  api.get(
    `/api/ui/metadata/bangumi/search?keyword=${encodeURIComponent(data.keyword)}`
  )

/** 豆瓣搜索 */
export const getDoubanSearch = data =>
  api.get(
    `/api/ui/metadata/douban/search?keyword=${encodeURIComponent(data.keyword)}`
  )
/** imdb搜索 */
export const getImdbSearch = data =>
  api.get(
    `/api/ui/metadata/imdb/search?keyword=${encodeURIComponent(data.keyword)}&mediaType=${data.mediaType}`
  )

/** ---------------------------------------------------任务相关开始------------------------------------------------ */
/** 任务列表 */
export const getTaskList = data => api.get('/api/ui/tasks', data)
/** 暂停任务 */
export const pauseTask = data => api.post(`/api/ui/tasks/${data.taskId}/pause`)
/** 继续任务 */
export const resumeTask = data =>
  api.post(`/api/ui/tasks/${data.taskId}/resume`)
/** 删除任务 */
export const deleteTask = data => api.delete(`/api/ui/tasks/${data.taskId}`, { params: { force: data.force || false } })
/** 中止任务 */
export const stopTask = data => api.post(`/api/ui/tasks/${data.taskId}/abort`, { force: data.force || false })
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

/** 获取可用的定时任务类型 */
export const getAvailableScheduledJobs = () =>
  api.get('/api/ui/scheduled-tasks/available-jobs')

/** 获取流控状态 */
export const getRateLimitStatus = () => api.get('/api/ui/rate-limit/status')

/** ---------------------------------------------------token相关开始------------------------------------------------ */
/** 获取token列表 */
export const getTokenList = () => api.get('/api/ui/tokens')
/** 增加token */
export const addToken = data => api.post('/api/ui/tokens', data)
/** 获取UA过滤模式 */
export const getUaMode = () => api.get('/api/ui/config/uaFilterMode')
/** 设置UA过滤模式 */
export const setUaMode = data => api.put('/api/ui/config/uaFilterMode', data)
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
/** 编辑token */
export const editToken = data => api.put(`/api/ui/tokens/${data.id}`, data)
/** 重置token调用次数 */
export const resetTokenCounter = data =>
  api.post(`/api/ui/tokens/${data.id}/reset`)
/** 获取ua规则 */
export const getUaRules = () => api.get('/api/ui/ua-rules')
/** 添加ua规则 */
export const addUaRule = data => api.post('/api/ui/ua-rules', data)
/** 删除ua规则 */
export const deleteUaRule = data => api.delete(`/api/ui/ua-rules/${data.id}`)

/** 弹幕输出控制 单源输出总数 */
export const getDanmuOutputTotal = () =>
  api.get('/api/ui/config/danmakuOutputLimitPerSource')

export const setDanmuOutputTotal = data =>
  api.put('/api/ui/config/danmakuOutputLimitPerSource', data)
/** 弹幕输出控制 合并输出开关 */
export const getDanmakuMergeOutputEnabled = () =>
  api.get('/api/ui/config/danmakuMergeOutputEnabled')
/** 弹幕输出控制 合并输出开关 */
export const setDanmakuMergeOutputEnabled = data =>
  api.put('/api/ui/config/danmakuMergeOutputEnabled', data)
/** 弹幕输出配置 简繁转换 */
export const getDanmakuChConvert = () =>
  api.get('/api/ui/config/danmakuChConvert')
/** 弹幕输出配置 简繁转换 */
export const setDanmakuChConvert = data =>
  api.put('/api/ui/config/danmakuChConvert', data)
/** 弹幕输出配置 简繁转换优先级 */
export const getDanmakuChConvertPriority = () =>
  api.get('/api/ui/config/danmakuChConvertPriority')
/** 弹幕输出配置 简繁转换优先级 */
export const setDanmakuChConvertPriority = data =>
  api.put('/api/ui/config/danmakuChConvertPriority', data)
/** 弹幕输出配置 随机颜色模式 */
export const getDanmakuRandomColorMode = () =>
  api.get('/api/ui/config/danmakuRandomColorMode')
/** 弹幕输出配置 随机颜色模式 */
export const setDanmakuRandomColorMode = data =>
  api.put('/api/ui/config/danmakuRandomColorMode', data)
/** 弹幕输出配置 随机颜色色板 */
export const getDanmakuRandomColorPalette = () =>
  api.get('/api/ui/config/danmakuRandomColorPalette')
/** 弹幕输出配置 随机颜色色板 */
export const setDanmakuRandomColorPalette = data =>
  api.put('/api/ui/config/danmakuRandomColorPalette', data)
/** 弹幕输出配置 黑名单开关 */
export const getDanmakuBlacklistEnabled = () =>
  api.get('/api/ui/config/danmakuBlacklistEnabled')
/** 弹幕输出配置 黑名单开关 */
export const setDanmakuBlacklistEnabled = data =>
  api.put('/api/ui/config/danmakuBlacklistEnabled', data)
/** 弹幕输出配置 黑名单规则 */
export const getDanmakuBlacklistPatterns = () =>
  api.get('/api/ui/config/danmakuBlacklistPatterns')
/** 弹幕输出配置 黑名单规则 */
export const setDanmakuBlacklistPatterns = data =>
  api.put('/api/ui/config/danmakuBlacklistPatterns', data)

/** 获取匹配后备机制状态 */
export const getMatchFallback = () =>
  api.get('/api/ui/config/matchFallbackEnabled')
/** 设置匹配后备机制状态 */
export const setMatchFallback = data =>
  api.put('/api/ui/config/matchFallbackEnabled', data)

/** 获取匹配后备黑名单 */
export const getMatchFallbackBlacklist = () =>
  api.get('/api/ui/config/matchFallbackBlacklist')
/** 设置匹配后备黑名单 */
export const setMatchFallbackBlacklist = data =>
  api.put('/api/ui/config/matchFallbackBlacklist', data)

/** 获取自定义弹幕路径配置 */
export const getCustomDanmakuPath = () =>
  api.get('/api/ui/config/customDanmakuPath')
/** 设置自定义弹幕路径配置 */
export const setCustomDanmakuPath = data =>
  api.put('/api/ui/config/customDanmakuPath', data)

/** 获取匹配后备Token配置 */
export const getMatchFallbackTokens = () =>
  api.get('/api/ui/config/matchFallbackTokens')
/** 设置匹配后备Token配置 */
export const setMatchFallbackTokens = data =>
  api.put('/api/ui/config/matchFallbackTokens', data)

/** 获取后备搜索状态 */
export const getSearchFallback = () =>
  api.get('/api/ui/config/searchFallbackEnabled')
/** 设置后备搜索状态 */
export const setSearchFallback = data =>
  api.put('/api/ui/config/searchFallbackEnabled', data)

/** ---------------------------------------------- webhook ----------------------------------------------*/
/** 获取webhook apikey */
export const getWebhookApikey = () => api.get('/api/ui/config/webhookApiKey')
/** 刷新webhookapi key */
export const refreshWebhookApikey = () =>
  api.post('/api/ui/config/webhookApiKey/regenerate')
/** 获取webhook 域名 */
export const getWebhookDomain = () =>
  api.get('/api/ui/config/webhook_custom_domain')
/** 设置webhook自定义域名 */
export const setWebhookDomain = data =>
  api.put('/api/ui/config/webhook_custom_domain', data)
/** webhook可用服务 */
export const getWebhookServices = () => api.get('/api/ui/webhooks/available')
/** 获取webhook设置 */
export const getWebhookSettings = () => api.get('/api/ui/settings/webhook')
/** 设置webhook设置 */
export const setWebhookSettings = data =>
  api.put('/api/ui/settings/webhook', data)
/** 获取webhook任务列表 */
export const getWebhookTasks = data => api.get('/api/ui/webhook-tasks', data)
/** 批量删除webhook任务 */
export const deleteWebhookTasks = data =>
  api.post('/api/ui/webhook-tasks/delete-bulk', data)
/** 清空所有webhook任务 */
export const clearAllWebhookTasks = () =>
  api.delete('/api/ui/webhook-tasks/clear-all')
/** 立即执行webhook任务 */
export const runWebhookTasksNow = data =>
  api.post('/api/ui/webhook-tasks/run-now', data)

/** ---------------------------------------------- Bangumi  ----------------------------------------------*/
/** 获取bangumi api配置 */
export const getBangumiConfig = () => api.get('/api/ui/config/provider/bangumi')
/** 设置bangumi api配置 */
export const setBangumiConfig = data =>
  api.put('/api/ui/config/provider/bangumi', data)
/** 获取授权信息 */
export const getBangumiAuth = () =>
  api.post('/api/ui/metadata/bangumi/actions/get_auth_state')
/** 获取授权链接 */
export const getBangumiAuthUrl = () =>
  api.post('/api/ui/metadata/bangumi/actions/get_auth_url')
/** 注销授权 */
export const logoutBangumiAuth = () =>
  api.post('/api/ui/metadata/bangumi/actions/logout')

/** ------------------------------------------ 豆瓣、tmdb、tvdb配置、代理------------------------------------------  */
/** 获取tmdb配置 */
export const getTmdbConfig = () => api.get('/api/ui/config/provider/tmdb')
/** 设置tmdb配置 */
export const setTmdbConfig = data =>
  api.put('/api/ui/config/provider/tmdb', data)
/** 获取豆瓣配置 */
export const getDoubanConfig = () => api.get('/api/ui/config/provider/douban')
/** 设置豆瓣配置 */
export const setDoubanConfig = data =>
  api.put('/api/ui/config/provider/douban', data)
/** 获取tvdb配置 */
export const getTvdbConfig = () => api.get('/api/ui/config/provider/tvdb')
/** 设置tvdb配置 */
export const setTvdbConfig = data =>
  api.put('/api/ui/config/provider/tvdb', data)

/** 获取代理配置 */
export const getProxyConfig = () => api.get('/api/ui/config/proxy')
/** 设置代理配置 */
export const setProxyConfig = data => api.put('/api/ui/config/proxy', data)

/** 测试代理连接 */
export const testProxy = data => api.post('/api/ui/proxy/test', data)

/** 获取受信任的反向代理IP */
export const getTrustedProxiesConfig = () =>
  api.get('/api/ui/config/trustedProxies')
/** 设置受信任的反向代理IP */
export const setTrustedProxiesConfig = data =>
  api.put('/api/ui/config/trustedProxies', data)
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

/** 获取资源仓库配置 */
export const getResourceRepo = () => api.get('/api/ui/scrapers/resource-repo')
/** 保存资源仓库配置 */
export const saveResourceRepo = data => api.put('/api/ui/scrapers/resource-repo', data)
/** 获取资源包版本信息 */
export const getScraperVersions = () => api.get('/api/ui/scrapers/versions')
/** 加载弹幕源资源 */
export const loadScraperResources = data => api.post('/api/ui/scrapers/load-resources', data)
/** 备份弹幕源 */
export const backupScrapers = () => api.post('/api/ui/scrapers/backup')
/** 还原弹幕源 */
export const restoreScrapers = () => api.post('/api/ui/scrapers/restore')
/** 获取备份信息 */
export const getBackupInfo = () => api.get('/api/ui/scrapers/backup-info')
/** 重载弹幕源 */
export const reloadScrapers = () => api.post('/api/ui/scrapers/reload')
/** 删除弹幕源备份 */
export const deleteScraperBackup = () => api.delete('/api/ui/scrapers/backup')
/** 删除当前弹幕源 */
export const deleteCurrentScrapers = () => api.delete('/api/ui/scrapers/current')
/** 删除当前源和备份源 */
export const deleteAllScrapers = () => api.delete('/api/ui/scrapers/all')
/** 获取自动更新配置 */
export const getScraperAutoUpdate = () => api.get('/api/ui/scrapers/auto-update')
/** 保存自动更新配置 */
export const saveScraperAutoUpdate = data => api.put('/api/ui/scrapers/auto-update', data)
/** 获取全量替换配置 */
export const getScraperFullReplace = () => api.get('/api/ui/scrapers/full-replace')
/** 保存全量替换配置 */
export const saveScraperFullReplace = data => api.put('/api/ui/scrapers/full-replace', data)

/** 启动下载任务（新版后台任务模式） */
export const startScraperDownload = data => api.post('/api/ui/scrapers/download/start', data)
/** 获取下载任务状态 */
export const getScraperDownloadStatus = taskId => api.get(`/api/ui/scrapers/download/status/${taskId}`)
/** 获取当前下载任务 */
export const getCurrentScraperDownload = () => api.get('/api/ui/scrapers/download/current')
/** 取消下载任务 */
export const cancelScraperDownload = taskId => api.post(`/api/ui/scrapers/download/cancel/${taskId}`)
/** 获取GitHub Token */
export const getGithubToken = () => api.get('/api/ui/config/github-token')
/** 保存GitHub Token */
export const saveGithubToken = data => api.post('/api/ui/config/github-token', data)
/** 验证GitHub Token */
export const verifyGithubToken = data => api.post('/api/ui/config/github-token/verify', data)
/** 上传弹幕源离线包 */
export const uploadScraperPackage = (data, config) => api.post('/api/ui/scrapers/upload-package', data, config)

/** 获取元信息搜索 配置 */
export const getMetaData = () => api.get('/api/ui/metadata-sources')
/** 设置元数据 配置 */
export const setMetaData = data => api.put('/api/ui/metadata-sources', data)

/** 获取单个元数据源的配置 (新) */
export const getProviderConfig = data =>
  api.get(`/api/ui/metadata-sources/${data.providerName}/config`)

/** 设置单个元数据源的配置 (新) */
export const setProviderConfig = (providerName, data) =>
  api.put(`/api/ui/metadata-sources/${providerName}/config`, data)
/** ---------------------------------------------- 全局过滤设置 ----------------------------------------------  */
/** 获取全局过滤规则 */
export const getGlobalFilter = () => api.get('/api/ui/settings/global-filter')
/** 更新全局过滤规则 */
export const setGlobalFilter = data =>
  api.put('/api/ui/settings/global-filter', data)
/** 获取全局过滤默认规则 */
export const getGlobalFilterDefaults = () => api.get('/api/ui/settings/global-filter/defaults')
/** 获取搜索源默认分集黑名单 */
export const getScraperDefaultBlacklist = (providerName) =>
  api.get(`/api/ui/scrapers/${providerName}/default-blacklist`)
/** 获取通用分集黑名单规则 */
export const getCommonBlacklist = () => api.get('/api/ui/scrapers/common-blacklist')

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

/** 通用 scraper action 调用 */
export const executeScraperAction = (providerName, actionName, payload = {}) =>
  api.post(`/api/ui/scrapers/${providerName}/actions/${actionName}`, payload)

/** ----------------------------------------------弹幕库----------------------------------------------  */
/** 弹幕库列表 (支持搜索和分页) */
export const getAnimeLibrary = data => api.get('/api/ui/library', data)
/** 删除单个资源 */
export const deleteAnime = data =>
  api.delete(`/api/ui/library/anime/${data.animeId}?deleteFiles=${data.deleteFiles !== false}`)
/** 获取影视信息 */
export const getAnimeDetail = data =>
  api.get(`/api/ui/library/anime/${data.animeId}/details`)

/** 保存影视信息 */
export const setAnimeDetail = data =>
  api.put(`/api/ui/library/anime/${data.animeId}`, data)

/** 创建自定义作品条目 */
export const createAnimeEntry = data => api.post('/api/ui/library/anime', data)

/** 为作品添加数据源 */
export const addSourceToAnime = data =>
  api.post(`/api/ui/library/anime/${data.animeId}/sources`, data)

/** 批量手动导入 */
export const batchManualImport = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/batch-import`, data)

/** 校验并解析导入URL */
export const validateImportUrl = data =>
  api.post('/api/ui/validate-url', data)

/** 从URL导入弹幕 */
export const importFromUrl = data =>
  api.post('/api/ui/import-from-url', data)

/** 获取影视的资源 */
export const getAnimeSource = data =>
  api.get(`/api/ui/library/anime/${data.animeId}/sources`)

/** 检测关联冲突 */
export const checkReassociationConflicts = data =>
  api.post(`/api/ui/library/anime/${data.sourceAnimeId}/reassociate/check`, {
    targetAnimeId: data.targetAnimeId,
  })

/** 关联数据源 */
export const setAnimeSource = data =>
  api.post(`/api/ui/library/anime/${data.sourceAnimeId}/reassociate`, {
    targetAnimeId: data.targetAnimeId,
  })

/** 关联数据源并解决冲突 */
export const reassociateWithResolution = data =>
  api.post(`/api/ui/library/anime/${data.sourceAnimeId}/reassociate/resolve`, {
    targetAnimeId: data.targetAnimeId,
    resolutions: data.resolutions,
  })

/** 批量删除数据源 */
export const deleteAnimeSource = data =>
  api.post('/api/ui/library/sources/delete-bulk', { sourceIds: data.sourceIds, deleteFiles: data.deleteFiles !== false })

/** 删除单个数据源 */
export const deleteAnimeSourceSingle = data =>
  api.delete(`/api/ui/library/source/${data.sourceId}?deleteFiles=${data.deleteFiles !== false}`)

/** 数据源收藏状态 */
export const toggleSourceFavorite = data =>
  api.put(`/api/ui/library/source/${data.sourceId}/favorite`)

/** 数据源增量定时状态 */
export const toggleSourceIncremental = data =>
  api.put(`/api/ui/library/source/${data.sourceId}/toggle-incremental-refresh`)

/** 增量更新 */
export const incrementalUpdate = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/refresh?mode=incremental`)

/** 全量刷新 */
export const fullSourceUpdate = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/refresh?mode=full`)

/** 获取分集 */
export const getEpisodes = data => {
  return api.get(`/api/ui/library/source/${data.sourceId}/episodes`, {
    page: data.page,
    pageSize: data.pageSize,
  })
}

/** 编辑分集信息 */
export const editEpisode = data =>
  api.put(`/api/ui/library/episode/${data.episodeId}`, data)
/** 手动导入集 */
export const manualImportEpisode = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/manual-import`, data)

/** 批量删除集 */
export const deleteAnimeEpisode = data =>
  api.post('/api/ui/library/episodes/delete-bulk', { episodeIds: data.episodeIds, deleteFiles: data.deleteFiles !== false })

/** 刷新集弹幕 */
export const refreshEpisodeDanmaku = data =>
  api.post(`/api/ui/library/episode/${data.id}/refresh`)

/** 批量刷新集弹幕 */
export const refreshEpisodesBulk = data =>
  api.post('/api/ui/library/episodes/refresh-bulk', data)

/** 删除集 */
export const deleteAnimeEpisodeSingle = data =>
  api.delete(`/api/ui/library/episode/${data.id}?deleteFiles=${data.deleteFiles !== false}`)

/** 重整集数 */
export const resetEpisode = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/reorder-episodes`)

/** 集数偏移 */
export const offsetEpisodes = data =>
  api.post('/api/ui/library/episodes/offset', data)

/** 获取弹幕详情 */
export const getDanmakuDetail = data =>
  api.get(`/api/ui/comment/${data.id}`, {
    page: data.page,
    pageSize: data.pageSize,
  })

/** 刷新海报 */
export const refreshPoster = data =>
  api.post(`/api/ui/library/anime/${data.animeId}/refresh-poster`, data)

/** 精准获取资源信息 */
export const getAnimeInfoAsSource = data => {
  const detailsUrlMap = {
    bangumi: `/api/ui/metadata/bangumi/details/${data.currentId}`,
    douban: `/api/ui/metadata/douban/details/${data.currentId}`,
    tvdb: `/api/ui/metadata/tvdb/details/${data.currentId}`,
    imdb: `/api/ui/metadata/imdb/details/${data.currentId}`,
    tmdb: `/api/ui/metadata/tmdb/details/${data.mediaType}/${data.currentId}`,
  }
  const detailsUrl = detailsUrlMap[data.source]
  return api.get(detailsUrl)
}

/** ----------------------------------------------外部控制----------------------------------------------  */
/** 获取apikey */
export const getControlApiKey = () => api.get('/api/ui/config/externalApiKey')

/** 刷新apikey */
export const refreshControlApiKey = () =>
  api.post('/api/ui/config/externalApiKey/regenerate')

/** apikey 访问日志 */
export const getControlApiKeyLog = () => api.get('api/ui/external-logs')

/** 识别词管理 */
export const getRecognition = () =>
  api.get('/api/ui/settings/title-recognition')

export const setRecognition = data =>
  api.put('/api/ui/settings/title-recognition', data)

/** 获取TMDB反查配置 */
export const getTmdbReverseLookupConfig = () => api.get('/api/ui/config/tmdbReverseLookup')

/** 保存TMDB反查配置 */
export const saveTmdbReverseLookupConfig = (data) => api.post('/api/ui/config/tmdbReverseLookup', data)

/** 通用配置管理 */
export const getConfig = (key) => api.get(`/api/ui/config/${key}`)
export const setConfig = (key, value) => api.put(`/api/ui/config/${key}`, { value })
/** 获取参数配置 Schema */
export const getConfigSchema = () => api.get('/api/ui/config/schema/parameters')
export const getDefaultAIPrompts = () => api.get('/api/ui/config/ai/default-prompts')

/** AI 余额查询 */
export const getAIBalance = () => api.get('/api/ui/config/ai/balance')

/** AI 调用统计 */
export const getAIMetrics = (hours = 24, source = 'db') => api.get(`/api/ui/config/ai/metrics?hours=${hours}&source=${source}`)

/** 清空 AI 缓存 */
export const clearAICache = () => api.post('/api/ui/config/ai/cache/clear')

/** 获取 AI 模型列表 */
export const getAIModels = (provider, refresh = false) => api.get(`/api/ui/config/ai/models?provider=${provider}&refresh=${refresh}`)

/** ---------------------------------------------- 媒体服务器 ----------------------------------------------  */
/** 获取所有媒体服务器 */
export const getMediaServers = () => api.get('/api/ui/media-servers')

/** 创建媒体服务器 */
export const createMediaServer = (data) => api.post('/api/ui/media-servers', data)

/** 更新媒体服务器 */
export const updateMediaServer = (serverId, data) => api.put(`/api/ui/media-servers/${serverId}`, data)

/** 删除媒体服务器 */
export const deleteMediaServer = (serverId) => api.delete(`/api/ui/media-servers/${serverId}`)

/** 测试媒体服务器连接 */
export const testMediaServerConnection = (serverId) => api.post(`/api/ui/media-servers/${serverId}/test`)

/** 获取媒体服务器的媒体库列表 */
export const getMediaServerLibraries = (serverId) => api.get(`/api/ui/media-servers/${serverId}/libraries`)

/** 扫描媒体服务器 */
export const scanMediaServer = (serverId, libraryIds = null) =>
  api.post(`/api/ui/media-servers/${serverId}/scan`, { library_ids: libraryIds })

/** 获取媒体项列表 */
export const getMediaItems = (params) => api.get('/api/ui/media-items', params)

/** 获取作品列表(按作品分组) */
export const getMediaWorks = (params) => api.get('/api/ui/media-works', params)

/** 获取剧集的季度信息 */
export const getShowSeasons = (title, serverId) => api.get(`/api/ui/shows/${encodeURIComponent(title)}/seasons`, { server_id: serverId })

/** 获取某一季的分集列表 */
export const getSeasonEpisodes = (title, season, serverId, page = 1, pageSize = 100) =>
  api.get(`/api/ui/shows/${encodeURIComponent(title)}/seasons/${season}/episodes`, {
    server_id: serverId,
    page,
    page_size: pageSize
  })

/** 更新媒体项 */
export const updateMediaItem = (itemId, data) => api.put(`/api/ui/media-items/${itemId}`, data)

/** 删除媒体项 */
export const deleteMediaItem = (itemId) => api.delete(`/api/ui/media-items/${itemId}`)

/** 批量删除媒体项 */
export const batchDeleteMediaItems = (payload) => api.post('/api/ui/media-items/batch-delete', payload)

/** 导入媒体项 */
export const importMediaItems = (data) => api.post('/api/ui/media-items/import', data)

/** 获取未导入媒体项数量 */
export const getUnimportedCount = (serverId, mediaType) => api.get('/api/ui/media-items/unimported-count', { server_id: serverId, media_type: mediaType })

/** 一键导入全部未导入的媒体项 */
export const importAllUnimported = (data) => api.post('/api/ui/media-items/import-all-unimported', data)

// ==================== 本地弹幕扫描 ====================

/** 浏览本地目录 */
export const browseDirectory = (fileitem, sort = 'name') => api.post('/api/ui/local-scan/browse', fileitem, { params: { sort } })

/** 获取上次使用的扫描路径 */
export const getLastScanPath = () => api.get('/api/ui/local-scan/last-path')

/** 保存扫描路径 */
export const saveScanPath = (scanPath) => api.post('/api/ui/local-scan/save-path', { scanPath })

/** 扫描本地弹幕文件 */
export const scanLocalDanmaku = (scanPath) => api.post('/api/ui/local-scan', { scanPath })

/** 创建文件夹 */
export const createFolder = (parentPath, folderName) => api.post('/api/ui/local-scan/create-folder', { parentPath, folderName })

/** 删除文件夹 */
export const deleteFolder = (folderPath) => api.delete('/api/ui/local-scan/delete-folder', { params: { folderPath } })

/** 获取本地弹幕项列表 */
export const getLocalItems = (params) => api.get('/api/ui/local-items', params)

/** 获取本地作品列表(按作品分组) */
export const getLocalWorks = (params) => api.get('/api/ui/local-works', params)

/** 获取电影的弹幕文件列表 */
export const getLocalMovieFiles = (title, year = null, page = 1, pageSize = 100) =>
  api.get(`/api/ui/local-movies/${encodeURIComponent(title)}/files`, {
    year,
    page,
    page_size: pageSize
  })

/** 获取本地剧集的季度信息 */
export const getLocalShowSeasons = (title) => api.get(`/api/ui/local-shows/${encodeURIComponent(title)}/seasons`)

/** 获取本地某一季的分集列表 */
export const getLocalSeasonEpisodes = (title, season, page = 1, pageSize = 100) =>
  api.get(`/api/ui/local-shows/${encodeURIComponent(title)}/seasons/${season}/episodes`, {
    page,
    page_size: pageSize
  })

/** 更新本地弹幕项 */
export const updateLocalItem = (itemId, data) => api.put(`/api/ui/local-items/${itemId}`, data)

/** 删除本地弹幕项 */
export const deleteLocalItem = (itemId) => api.delete(`/api/ui/local-items/${itemId}`)

/** 批量删除本地弹幕项 */
export const batchDeleteLocalItems = (itemIds) => api.post('/api/ui/local-items/batch-delete', { itemIds })

/** 导入本地弹幕项 */
export const importLocalItems = (data) => api.post('/api/ui/local-items/import', data)

// ==================== 弹幕存储批量操作 ====================

/** 预览批量迁移 */
export const previewMigrateDanmaku = (data) => api.post('/api/ui/danmaku-storage/preview-migrate', data)

/** 批量迁移弹幕文件 */
export const batchMigrateDanmaku = (data) => api.post('/api/ui/danmaku-storage/batch-migrate', data)

/** 预览批量重命名 */
export const previewRenameDanmaku = (data) => api.post('/api/ui/danmaku-storage/preview-rename', data)

/** 批量重命名弹幕文件 */
export const batchRenameDanmaku = (data) => api.post('/api/ui/danmaku-storage/batch-rename', data)

/** 预览应用模板 */
export const previewDanmakuTemplate = (data) => api.post('/api/ui/danmaku-storage/preview-template', data)

/** 应用新模板 */
export const applyDanmakuTemplate = (data) => api.post('/api/ui/danmaku-storage/apply-template', data)

/** 获取模板变量列表 */
export const getTemplateVariables = () => api.get('/api/ui/danmaku-storage/template-variables')

// --- 追更与标记管理 ---

/** 获取所有源（按番剧分组）用于批量管理，支持分页和过滤 */
export const getIncrementalRefreshSources = ({ page = 1, pageSize = 20, keyword = '', favoriteFilter = 'all', refreshFilter = 'all', typeFilter = 'all' } = {}) => {
  const queryParams = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
    keyword,
    favoriteFilter,
    refreshFilter,
    typeFilter
  })
  return api.get(`/api/ui/library/incremental-refresh/sources?${queryParams.toString()}`)
}

/** 获取增量追更定时任务状态 */
export const getIncrementalRefreshTaskStatus = () => api.get('/api/ui/library/incremental-refresh/task-status')

/** 批量开启/关闭追更 */
export const batchToggleIncrementalRefresh = (data) => api.post('/api/ui/library/incremental-refresh/batch-toggle', data)

/** 批量设置标记 */
export const batchSetFavorite = (data) => api.post('/api/ui/library/incremental-refresh/batch-favorite', data)

/** 批量取消标记 */
export const batchUnsetFavorite = (data) => api.post('/api/ui/library/incremental-refresh/batch-unfavorite', data)

// --- 数据库备份管理 ---

/** 获取备份列表 */
export const getBackupList = () => api.get('/api/ui/backup/list')

/** 创建备份 */
export const createBackup = () => api.post('/api/ui/backup/create')

/** 下载备份 */
export const downloadBackup = (filename) => `/api/ui/backup/download/${filename}`

/** 删除备份 */
export const deleteBackup = (filename) => api.delete(`/api/ui/backup/delete/${filename}`)

/** 批量删除备份 */
export const deleteBackupBatch = (filenames) => {
  const params = new URLSearchParams()
  filenames.forEach(f => params.append('filenames', f))
  return api.delete(`/api/ui/backup/delete-batch?${params.toString()}`)
}

/** 还原备份 */
export const restoreBackup = (data) => api.post('/api/ui/backup/restore', data)

/** 获取备份定时任务状态 */
export const getBackupJobStatus = () => api.get('/api/ui/backup/job-status')

/** 获取备份配置 */
export const getBackupConfig = () => api.get('/api/ui/backup/config')

/** 上传备份文件 */
export const uploadBackup = (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/api/ui/backup/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

/** ---------------------------------------------------弹幕编辑相关------------------------------------------------ */
/** 获取弹幕编辑详情（统计、分布、预览） */
export const getDanmakuEditDetail = (episodeId) => api.get(`/api/ui/danmaku/detail/${episodeId}`)

/** 分页获取弹幕列表（用于编辑预览） */
export const getDanmakuEditComments = (episodeId, params = {}) => {
  const queryParams = new URLSearchParams()
  if (params.page) queryParams.append('page', params.page)
  if (params.pageSize) queryParams.append('pageSize', params.pageSize)
  if (params.startTime !== undefined) queryParams.append('startTime', params.startTime)
  if (params.endTime !== undefined) queryParams.append('endTime', params.endTime)
  const queryString = queryParams.toString()
  return api.get(`/api/ui/danmaku/comments/${episodeId}${queryString ? `?${queryString}` : ''}`)
}

/** 应用时间偏移 */
export const applyDanmakuOffset = (data) => api.post('/api/ui/danmaku/offset', JSON.stringify(data))

/** 分集拆分 */
export const splitEpisodeDanmaku = (data) => api.post('/api/ui/danmaku/split', JSON.stringify(data))

/** 分集合并 */
export const mergeEpisodesDanmaku = (data) => api.post('/api/ui/danmaku/merge', JSON.stringify(data))

// ==================== 拆分数据源 ====================

/** 获取数据源的分集列表（用于拆分选择） */
export const getSourceEpisodesForSplit = (sourceId) =>
  api.get(`/api/ui/library/source/${sourceId}/episodes-for-split`)

/** 拆分数据源 */
export const splitSource = (animeId, data) =>
  api.post(`/api/ui/library/anime/${animeId}/split-source`, JSON.stringify(data))


// ========== 通知渠道 ==========

/** 获取可用渠道类型 */
export const getNotificationChannelTypes = () => api.get('/api/ui/notification/channel-types')

/** 获取指定渠道类型的配置 Schema */
export const getNotificationChannelSchema = (channelType) => api.get(`/api/ui/notification/schema/${channelType}`)

/** 获取所有通知渠道 */
export const getNotificationChannels = () => api.get('/api/ui/notification/channels')

/** 创建通知渠道 */
export const createNotificationChannel = (data) => api.post('/api/ui/notification/channels', data)

/** 更新通知渠道 */
export const updateNotificationChannel = (channelId, data) => api.put(`/api/ui/notification/channels/${channelId}`, data)

/** 删除通知渠道 */
export const deleteNotificationChannel = (channelId) => api.delete(`/api/ui/notification/channels/${channelId}`)

/** 测试通知渠道连接 */
export const testNotificationChannel = (channelId) => api.post(`/api/ui/notification/channels/${channelId}/test`)

// ========== 海报搜索 ==========

/** 查找作品的本地海报路径 */
export const getLocalImage = (params) => api.get('/api/ui/poster/local-image', params)

/** 下载网络海报到本地缓存 */
export const downloadPosterToLocal = (data) => api.post('/api/ui/poster/download-to-local', data)

/** 从 Fanart.tv 搜索海报 */
export const searchFanartPosters = (params) => api.get('/api/ui/poster/fanart', params)

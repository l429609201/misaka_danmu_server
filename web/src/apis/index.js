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
      oldPassword: data.oldPassword,
      newPassword: data.newPassword,
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
    `/api/v1/${data.apiToken}/match`,
    JSON.stringify({ fileName: data.fileName })
  )

/** 清除搜索缓存 */
export const clearSearchCache = () => api.post('/api/ui/cache/clear')

/** 搜索结果 */
export const getSearchResult = (data, onProgress) => {
  return api.get(
    '/api/ui/search/provider',
    {
      keyword: data.keyword,
    },
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
export const deleteTask = data => api.delete(`/api/ui/tasks/${data.taskId}`)
/** 中止任务 */
export const stopTask = data => api.post(`/api/ui/tasks/${data.taskId}/abort`)
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

/** 弹幕输出控制 单源输出总数 */
export const getDanmuOutputTotal = () =>
  api.get('/api/ui/config/danmaku_output_limit_per_source')

export const setDanmuOutputTotal = data =>
  api.put('/api/ui/config/danmaku_output_limit_per_source', data)
/** 弹幕输出控制 启用弹幕聚合 */
export const getDanmuOutputAggregation = () =>
  api.get('/api/ui/config/danmaku_aggregation_enabled')
/** 弹幕输出控制 启用弹幕聚合 */
export const setDanmuOutputAggregation = data =>
  api.put('/api/ui/config/danmaku_aggregation_enabled', data)

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

/** ----------------------------------------------弹幕库----------------------------------------------  */
/** 弹幕库列表 */
export const getAnimeLibrary = () => api.get('/api/ui/library')
/** 删除单个资源 */
export const deleteAnime = data =>
  api.delete(`/api/ui/library/anime/${data.animeId}`)
/** 获取影视信息 */
export const getAnimeDetail = data =>
  api.get(`/api/ui/library/anime/${data.animeId}/details`)

/** 保存影视信息 */
export const setAnimeDetail = data =>
  api.put(`/api/ui/library/anime/${data.animeId}`, data)

/** 获取影视的资源 */
export const getAnimeSource = data =>
  api.get(`/api/ui/library/anime/${data.animeId}/sources`)

/** 关联数据源 */
export const setAnimeSource = data =>
  api.post(`/api/ui/library/anime/${data.sourceAnimeId}/reassociate`, {
    targetAnimeId: data.targetAnimeId,
  })

/** 批量删除数据源 */
export const deleteAnimeSource = data =>
  api.post('/api/ui/library/sources/delete-bulk', data)

/** 删除单个数据源 */
export const deleteAnimeSourceSingle = data =>
  api.delete(`/api/ui/library/source/${data.sourceId}`)

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
export const getEpisodes = data =>
  api.get(`/api/ui/library/source/${data.sourceId}/episodes`)

/** 编辑分集信息 */
export const editEpisode = data =>
  api.put(`/api/ui/library/episode/${data.episodeId}`, data)
/** 手动导入集 */
export const manualImportEpisode = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/manual-import`, data)

/** 批量删除集 */
export const deleteAnimeEpisode = data =>
  api.post('/api/ui/library/episodes/delete-bulk', data)

/** 刷新集弹幕 */
export const refreshEpisodeDanmaku = data =>
  api.post(`/api/ui/library/episode/${data.id}/refresh`)

/** 删除集 */
export const deleteAnimeEpisodeSingle = data =>
  api.delete(`/api/ui/library/episode/${data.id}`)

/** 重整集数 */
export const resetEpisode = data =>
  api.post(`/api/ui/library/source/${data.sourceId}/reorder-episodes`)

/** 获取弹幕详情 */
export const getDanmakuDetail = data => api.get(`/api/ui/comment/${data.id}`)

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

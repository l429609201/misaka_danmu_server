/**
 * 全功能搜索 - 功能索引（单一数据源）
 *
 * 每条功能项结构：
 *   - id:        唯一标识
 *   - titleKey:  功能名 i18n key（三语自动）
 *   - descKey:   简介 i18n key（可选，复用现有 Tip/Desc 文案）
 *   - keywords:  搜索别名（中英混合，覆盖口语化叫法）
 *   - path:      目标页面路由
 *   - tabKey:    页面内 Tab 的 key（拼到 ?key=xxx）
 *   - anchor:    页面内 DOM 锚点 id（可选，点击后滚动高亮定位）
 *   - icon:      iconfont 图标名（复用导航图标）
 *
 * 说明：
 *   - 未配置 anchor 的条目，点击仅跳转到对应 Tab（降级，不报错）。
 *   - path + tabKey 与 Header.jsx 的 navItems、各页面 index.jsx 保持一致。
 */

export const FEATURES = [
  // ============ 首页 ============
  {
    id: 'home-search',
    titleKey: 'features.homeSearch.title',
    descKey: 'features.homeSearch.desc',
    keywords: ['搜索', '搜番', '搜剧', 'search', 'anime', '导入', 'import'],
    path: '/', tabKey: '', icon: 'home',
  },

  // ============ 弹幕库 ============
  {
    id: 'library-list',
    titleKey: 'features.libraryList.title',
    descKey: 'features.libraryList.desc',
    keywords: ['弹幕库', '媒体库', '作品', 'library', '收录'],
    path: '/library', tabKey: 'library', icon: 'kufangguanli',
  },
  {
    id: 'library-batch',
    titleKey: 'features.libraryBatch.title',
    descKey: 'features.libraryBatch.desc',
    keywords: ['批量管理', '批量', '追更', '完结', 'batch'],
    path: '/library/batch-manage', tabKey: 'batch', icon: 'piliangguanli',
  },
  {
    id: 'subscriptions',
    titleKey: 'features.subscriptions.title',
    descKey: 'features.subscriptions.desc',
    keywords: ['订阅', '日历', '追番', 'subscription', 'calendar', 'trakt', 'bangumi'],
    path: '/library/subscriptions', tabKey: '', icon: 'kufangguanli',
  },

  // ============ 任务管理器 ============
  {
    id: 'task-running',
    titleKey: 'features.taskRunning.title',
    descKey: 'features.taskRunning.desc',
    keywords: ['进行中的任务', '任务', '进度', 'task', 'running', '导入任务'],
    path: '/task', tabKey: 'task', icon: 'tongji-jinhangzhongderenwushuliang',
  },
  {
    id: 'task-webhook',
    titleKey: 'features.taskWebhook.title',
    descKey: 'features.taskWebhook.desc',
    keywords: ['webhook任务', 'webhook', '清空任务', '待处理'],
    path: '/task', tabKey: 'webhook', icon: 'Webhookrenwu',
  },
  {
    id: 'task-schedule',
    titleKey: 'features.taskSchedule.title',
    descKey: 'features.taskSchedule.desc',
    keywords: ['定时任务', 'cron', 'schedule', '计划任务', '自动'],
    path: '/task', tabKey: 'schedule', icon: 'dingshirenwu',
  },
  {
    id: 'task-ratelimit',
    titleKey: 'features.taskRatelimit.title',
    descKey: 'features.taskRatelimit.desc',
    keywords: ['流控', '限速', '限流', 'ratelimit', '速率'],
    path: '/task', tabKey: 'ratelimit', icon: 'liukong',
  },
  {
    id: 'task-profile',
    titleKey: 'features.taskProfile.title',
    descKey: 'features.taskProfile.desc',
    keywords: ['任务画像', '统计', 'profile', '分析'],
    path: '/task', tabKey: 'profile', icon: 'tongji-jinhangzhongderenwushuliang',
  },

  // ============ 弹幕（Token 页子卡片）============
  {
    id: 'bullet-token-list',
    titleKey: 'features.bulletTokenList.title',
    descKey: 'features.bulletTokenList.desc',
    keywords: ['token列表', '令牌列表', '新建token', '生成token', 'api令牌', '调用次数'],
    path: '/bullet', tabKey: 'token', anchor: 'feat-bullet-token-list', icon: 'tokenguanli',
  },
  {
    id: 'bullet-custom-domain',
    titleKey: 'features.bulletCustomDomain.title',
    descKey: 'features.bulletCustomDomain.desc',
    keywords: ['自定义域名', '外链域名', '公网域名', 'custom domain', 'https域名', '图片外链'],
    path: '/bullet', tabKey: 'token', anchor: 'feat-custom-domain', icon: 'tokenguanli',
  },
  {
    id: 'bullet-ua-filter',
    titleKey: 'features.bulletUaFilter.title',
    descKey: 'features.bulletUaFilter.desc',
    keywords: ['ua过滤', 'user agent', 'ua规则', 'user-agent', 'ua白名单', '客户端过滤'],
    path: '/bullet', tabKey: 'token', anchor: 'feat-ua-filter', icon: 'tokenguanli',
  },

  // ============ 弹幕 ============
  {
    id: 'bullet-token',
    titleKey: 'features.bulletToken.title',
    descKey: 'features.bulletToken.desc',
    keywords: ['token', '令牌', '密钥', 'token管理', 'dandanplay'],
    path: '/bullet', tabKey: 'token', icon: 'tokenguanli',
  },
  {
    id: 'bullet-output',
    titleKey: 'features.bulletOutput.title',
    descKey: 'features.bulletOutput.desc',
    keywords: ['弹幕输出', '输出配置', '输出上限', '合并输出', '简繁转换', '点赞', '随机颜色', '黑名单', '弹幕类型', '顶部', '底部', '滚动', 'output', 'convert'],
    path: '/bullet', tabKey: 'output', anchor: 'feat-bullet-output', icon: 'shuchupeizhi',
  },
  {
    id: 'bullet-color',
    titleKey: 'features.bulletColor.title',
    descKey: 'features.bulletColor.desc',
    keywords: ['弹幕颜色', '随机颜色', '颜色规则', '彩色弹幕', 'color', '颜色设置'],
    path: '/bullet', tabKey: 'output', anchor: 'feat-bullet-color', icon: 'shuchupeizhi',
  },
  {
    id: 'bullet-blacklist',
    titleKey: 'features.bulletBlacklist.title',
    descKey: 'features.bulletBlacklist.desc',
    keywords: ['弹幕黑名单', '屏蔽词', '过滤词', '关键词过滤', 'blacklist', '屏蔽'],
    path: '/bullet', tabKey: 'output', anchor: 'feat-bullet-blacklist', icon: 'shuchupeizhi',
  },
  {
    id: 'bullet-storage',
    titleKey: 'features.bulletStorage.title',
    descKey: 'features.bulletStorage.desc',
    keywords: ['弹幕存储', '存储配置', '文件路径', '保存路径', 'storage', 'path'],
    path: '/bullet', tabKey: 'storage', icon: 'cunchupeizhi',
  },
  {
    id: 'bullet-fallback',
    titleKey: 'features.bulletFallback.title',
    descKey: 'features.bulletFallback.desc',
    keywords: ['匹配后备', '搜索后备', 'fallback', '兜底', '全网搜索'],
    path: '/bullet', tabKey: 'fallback', icon: 'sanfangyunpeizhi',
  },
  {
    id: 'bullet-data-check',
    titleKey: 'features.bulletDataCheck.title',
    descKey: 'features.bulletDataCheck.desc',
    keywords: ['数据校验', '数据检查', 'data check', '一致性'],
    path: '/bullet', tabKey: 'data-check', icon: 'renlianshibie_o',
  },

  // ============ 媒体获取 ============
  {
    id: 'media-library-scan',
    titleKey: 'features.mediaLibraryScan.title',
    descKey: 'features.mediaLibraryScan.desc',
    keywords: ['媒体库读取', 'emby', 'jellyfin', 'plex', '扫描', 'scan', '媒体服务器'],
    path: '/media-fetch', tabKey: 'library-scan', icon: 'meitiduqu',
  },
  {
    id: 'media-local-scan',
    titleKey: 'features.mediaLocalScan.title',
    descKey: 'features.mediaLocalScan.desc',
    keywords: ['本地扫描', '本地文件', 'local scan', '本地目录'],
    path: '/media-fetch', tabKey: 'local-scan', icon: 'bendiduqu',
  },

  // ============ 搜索源 ============
  {
    id: 'source-scrapers',
    titleKey: 'features.sourceScrapers.title',
    descKey: 'features.sourceScrapers.desc',
    keywords: ['弹幕搜索源', '弹幕源', 'scraper', 'bilibili', 'tencent', 'iqiyi', '源管理', 'cookie'],
    path: '/source', tabKey: 'scrapers', icon: 'accurate-search',
  },
  {
    id: 'source-metadata',
    titleKey: 'features.sourceMetadata.title',
    descKey: 'features.sourceMetadata.desc',
    keywords: ['元信息源', '元数据源', 'metadata', 'tmdb', 'tvdb', 'bangumi', 'imdb', 'douban'],
    path: '/source', tabKey: 'metadata', icon: 'accurate-search-full',
  },
  {
    id: 'source-global-filter',
    titleKey: 'features.sourceGlobalFilter.title',
    descKey: 'features.sourceGlobalFilter.desc',
    keywords: ['过滤配置', '全局过滤', '分集过滤', '分集标题过滤', '单剧过滤', '兜底过滤', 'filter', '黑名单'],
    path: '/source', tabKey: 'global-filter', anchor: 'feat-global-filter', icon: 'guolvshezhi',
  },
  {
    id: 'source-episode-title-filter',
    titleKey: 'features.sourceEpisodeTitleFilter.title',
    descKey: 'features.sourceEpisodeTitleFilter.desc',
    keywords: ['分集标题过滤', '集数标题', '正则过滤', '标题过滤', 'episode title filter', '集标'],
    path: '/source', tabKey: 'global-filter', anchor: 'feat-episode-title-filter', icon: 'guolvshezhi',
  },
  {
    id: 'source-single-filter',
    titleKey: 'features.sourceSingleFilter.title',
    descKey: 'features.sourceSingleFilter.desc',
    keywords: ['单剧过滤', '按作品过滤', '单剧规则', 'per-title filter', '剧名过滤', 'single filter'],
    path: '/source', tabKey: 'global-filter', anchor: 'feat-single-filter', icon: 'guolvshezhi',
  },

  // ============ 外部控制 ============
  {
    id: 'control-apikey',
    titleKey: 'features.controlApikey.title',
    descKey: 'features.controlApikey.desc',
    keywords: ['api密钥', 'apikey', '密钥', 'token', '外部控制'],
    path: '/control', tabKey: 'apikey', icon: 'API',
  },
  {
    id: 'control-settings',
    titleKey: 'features.controlSettings.title',
    descKey: 'features.controlSettings.desc',
    keywords: ['外部控制设置', '参数配置', 'settings'],
    path: '/control', tabKey: 'settings', icon: 'canshupeizhi',
  },
  {
    id: 'control-apilogs',
    titleKey: 'features.controlApilogs.title',
    descKey: 'features.controlApilogs.desc',
    keywords: ['api日志', 'api访问日志', 'apilogs', '访问记录'],
    path: '/control', tabKey: 'apilogs', icon: 'APIrizhi',
  },
  {
    id: 'control-mcp',
    titleKey: 'features.controlMcp.title',
    descKey: 'features.controlMcp.desc',
    keywords: ['mcp', 'model context protocol', 'ai工具'],
    path: '/control', tabKey: 'mcp', icon: 'MCP',
  },
  {
    id: 'control-apidoc',
    titleKey: 'features.controlApidoc.title',
    descKey: 'features.controlApidoc.desc',
    keywords: ['api文档', 'apidoc', 'swagger', '接口文档'],
    path: '/control', tabKey: 'apidoc', icon: 'kuaijierukou_apiwendang',
  },

  // ============ 设置 ============
  {
    id: 'setting-parameters',
    titleKey: 'features.settingParameters.title',
    descKey: 'features.settingParameters.desc',
    keywords: ['参数配置', '通用设置', 'parameters', '系统参数'],
    path: '/setting', tabKey: 'parameters', icon: 'canshupeizhi',
  },
  {
    id: 'setting-proxy',
    titleKey: 'features.settingProxy.title',
    descKey: 'features.settingProxy.desc',
    keywords: ['代理', 'proxy', '代理配置', 'http代理', 'socks', '加速'],
    path: '/setting', tabKey: 'proxy', icon: 'dailipeizhi',
  },
  {
    id: 'proxy-speedtest',
    titleKey: 'features.proxySpeedtest.title',
    descKey: 'features.proxySpeedtest.desc',
    keywords: ['测速', '代理测速', '延迟', '连通性', 'speedtest', 'ping', '延时测试'],
    path: '/setting', tabKey: 'proxy', anchor: 'feat-proxy-speedtest', icon: 'dailipeizhi',
  },
  {
    id: 'setting-webhook',
    titleKey: 'features.settingWebhook.title',
    descKey: 'features.settingWebhook.desc',
    keywords: ['webhook配置', 'webhook', '延时导入', '自动化导入'],
    path: '/setting', tabKey: 'webhook', icon: 'webhookpeizhi',
  },
  {
    id: 'setting-notification',
    titleKey: 'features.settingNotification.title',
    descKey: 'features.settingNotification.desc',
    keywords: ['通知', 'notification', 'telegram', '消息推送', 'bark'],
    path: '/setting', tabKey: 'notification', icon: 'jiaohu',
  },
  {
    id: 'setting-recognition',
    titleKey: 'features.settingRecognition.title',
    descKey: 'features.settingRecognition.desc',
    keywords: ['识别词', '标题识别', 'recognition', '集数偏移', '自定义识别'],
    path: '/setting', tabKey: 'recognition', icon: 'renlianshibie_o',
  },
  {
    id: 'recognition-test',
    titleKey: 'features.recognitionTest.title',
    descKey: 'features.recognitionTest.desc',
    keywords: ['识别词测试', '测试识别', '识别调试', 'recognition test', '识别结果'],
    path: '/setting', tabKey: 'recognition', anchor: 'feat-recognition-test', icon: 'renlianshibie_o',
  },
  {
    id: 'setting-automatch',
    titleKey: 'features.settingAutomatch.title',
    descKey: 'features.settingAutomatch.desc',
    keywords: ['自动匹配', 'ai匹配', 'automatch', '季度映射', '剧集组', 'ai'],
    path: '/setting', tabKey: 'automatch', icon: 'ai',
  },
  {
    id: 'automatch-connection',
    titleKey: 'features.automatchConnection.title',
    descKey: 'features.automatchConnection.desc',
    keywords: ['ai连接', 'ai key', 'openai', 'deepseek', 'gemini', 'claude', 'ai模型', 'api key', 'base url'],
    path: '/setting', tabKey: 'automatch', anchor: 'feat-ai-connection', icon: 'ai',
  },
  {
    id: 'automatch-season-mapping',
    titleKey: 'features.automatchSeasonMapping.title',
    descKey: 'features.automatchSeasonMapping.desc',
    keywords: ['季度映射', 'tmdb季度', '剧集组', 'season mapping', '映射配置', '季度配置'],
    path: '/setting', tabKey: 'automatch', anchor: 'feat-ai-season-mapping', icon: 'ai',
  },
  {
    id: 'automatch-recognition',
    titleKey: 'features.automatchRecognition.title',
    descKey: 'features.automatchRecognition.desc',
    keywords: ['ai识别', '识别增强', '别名纠错', '别名扩展', '名称转换', 'ai recognition', '简繁'],
    path: '/setting', tabKey: 'automatch', anchor: 'feat-ai-recognition', icon: 'ai',
  },
  {
    id: 'setting-security',
    titleKey: 'features.settingSecurity.title',
    descKey: 'features.settingSecurity.desc',
    keywords: ['安全', 'security', '白名单', 'ua规则', '密码', 'mfa', '双因素'],
    path: '/setting', tabKey: 'security', icon: 'anquan',
  },
  {
    id: 'security-totp',
    titleKey: 'features.securityTotp.title',
    descKey: 'features.securityTotp.desc',
    keywords: ['totp', '两步验证', '双因素', '2fa', 'mfa', 'otp', '验证器', '动态码'],
    path: '/setting', tabKey: 'security', anchor: 'feat-security-totp', icon: 'anquan',
  },
  {
    id: 'security-passkey',
    titleKey: 'features.securityPasskey.title',
    descKey: 'features.securityPasskey.desc',
    keywords: ['passkey', '免密登录', 'webauthn', '指纹', '人脸', 'fido', '无密码登录'],
    path: '/setting', tabKey: 'security', anchor: 'feat-security-passkey', icon: 'anquan',
  },
]

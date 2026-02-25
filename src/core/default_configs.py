"""
默认配置定义
每个配置项格式: (默认值, 描述)

注意: 某些配置需要在运行时动态生成(如jwtSecretKey),这些配置在main.py中单独处理
"""

def get_default_configs(settings=None, ai_prompts=None):
    """
    获取默认配置字典

    Args:
        settings: FastAPI settings对象,用于获取JWT过期时间等配置
        ai_prompts: AI提示词字典,包含DEFAULT_AI_MATCH_PROMPT等

    Returns:
        dict: 默认配置字典
    """
    configs = {
        # 缓存 TTL
        'searchTtlSeconds': (10800, '搜索结果的缓存时间（秒），最低3小时。'),
        'episodesTtlSeconds': (10800, '分集列表的缓存时间（秒），最低3小时。'),
        'baseInfoTtlSeconds': (10800, '基础媒体信息（如爱奇艺）的缓存时间（秒），最低3小时。'),
        'metadataSearchTtlSeconds': (10800, '元数据（如TMDB, Bangumi）搜索结果的缓存时间（秒），最低3小时。'),

        # API 和 Webhook
        'customApiDomain': ('', '用于拼接弹幕API地址的自定义域名。'),
        'webhookApiKey': ('', '用于Webhook调用的安全密钥。'),
        'trustedProxies': ('', '受信任的反向代理IP列表，用逗号分隔。当请求来自这些IP时，将从 X-Forwarded-For 或 X-Real-IP 头中解析真实客户端IP。'),
        'webhookEnabled': ('true', '是否全局启用 Webhook 功能。'),
        'webhookDelayedImportEnabled': ('false', '是否为 Webhook 触发的导入启用延时。'),
        'webhookDelayedImportHours': ('24', 'Webhook 延时导入的小时数。'),
        'webhookFilterMode': ('blacklist', 'Webhook 标题过滤模式 (blacklist/whitelist)。'),
        'webhookFilterRegex': ('', '用于过滤 Webhook 标题的正则表达式。'),
        'webhookLogRawRequest': ('false', '是否记录 Webhook 的原始请求体。'),
        'externalApiKey': ('', '用于外部API调用的安全密钥。'),
        'externalApiDuplicateTaskThresholdHours': (3, '（外部API）重复任务提交阈值（小时）。在此时长内，不允许为同一媒体提交重复的自动导入任务。0为禁用。'),
        'webhookCustomDomain': ('', '用于拼接Webhook URL的自定义域名。'),

        # 代理
        'proxyMode': ('none', '代理模式: none(不使用代理), http_socks(HTTP/SOCKS代理), accelerate(加速代理)'),
        'proxyUrl': ('', '全局HTTP/HTTPS/SOCKS5代理地址。'),
        'proxyEnabled': ('false', '是否全局启用代理。'),  # 保留兼容性
        'proxySslVerify': ('true', '使用HTTPS代理时是否验证SSL证书。设为false可解决自签名证书问题。'),
        'accelerateProxyUrl': ('', '加速代理地址，如 https://your-proxy.vercel.app'),

        # 元数据源
        'tmdbApiKey': ('', '用于访问 The Movie Database API 的密钥。'),
        'tmdbApiBaseUrl': ('https://api.themoviedb.org', 'TMDB API 的基础域名。'),
        'tmdbImageBaseUrl': ('https://image.tmdb.org', 'TMDB 图片服务的基础 URL。'),
        'tvdbApiKey': ('', '用于访问 TheTVDB API 的密钥。'),
        'bangumiClientId': ('', '用于Bangumi OAuth的App ID。'),
        'bangumiClientSecret': ('', '用于Bangumi OAuth的App Secret。'),
        'doubanCookie': ('', '用于访问豆瓣API的Cookie。'),
        'imdbUseApi': ('true', 'IMDb是否使用第三方API (api.imdbapi.dev) 而不是官方网站HTML解析。'),
        'imdbEnableFallback': ('true', 'IMDb是否启用兜底机制。当主方式失败时,自动尝试另一种方式。'),

        # 弹幕源
        'danmakuOutputLimitPerSource': ('-1', '弹幕输出上限。-1为无限制。超出限制时按时间段均匀采样。'),
        'danmakuMergeOutputEnabled': ('false', '是否启用合并输出。启用后，将所有源的弹幕合并后再进行均衡采样输出。'),
        'danmakuChConvert': ('0', '弹幕简繁转换。0-不转换，1-转换为简体，2-转换为繁体。'),
        'danmakuChConvertPriority': ('player', '简繁转换优先级。player-播放器优先（播放器明确指定时覆盖服务端配置），server-服务端优先（始终使用服务端配置）。'),
        'danmakuRandomColorMode': ('off', '弹幕颜色转换模式：off(不使用)、white_to_random(白色弹幕随机染色)、all_random(全部随机染色)、all_white(全部变白色)。'),
        'danmakuRandomColorPalette': (
            '16777215,16777215,16777215,16777215,16777215,16777215,16777215,16777215,16744319,16752762,16774799,9498256,8388564,8900346,14204888,16758465',
            '弹幕随机颜色色板，逗号分隔的十进制颜色值，默认加大白色概率。'
        ),
        'danmakuBlacklistEnabled': ('false', '是否启用弹幕黑名单过滤。启用后，将过滤掉匹配黑名单正则表达式的弹幕。'),
        'danmakuBlacklistPatterns': ('2333|666|哈哈哈|牛逼|前排|抢前排|第[0-9一二三四五六七八九十百千]+排|空降|到此一游|打卡|报道|报到|学[jJvVaA]+|后台播放|生日快乐|现在.+点|几点了|^\d+小时|^\d+分钟|^\d+秒|^\d{4}年|^\d+月\d+日|纯享版|三连|一键三连|恰饭|币没了|热乎的|^\d+分钟前|白嫖|奥利给|寄了|蚌埠住了|蚌住|绷不住|笑死|草|泪目|哭了|泪奔|我哭了|弹幕护体|高考加油|上岸|保佑|还愿|活该|大快人心|报应|吓得我|一个巴掌拍不响|苍蝇不叮无缝的蛋|可怜之人必有可恨之处|^从.{0,8}来的|广东人|四川人|东北人|山东人|河南人|江苏人|浙江人|上海人|北京人|我老婆|我老公|我儿子|我女儿|我妈|爸|弟|姐|szd|真香|真恶心|太丑了|太美了|抱走|承包|舔屏|鼻血|已存|壁纸|手机壁纸|桌面|高清|无码|开车|手动狗头|手动滑稽|doge|妙啊|寄寄+|111+|222+|333+|444+|555+|777+|888+|999+|000+|(.)\1{6,}|^.{0,9}\(|^[\u4e00-\u9fa5\w]{0,10} \)|^[^\u4e00-\u9fa5]{8,}\(|[·・]?(■|▂|▃|▄|▅|▆|▇|█){3,}[·・]?|^[\u4e00-\u9fa5]{5}[，,][\u4e00-\u9fa5]{7}[，,][\u4e00-\u9fa5]{5} \)|见.{0,6}滚|滚.{0,6}见|智障|弱智|脑残|垃圾|辣鸡|恶心|死全家|去死妈|死爹|去死|傻逼|傻B|SB|sb|S ?b|cnm|你妈|NMSL|nm+l|tmd|他妈|操|艹|曹|叉|尼玛|泥马|日你|日死|去死吧|傻吊|阳痿|早泄|卖鲍|约炮|赌博|菠菜|开盘|杀猪盘|三狗|pg|AG|DG|OB|MG|BBIN|PT|EA|JDB|已三连|已投币|已充电|已关注|已收藏|已点赞|已打赏|已上舰|已续舰|提督|总督|舰长|大会员|年度大会员|小心心|辣条|打call|冲鸭|yyds|YYDS|绝绝子|神作|神番|封神|名场面|修罗场|真香警告|社死|翻车|贴贴|抱抱|亲亲|我爱你|娶我|嫁我|已婚|已离婚|已出轨|已出柜|已弯|已直|已黑化|已净化|已成佛|已飞升|已圆寂|已投胎|已退网|已退圈|已取关|已拉黑|已举报|已切割|已脱粉|已回踩|已反黑|已洗白|世界尽头|冷酷异变|生崽|生猴子|生一窝|小奶猫|小奶狗|小奶狐|小奶狼|小奶龙|舔狗|舔狼|上头了|太上头|眼睛怀孕|耳朵怀孕|妊娠纹|打桩机|大力出奇迹|黑化强三倍|洗白弱三倍|寄中寄|寄里寄气|玉玉了|已紫砂|我裂开了|xswl|awsl|AWSL|好甜|好刀|锁死|嗑疯了|嗑到脑溢血|嗑拉了|嗑吐了|cp粉狂喜|大型发糖|大型撒狗粮|大型虐狗|大型修罗场|大型翻车现场|大型社死现场|大型真香现场|大型纪录片|太顶了|太硬了|太粗了|太长了|太快了|太刺激了|爽飞了|高潮了|喷了|射了|已升天|手动@所有人|我来晚了|我先润了|我先溜了|我先寄了|88|886|拜拜|202[5-9]|2030|新年快乐|跨年快乐|龙年大吉|恭喜发财|暴富|脱单', '弹幕黑名单正则表达式，使用 | 分隔多个规则。匹配弹幕内容(m字段)，不区分大小写。'),
        'scraperVerificationEnabled': ('false', '是否启用搜索源签名验证。'),
        'bilibiliCookie': ('', '用于访问B站API的Cookie，特别是buvid3。'),
        'gamerCookie': ('', '用于访问巴哈姆特动画疯的Cookie。'),
        'matchFallbackEnabled': ('false', '是否为匹配接口启用后备机制（自动搜索导入）。'),
        'matchFallbackBlacklist': ('', '匹配后备黑名单，使用正则表达式过滤文件名，匹配的文件不会触发后备机制。'),
        'searchFallbackEnabled': ('false', '是否为搜索接口启用后备搜索功能（全网搜索）。'),

        # 弹幕文件路径配置
        'customDanmakuPathEnabled': ('false', '是否启用自定义弹幕文件保存路径。'),
        'movieDanmakuDirectoryPath': ('/app/config/danmaku/movies', '电影/剧场版弹幕文件存储的根目录。'),
        'movieDanmakuFilenameTemplate': ('${title}/${episodeId}', '电影/剧场版弹幕文件命名模板。支持变量：${title}, ${titleBase}(标准化标题，去除季度信息), ${season}, ${episode}, ${year}, ${provider}, ${animeId}, ${episodeId}, ${sourceId}。支持子目录。.xml后缀会自动添加。'),
        'tvDanmakuDirectoryPath': ('/app/config/danmaku/tv', '电视节目弹幕文件存储的根目录。'),
        'tvDanmakuFilenameTemplate': ('${animeId}/${episodeId}', '电视节目弹幕文件命名模板。支持变量：${title}, ${titleBase}(标准化标题，去除季度信息), ${season}, ${episode}, ${year}, ${provider}, ${animeId}, ${episodeId}, ${sourceId}。支持子目录。.xml后缀会自动添加。'),

        'iqiyiUseProtobuf': ('false', '（爱奇艺）是否使用新的Protobuf弹幕接口（实验性）。'),
        'gamerUserAgent': ('', '用于访问巴哈姆特动画疯的User-Agent。'),

        # 搜索性能优化
        'searchMaxResultsPerSource': ('30', '每个搜索源最多返回的结果数量。设置较小的值可以提高搜索速度。'),

        # 全局过滤（搜索结果标题）
        'search_result_global_blacklist_cn': (r'特典|预告|广告|菜单|花絮|特辑|速看|资讯|彩蛋|直拍|直播回顾|片头|片尾|幕后|映像|番外篇|纪录片|访谈|番外|短片|加更|走心|解忧|纯享|解读|揭秘|赏析', '用于过滤搜索结果标题的全局中文黑名单(正则表达式)。'),
        'search_result_global_blacklist_eng': (r'NC|OP|ED|SP|OVA|OAD|CM|PV|MV|BDMenu|Menu|Bonus|Recap|Teaser|Trailer|Preview|CD|Disc|Scan|Sample|Logo|Info|EDPV|SongSpot|BDSpot', '用于过滤搜索结果标题的全局英文黑名单(正则表达式)。'),

        'mysqlBinlogRetentionDays': (3, '（仅MySQL）自动清理多少天前的二进制日志（binlog）。0为不清理。需要SUPER或BINLOG_ADMIN权限。'),

        # 顺延机制配置
        'webhookFallbackEnabled': ('false', '是否启用Webhook顺延机制。当选中的源没有有效分集时，自动尝试下一个源。'),
        'externalApiFallbackEnabled': ('false', '是否启用外部控制API/匹配后备/后备搜索顺延机制。当选中的源没有有效分集时，自动尝试下一个源。'),

        # 增量追更配置
        'incrementalRefreshMaxFailures': (10, '增量追更最大失败次数。超过此次数后自动禁用该源的追更功能。'),

        # 预下载配置
        'preDownloadNextEpisodeEnabled': ('false', '是否启用预下载下一集弹幕。当播放当前集时，自动下载下一集的弹幕。需要启用匹配后备或后备搜索。'),

        # 季度映射配置
        'homeSearchEnableTmdbSeasonMapping': ('false', '是否启用主页搜索 TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'fallbackSearchEnableTmdbSeasonMapping': ('false', '是否启用后备搜索 TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'webhookEnableTmdbSeasonMapping': ('false', '是否启用Webhook TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'matchFallbackEnableTmdbSeasonMapping': ('false', '是否启用匹配后备 TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'externalSearchEnableTmdbSeasonMapping': ('false', '是否启用外部控制-搜索媒体 TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'autoImportEnableTmdbSeasonMapping': ('false', '是否启用全自动导入 TMDB季度映射。启用后，系统会通过TMDB等元数据源获取季度名称，提高多季度剧集的匹配准确率。'),
        'seasonMappingMetadataSource': ('tmdb', 'TMDB季度映射使用的元数据源。可选值: tmdb, tvdb, imdb, douban, bangumi。'),
        'seasonMappingPrompt': ('', 'AI季度映射提示词。用于指导AI从元数据源搜索结果中选择最佳匹配。留空使用默认提示词。'),

        # 媒体服务器配置
        'mediaServerAutoImport': ('false', '是否自动导入新扫描到的媒体项'),
        'mediaServerSyncInterval': ('3600', '媒体服务器同步间隔(秒)'),

        # Docker 容器管理配置
        'containerName': ('misaka_danmu_server', '当前运行的 Docker 容器名称，用于重启和更新操作。'),
        'dockerImageName': ('l429609201/misaka_danmu_server:latest', 'Docker 镜像名称（含标签），用于一键更新功能。'),
    }

    # 添加需要settings的配置
    if settings:
        configs['jwtExpireMinutes'] = (settings.jwt.access_token_expire_minutes, 'JWT令牌的有效期（分钟）。-1 表示永不过期。')

    # 添加AI相关配置
    if ai_prompts:
        configs.update({
            'aiMatchEnabled': ('false', '是否启用AI智能匹配。启用后，在自动匹配场景(外部API、Webhook、匹配后备)中使用AI选择最佳搜索结果。'),
            'aiFallbackEnabled': ('true', '是否启用传统匹配兜底。当AI匹配失败时，自动降级到传统匹配算法。'),
            'aiProvider': ('deepseek', 'AI提供商: deepseek, siliconflow, openai, gemini'),
            'aiApiKey': ('', 'AI服务的API密钥'),
            'aiBaseUrl': ('', 'AI服务的Base URL (可选,用于自定义接口)'),
            'aiModel': ('deepseek-chat', 'AI模型名称,如: deepseek-chat, Qwen/Qwen2.5-7B-Instruct, gpt-4'),
            'aiPrompt': (ai_prompts.get('DEFAULT_AI_MATCH_PROMPT', ''), 'AI智能匹配提示词'),
            'aiRecognitionEnabled': ('false', '是否启用AI辅助识别。启用后，在TMDB自动刮削任务中使用AI识别标题和季度信息。'),
            'aiRecognitionPrompt': (ai_prompts.get('DEFAULT_AI_RECOGNITION_PROMPT', ''), 'AI辅助识别提示词'),
            'aiAliasCorrectionEnabled': ('false', '是否启用AI别名修正。启用后，在TMDB自动刮削任务中使用AI验证和修正别名。'),
            'aiAliasValidationPrompt': (ai_prompts.get('DEFAULT_AI_ALIAS_VALIDATION_PROMPT', ''), 'AI别名验证提示词'),
            'aiAliasExpansionEnabled': ('false', '是否启用AI别名扩展。启用后，当元数据源返回非中文标题时，使用AI生成可能的别名用于搜索。'),
            'aiAliasExpansionPrompt': (ai_prompts.get('DEFAULT_AI_ALIAS_EXPANSION_PROMPT', ''), 'AI别名扩展提示词'),
            'aiNameConversionEnabled': ('false', '是否启用AI名称转换（兜底）。启用后，当元数据源查询失败时使用AI进行名称转换。'),
            'aiNameConversionPrompt': (ai_prompts.get('DEFAULT_AI_NAME_CONVERSION_PROMPT', ''), 'AI名称转换提示词'),
            'aiLogRawResponse': ('false', '是否记录AI原始响应到日志文件'),
            'seasonMappingPrompt': (ai_prompts.get('DEFAULT_AI_SEASON_MAPPING_PROMPT', ''), 'AI季度映射提示词。用于指导AI从元数据源搜索结果中选择最佳匹配。'),
            'aiEpisodeGroupEnabled': ('false', '是否启用AI剧集组自动选择。启用后，当作品有TMDB ID但缺少剧集组时，使用AI自动选择最佳剧集组，实现等价集数映射。'),
            'aiEpisodeGroupPrompt': (ai_prompts.get('DEFAULT_AI_EPISODE_GROUP_SELECT_PROMPT', ''), 'AI剧集组选择提示词。用于指导AI从TMDB剧集组列表中选择最佳匹配。'),
            'aiCacheEnabled': ('true', '是否启用AI响应缓存。启用后，相同查询将直接返回缓存结果，降低API调用成本。'),
            'aiCacheTtl': ('3600', 'AI缓存过期时间(秒)。默认3600秒(1小时)。'),
            # 名称转换功能配置
            'nameConversionEnabled': ('false', '是否启用名称转换功能。启用后，搜索时自动将非中文名称转换为中文。'),
            'nameConversionT2SEnabled': ('false', '是否启用繁体自动转简体。启用后，搜索时自动将繁体中文标题转换为简体。'),
            'nameConversionSourcePriority': ('[{"key":"bangumi","enabled":true},{"key":"tmdb","enabled":true},{"key":"tvdb","enabled":true},{"key":"douban","enabled":true},{"key":"imdb","enabled":true}]', '名称转换元数据源优先级配置（JSON格式）'),
        })

    return configs

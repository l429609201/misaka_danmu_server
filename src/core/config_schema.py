"""
参数配置 Schema 定义

配置项类型:
- string: 普通文本输入
- password: 密码输入（带遮罩）
- number: 数字输入
- boolean: 开关
- textarea: 多行文本
- select: 下拉选择

特殊字段:
- verifyEndpoint: 验证接口路径（如 Token 验证）
- suffix: 输入框后缀（如 "秒"）
- min/max: 数字输入的范围限制
- options: select 类型的选项列表
- placeholder: 输入框占位符
- description: 配置项描述
- rows: textarea 的行数
"""

CONFIG_SCHEMA = [
    {
        "key": "security",
        "label": "安全设置",
        "items": [
            {
                "key": "github_token",
                "label": "GitHub Token",
                "type": "password",
                "description": "用于请求 GitHub API，避免速率限制。无需任何权限，只需创建一个 Token 即可。",
                "placeholder": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "verifyEndpoint": "/api/ui/config/github-token/verify",
            },
            {
                "key": "ipWhitelist",
                "label": "访问IP白名单",
                "type": "textarea",
                "rows": 3,
                "description": "⚠️ 安全警告：白名单内的IP访问管理界面时无需登录！请谨慎配置。多个IP或CIDR网段请用英文逗号(,)分隔。留空表示禁用此功能。",
                "placeholder": "例如: 192.168.1.100, 10.0.0.0/8",
            },
            {
                "key": "trustedProxies",
                "label": "受信任的反向代理",
                "type": "textarea",
                "rows": 3,
                "description": "当请求来自这些IP时，将从 X-Forwarded-For 或 X-Real-IP 头中解析真实客户端IP。多个IP或CIDR网段请用英文逗号(,)分隔。",
                "placeholder": "例如: 127.0.0.1, 192.168.1.0/24, 10.0.0.1/32",
            },
            {
                "key": "jwtExpireMinutes",
                "label": "登录令牌有效期",
                "type": "number",
                "suffix": "分钟",
                "min": -1,
                "description": "JWT 令牌的有效期（分钟）。-1 表示永不过期。修改后需要重新登录生效。",
                "placeholder": "4320",
            },
        ],
    },
    {
        "key": "cache",
        "label": "缓存设置",
        "items": [
            {
                "key": "searchTtlSeconds",
                "label": "搜索缓存时间",
                "type": "number",
                "suffix": "秒",
                "min": 10800,
                "description": "搜索结果的缓存时间，最低3小时（10800秒）",
                "placeholder": "10800",
            },
            {
                "key": "episodesTtlSeconds",
                "label": "分集缓存时间",
                "type": "number",
                "suffix": "秒",
                "min": 10800,
                "description": "分集列表的缓存时间，最低3小时（10800秒）",
                "placeholder": "10800",
            },
            {
                "key": "baseInfoTtlSeconds",
                "label": "基础信息缓存时间",
                "type": "number",
                "suffix": "秒",
                "min": 10800,
                "description": "基础媒体信息的缓存时间，最低3小时（10800秒）",
                "placeholder": "10800",
            },
            {
                "key": "metadataSearchTtlSeconds",
                "label": "元数据搜索缓存时间",
                "type": "number",
                "suffix": "秒",
                "min": 10800,
                "description": "元数据（如TMDB, Bangumi）搜索结果的缓存时间，最低3小时（10800秒）",
                "placeholder": "10800",
            },
        ],
    },
    {
        "key": "performance",
        "label": "性能设置",
        "items": [
            {
                "key": "searchMaxResultsPerSource",
                "label": "每源最大搜索结果数",
                "type": "number",
                "min": 1,
                "max": 100,
                "description": "每个搜索源最多返回的结果数量。设置较小的值可以提高搜索速度。",
                "placeholder": "30",
            },
        ],
    },
    {
        "key": "database",
        "label": "数据库",
        "items": [
            {
                "key": "mysqlBinlogRetentionDays",
                "label": "MySQL Binlog 保留天数",
                "type": "number",
                "min": 0,
                "description": "（仅MySQL）自动清理多少天前的二进制日志（binlog）。0为不清理。需要SUPER或BINLOG_ADMIN权限。",
                "placeholder": "3",
            },
            {
                "key": "backupPath",
                "label": "备份存储路径",
                "type": "text",
                "description": "数据库备份文件的存储目录。Docker 环境建议使用默认路径。",
                "placeholder": "/app/config/sql_backup",
            },
            {
                "key": "backupRetentionCount",
                "label": "备份保留数量",
                "type": "number",
                "min": 1,
                "max": 100,
                "description": "保留最近多少个备份文件，超出的旧备份将被自动删除。",
                "placeholder": "5",
            },
        ],
        "customComponent": "DatabaseBackupManager",
    },
    {
        "key": "scraper_update",
        "label": "弹幕源自动更新",
        "items": [
            {
                "key": "scraperAutoUpdateInterval",
                "label": "检查间隔",
                "type": "number",
                "suffix": "分钟",
                "min": 15,
                "max": 1440,
                "description": "自动检查更新的时间间隔，最低15分钟，最高1440分钟（24小时）。修改后需要重启服务生效。",
                "placeholder": "30",
            },
        ],
    },
    {
        "key": "ui",
        "label": "界面设置",
        "items": [
            {
                "key": "pageSizeLibrary",
                "label": "弹幕库列表 - 分页数量",
                "type": "select",
                "options": [
                    {"value": "20", "label": "20 条/页"},
                    {"value": "50", "label": "50 条/页"},
                    {"value": "100", "label": "100 条/页"},
                    {"value": "200", "label": "200 条/页"},
                ],
                "description": "弹幕库页面的每页显示数量。修改后刷新页面生效。",
                "placeholder": "50",
            },
            {
                "key": "pageSizeEpisode",
                "label": "剧集列表 - 分页数量",
                "type": "select",
                "options": [
                    {"value": "20", "label": "20 条/页"},
                    {"value": "50", "label": "50 条/页"},
                    {"value": "100", "label": "100 条/页"},
                    {"value": "200", "label": "200 条/页"},
                ],
                "description": "剧集详情页面的每页显示数量。修改后刷新页面生效。",
                "placeholder": "50",
            },
            {
                "key": "pageSizeLocalItems",
                "label": "本地媒体列表 - 分页数量",
                "type": "select",
                "options": [
                    {"value": "20", "label": "20 条/页"},
                    {"value": "50", "label": "50 条/页"},
                    {"value": "100", "label": "100 条/页"},
                    {"value": "200", "label": "200 条/页"},
                ],
                "description": "本地媒体列表的每页显示数量。修改后刷新页面生效。",
                "placeholder": "50",
            },
            {
                "key": "pageSizeMediaItems",
                "label": "媒体服务器列表 - 分页数量",
                "type": "select",
                "options": [
                    {"value": "20", "label": "20 条/页"},
                    {"value": "50", "label": "50 条/页"},
                    {"value": "100", "label": "100 条/页"},
                    {"value": "200", "label": "200 条/页"},
                ],
                "description": "媒体服务器作品列表的每页显示数量。修改后刷新页面生效。",
                "placeholder": "100",
            },
            {
                "key": "pageSizeRefreshModal",
                "label": "追更管理列表 - 分页数量",
                "type": "select",
                "options": [
                    {"value": "20", "label": "20 条/页"},
                    {"value": "50", "label": "50 条/页"},
                    {"value": "100", "label": "100 条/页"},
                    {"value": "200", "label": "200 条/页"},
                ],
                "description": "追更与标记管理弹窗的每页显示数量。修改后刷新页面生效。",
                "placeholder": "20",
            },
        ],
    },
    {
        "key": "name_conversion",
        "label": "名称转换",
        "items": [
            {
                "key": "nameConversionEnabled",
                "label": "启用名称转换",
                "type": "boolean",
                "description": "搜索时自动将非中文名称转换为中文，提高在中文弹幕源中的搜索准确率。元数据源都失败时，若启用了AI名称转换则使用AI兜底。",
            },
            {
                "key": "nameConversionT2SEnabled",
                "label": "繁体自动转简体",
                "type": "boolean",
                "description": "搜索时自动将繁体中文标题转换为简体，提高在简体弹幕源中的搜索匹配率。此选项独立于上方的名称转换开关。",
            },
        ],
        "customComponent": {
            "type": "SortablePriorityList",
            "props": {
                "configKey": "nameConversionSourcePriority",
                "title": "元数据源查询优先级",
                "titleIcon": "🔢",
                "description": "名称转换时会并行查询所有启用的元数据源，按优先级顺序返回第一个有结果的中文名称。",
                "availableItems": [
                    {"key": "bangumi", "name": "Bangumi", "description": "动漫数据库，中文名称准确"},
                    {"key": "tmdb", "name": "TMDB", "description": "影视数据库，覆盖范围广"},
                    {"key": "tvdb", "name": "TVDB", "description": "电视剧数据库"},
                    {"key": "douban", "name": "Douban", "description": "豆瓣，国产剧/电影中文名称准确"},
                    {"key": "imdb", "name": "IMDB", "description": "国际电影数据库"},
                ],
                "tips": [
                    "拖拽调整优先级顺序，排在前面的源优先使用",
                    "关闭开关可禁用该源的查询",
                    "Bangumi 对动漫的中文名称更准确",
                    "Douban 对国产剧/电影的中文名称更准确",
                    "TMDB 覆盖范围更广，包含电影和电视剧",
                    "元数据源都失败时，若启用了AI名称转换则使用AI兜底",
                ],
                "showSwitch": True,
            },
        },
    },
]


def get_config_schema():
    """获取配置 Schema"""
    return CONFIG_SCHEMA


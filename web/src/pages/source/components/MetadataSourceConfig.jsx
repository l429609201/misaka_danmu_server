/**
 * 元信息搜索源特定配置组件
 * 根据不同的源类型显示不同的配置表单
 */
import { Form, Input, Switch, Button, Alert } from 'antd'
import { useState, useEffect, useRef } from 'react'
import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
  QuestionCircleOutlined,
  KeyOutlined,
} from '@ant-design/icons'
import {
  getBangumiConfig,
  getBangumiAuth,
  getBangumiAuthUrl,
  logoutBangumiAuth,
  getTmdbConfig,
  getTvdbConfig,
  getDoubanConfig,
} from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useModal } from '../../../ModalContext'
import dayjs from 'dayjs'

/**
 * Bangumi 配置组件
 */
export function BangumiConfig({ form }) {
  const { showMessage } = useMessage()
  const { confirm: showModal } = useModal()
  const [authMode, setAuthMode] = useState('token') // 'token' or 'oauth'
  const [authInfo, setAuthInfo] = useState({})
  const [showPassword, setShowPassword] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const oauthPopupRef = useRef(null)

  // 使用 ref 来存储当前状态，避免 useEffect 依赖导致重新加载
  const authModeRef = useRef(authMode)
  const authInfoRef = useRef(authInfo)

  useEffect(() => {
    authModeRef.current = authMode
  }, [authMode])

  useEffect(() => {
    authInfoRef.current = authInfo
  }, [authInfo])

  // 加载配置 - 只在组件挂载时执行一次
  useEffect(() => {
    loadConfig()

    // 监听 OAuth 完成消息
    const handleMessage = (event) => {
      if (event.data === 'BANGUMI-OAUTH-COMPLETE') {
        if (oauthPopupRef.current) {
          oauthPopupRef.current.close()
        }
        loadConfig()
      }
    }
    window.addEventListener('message', handleMessage)

    // 定期检查授权状态并自动刷新 (每5分钟检查一次)
    const checkAuthInterval = setInterval(() => {
      if (authModeRef.current === 'oauth' && authInfoRef.current.isAuthenticated) {
        loadConfig()
      }
    }, 5 * 60 * 1000) // 5分钟

    return () => {
      window.removeEventListener('message', handleMessage)
      clearInterval(checkAuthInterval)
    }
  }, []) // 空依赖数组，只在挂载时执行

  const loadConfig = async () => {
    try {
      const [configRes, authRes] = await Promise.all([
        getBangumiConfig(),
        getBangumiAuth()
      ])

      const config = configRes.data || configRes
      const auth = authRes.data || authRes

      // 使用后端返回的 authMode 字段
      const mode = config.authMode || (config.bangumiToken ? 'token' : 'oauth')
      setAuthMode(mode)

      form.setFieldsValue({
        bangumiToken: config.bangumiToken || '',
        bangumiClientId: config.bangumiClientId || '',
        bangumiClientSecret: config.bangumiClientSecret || '',
        authMode: mode, // 保存到表单中
      })

      setAuthInfo(auth || {})

      // 如果 token 被自动刷新,显示提示
      if (auth?.refreshed) {
        showMessage('success', '授权已自动延长')
      }
    } catch (error) {
      console.error('加载 Bangumi 配置失败:', error)
    }
  }

  const handleOAuthLogin = async () => {
    try {
      // 如果弹窗已存在且未关闭,聚焦到弹窗
      if (oauthPopupRef.current && !oauthPopupRef.current.closed) {
        oauthPopupRef.current.focus()
        return
      }

      // ani-rss 模式：前端用 location.origin 生成 redirect_uri
      const redirectUri = `${window.location.origin}/bgm-oauth-callback`

      const res = await getBangumiAuthUrl({ redirect_uri: redirectUri })
      const authUrl = res.data?.url || res.url
      if (!authUrl) {
        showMessage('error', '获取授权链接失败: 返回的URL为空')
        return
      }

      // 计算弹窗位置 (居中)
      const width = 600
      const height = 700
      const left = window.screen.width / 2 - width / 2
      const top = window.screen.height / 2 - height / 2

      // 打开弹窗
      oauthPopupRef.current = window.open(
        authUrl,
        'BangumiAuth',
        `width=${width},height=${height},top=${top},left=${left},resizable=yes,scrollbars=yes`
      )

      // 检测弹窗是否被拦截
      if (!oauthPopupRef.current || oauthPopupRef.current.closed) {
        showMessage('error', '弹窗被浏览器拦截，请允许弹窗后重试')
        return
      }

      // 定期检查弹窗是否被关闭
      const checkInterval = setInterval(() => {
        if (oauthPopupRef.current && oauthPopupRef.current.closed) {
          clearInterval(checkInterval)
          oauthPopupRef.current = null
        }
      }, 500)
    } catch (error) {
      showMessage('error', `获取授权链接失败: ${error.message}`)
    }
  }

  const handleLogout = () => {
    showModal({
      title: '注销',
      content: '确定要注销 Bangumi 授权吗？',
      onOk: async () => {
        try {
          await logoutBangumiAuth()
          loadConfig()
          showMessage('success', '已注销授权')
        } catch (error) {
          showMessage('error', `注销失败: ${error.message}`)
        }
      },
    })
  }

  return (
    <div className="space-y-4">
      <Alert
        message="Bangumi API 配置"
        description={
          <div className="space-y-1">
            <div>Bangumi 是一个动画、漫画、游戏等 ACG 作品的数据库，可以提供作品的元数据信息。</div>
            <div className="text-xs space-y-1 mt-2">
              <div>• <span className="font-medium">Access Token</span>: 有效期最长1年，配置简单，推荐使用</div>
              <div>• <span className="font-medium">OAuth 授权</span>: 有效期约7天，支持自动刷新（剩余≤3天时自动延长）</div>
            </div>
          </div>
        }
        type="info"
        showIcon
      />

      {/* 隐藏的 authMode 字段 */}
      <Form.Item name="authMode" hidden>
        <Input />
      </Form.Item>

      {/* 认证方式选择 */}
      <Form.Item label="认证方式">
        <Switch
          checkedChildren="OAuth 授权"
          unCheckedChildren="Access Token"
          checked={authMode === 'oauth'}
          onChange={(checked) => {
            const mode = checked ? 'oauth' : 'token'
            setAuthMode(mode)
            form.setFieldValue('authMode', mode)
          }}
        />
      </Form.Item>

      {/* OAuth 方式 */}
      {authMode === 'oauth' && (
        <>
          <Form.Item
            name="bangumiClientId"
            label={
              <span>
                App ID{' '}
                <a
                  href="https://bgm.tv/dev/app"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <QuestionCircleOutlined />
                </a>
              </span>
            }
            rules={[{ required: true, message: '请输入 App ID' }]}
            tooltip="在 bgm.tv/dev/app 创建应用后获取"
          >
            <Input placeholder="请输入 App ID" />
          </Form.Item>

          <Form.Item
            name="bangumiClientSecret"
            label="App Secret"
            rules={[{ required: true, message: '请输入 App Secret' }]}
            tooltip="应用的密钥，请妥善保管"
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入 App Secret"
              visibilityToggle={{
                visible: showPassword,
                onVisibleChange: setShowPassword,
              }}
              iconRender={(visible) =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
        </>
      )}

      {/* Access Token 方式 */}
      {authMode === 'token' && (
        <Form.Item
          name="bangumiToken"
          label={
            <span>
              Access Token{' '}
              <a
                href="https://next.bgm.tv/demo/access-token"
                target="_blank"
                rel="noopener noreferrer"
              >
                <QuestionCircleOutlined />
              </a>
            </span>
          }
          tooltip="有效期最长1年的访问令牌，在 next.bgm.tv/demo/access-token 获取"
        >
          <Input.Password
            prefix={<KeyOutlined />}
            placeholder="请输入 Access Token"
            visibilityToggle={{
              visible: showToken,
              onVisibleChange: setShowToken,
            }}
            iconRender={(visible) =>
              visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
            }
          />
        </Form.Item>
      )}

      {/* OAuth 授权状态 */}
      {authMode === 'oauth' && (
        <div className="border rounded p-4">
          <div className="font-medium mb-3">授权状态</div>
          {authInfo.isAuthenticated ? (
            <div className="space-y-3">
              {/* 用户信息卡片 */}
              <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-3">
                {/* 顶部：头像 + 基本信息 + 状态 */}
                <div className="flex items-center gap-3">
                  {/* 头像 */}
                  {authInfo.avatarUrl && (
                    <img
                      src={authInfo.avatarUrl}
                      alt={authInfo.nickname}
                      className="w-14 h-14 rounded-full object-cover border-2 border-gray-200 dark:border-gray-600 flex-shrink-0"
                      onError={(e) => {
                        e.target.style.display = 'none'
                      }}
                    />
                  )}
                  {/* 用户信息 */}
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-base truncate">{authInfo.nickname}</div>
                    {authInfo.username && (
                      <a
                        href={`https://bgm.tv/user/${authInfo.username}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-500 hover:text-blue-600 hover:underline"
                      >
                        @{authInfo.username}
                      </a>
                    )}
                    {!authInfo.username && (
                      <div className="text-xs text-gray-500">ID: {authInfo.bangumiUserId}</div>
                    )}
                  </div>
                  {/* 授权状态 */}
                  <div className="flex flex-col items-end gap-1 flex-shrink-0">
                    {(() => {
                      const now = dayjs()
                      const expiresAt = dayjs(authInfo.expiresAt)
                      const daysLeft = expiresAt.diff(now, 'day')
                      const isExpiringSoon = daysLeft <= 7 && daysLeft > 0
                      const isExpired = daysLeft < 0
                      return (
                        <div
                          className={`text-xs font-medium px-2 py-1 rounded ${
                            isExpired
                              ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400'
                              : isExpiringSoon
                              ? 'bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400'
                              : 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                          }`}
                        >
                          {isExpired ? '⚠️ 已过期' : isExpiringSoon ? `⚠️ ${daysLeft}天` : `✓ ${daysLeft}天`}
                        </div>
                      )
                    })()}
                    <div className="text-xs text-gray-500">
                      {dayjs(authInfo.authorizedAt).format('YYYY-MM-DD')}
                    </div>
                  </div>
                </div>

                {/* 签名/简介 */}
                {authInfo.sign && (
                  <div className="text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-2 break-words">
                    {authInfo.sign}
                  </div>
                )}

                {/* 详细信息行 */}
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-700 pt-2">
                  <span>UID: {authInfo.bangumiUserId}</span>
                  {authInfo.username && (
                    <a
                      href={`https://bgm.tv/user/${authInfo.username}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-500 hover:text-blue-600 hover:underline"
                    >
                      主页 ↗
                    </a>
                  )}
                </div>
              </div>

              {/* 操作按钮 */}
              <div className="flex gap-2 pt-1">
                {(() => {
                  const now = dayjs()
                  const expiresAt = dayjs(authInfo.expiresAt)
                  const daysLeft = expiresAt.diff(now, 'day')
                  const showRenewButton = daysLeft <= 7
                  return (
                    showRenewButton && (
                      <Button size="small" type="primary" onClick={handleOAuthLogin}>
                        延长授权
                      </Button>
                    )
                  )
                })()}
                <Button size="small" danger onClick={handleLogout}>
                  注销
                </Button>
              </div>
            </div>
          ) : authInfo.isExpired ? (
            <div className="text-center py-4">
              <div className="mb-2 text-orange-500 font-medium">⚠️ 授权已过期</div>
              <div className="mb-3 text-sm text-gray-500">请重新授权以继续使用 Bangumi 功能</div>
              <Button type="primary" onClick={handleOAuthLogin}>
                重新授权
              </Button>
            </div>
          ) : (
            <div className="text-center py-4">
              <div className="mb-3 text-sm text-gray-500">当前未授权。授权后可使用更多功能。</div>
              <Button type="primary" onClick={handleOAuthLogin}>
                通过 Bangumi 登录
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * TMDB 配置组件
 */
export function TMDBConfig({ form }) {
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    try {
      const response = await getTmdbConfig()
      const config = response.data || response
      console.log('TMDB 配置:', config)
      form.setFieldsValue({
        tmdbApiKey: config.tmdbApiKey || '',
        tmdbApiBaseUrl: config.tmdbApiBaseUrl || 'https://api.themoviedb.org',
        tmdbImageBaseUrl: config.tmdbImageBaseUrl || 'https://image.tmdb.org',
      })
    } catch (error) {
      console.error('加载 TMDB 配置失败:', error)
    }
  }

  return (
    <div className="space-y-4">
      <Alert
        message="TMDB 配置"
        description="The Movie Database (TMDB) 是一个电影和电视节目的数据库，可以提供作品的元数据信息。"
        type="info"
        showIcon
      />

      <Form.Item
        name="tmdbApiKey"
        label={
          <span>
            API Key{' '}
            <a
              href="https://www.themoviedb.org/settings/api"
              target="_blank"
              rel="noopener noreferrer"
            >
              <QuestionCircleOutlined />
            </a>
          </span>
        }
        rules={[{ required: true, message: '请输入 TMDB API Key' }]}
      >
        <Input.Password
          placeholder="请输入 TMDB API Key"
          prefix={<KeyOutlined />}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        在{' '}
        <a
          href="https://www.themoviedb.org/settings/api"
          target="_blank"
          rel="noopener noreferrer"
        >
          TMDB 设置页面
        </a>{' '}
        获取 API Key
      </div>

      <Form.Item
        name="tmdbApiBaseUrl"
        label="API 域名"
        rules={[{ required: true, message: '请输入 TMDB API 域名' }]}
      >
        <Input placeholder="https://api.themoviedb.org" />
      </Form.Item>

      <Form.Item
        name="tmdbImageBaseUrl"
        label="图片域名"
        rules={[{ required: true, message: '请输入 TMDB 图片域名' }]}
      >
        <Input placeholder="https://image.tmdb.org" />
      </Form.Item>
    </div>
  )
}

/**
 * TVDB 配置组件
 */
export function TVDBConfig({ form }) {
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    try {
      const response = await getTvdbConfig()
      const config = response.data || response
      form.setFieldsValue({
        tvdbApiKey: config.tvdbApiKey || '',
      })
    } catch (error) {
      console.error('加载 TVDB 配置失败:', error)
    }
  }

  return (
    <div className="space-y-4">
      <Alert
        message="TVDB 配置"
        description="The TVDB 是一个电视节目的数据库，可以提供电视节目的元数据信息。"
        type="info"
        showIcon
      />

      <Form.Item
        name="tvdbApiKey"
        label={
          <span>
            API Key{' '}
            <a
              href="https://thetvdb.com/dashboard/account/apikeys"
              target="_blank"
              rel="noopener noreferrer"
            >
              <QuestionCircleOutlined />
            </a>
          </span>
        }
        rules={[{ required: true, message: '请输入 TVDB API Key' }]}
      >
        <Input.Password
          placeholder="请输入 TVDB API Key"
          prefix={<KeyOutlined />}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        在{' '}
        <a
          href="https://thetvdb.com/dashboard/account/apikeys"
          target="_blank"
          rel="noopener noreferrer"
        >
          TVDB API Keys 页面
        </a>{' '}
        获取 API Key
      </div>
    </div>
  )
}

/**
 * 豆瓣配置组件
 */
export function DoubanConfig({ form }) {
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    try {
      const response = await getDoubanConfig()
      const config = response.data || response
      form.setFieldsValue({
        doubanCookie: config.doubanCookie || '',
      })
    } catch (error) {
      console.error('加载豆瓣配置失败:', error)
    }
  }

  return (
    <div className="space-y-4">
      <Alert
        message="豆瓣配置"
        description="豆瓣是一个提供书籍、电影、音乐等作品信息的社区网站，可以提供作品的元数据信息。"
        type="info"
        showIcon
      />

      <Form.Item
        name="doubanCookie"
        label="Cookie"
        rules={[{ required: true, message: '请输入豆瓣 Cookie' }]}
      >
        <Input.TextArea
          placeholder="请输入豆瓣 Cookie"
          rows={4}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        <div>在浏览器中登录豆瓣后，打开开发者工具 (F12)，在 Network 标签页中找到任意请求，复制 Cookie 值</div>
        <div className="mt-1">Cookie 格式示例: bid=xxx; dbcl2=xxx; ...</div>
      </div>
    </div>
  )
}

/**
 * IMDb配置组件
 */
export function ImdbConfig({ form }) {
  const [useApi, setUseApi] = useState(true)

  useEffect(() => {
    // 从表单获取初始值
    const initialValue = form.getFieldValue('imdbUseApi')
    setUseApi(initialValue ?? true)
  }, [form])

  return (
    <div className="space-y-4">
      <Alert
        message="IMDb 配置"
        description={
          <div className="space-y-1">
            <div>IMDb 是全球最大的电影数据库，提供电影、电视节目等作品的元数据信息。</div>
            <div className="text-xs space-y-1 mt-2">
              <div>• <span className="font-medium">第三方API</span>: 使用 api.imdbapi.dev，速度快，推荐使用</div>
              <div>• <span className="font-medium">官方网站</span>: HTML解析官方网站，更稳定但速度较慢</div>
            </div>
          </div>
        }
        type="info"
        showIcon
      />

      {/* 数据源选择 */}
      <Form.Item label="数据源">
        <Switch
          checkedChildren="第三方API"
          unCheckedChildren="官方网站"
          checked={useApi}
          onChange={(checked) => {
            setUseApi(checked)
            form.setFieldValue('imdbUseApi', checked)
          }}
        />
      </Form.Item>

      {/* 启用兜底 */}
      <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
        <Form.Item
          name="imdbEnableFallback"
          label="启用兜底"
          valuePropName="checked"
          className="min-w-[100px] shrink-0 !mb-0"
        >
          <Switch />
        </Form.Item>
        <div className="w-full text-gray-500">
          当主方式失败时,自动尝试另一种方式。默认开启。
        </div>
      </div>
    </div>
  )
}


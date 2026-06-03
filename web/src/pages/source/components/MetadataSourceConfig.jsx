/**
 * 元信息搜索源特定配置组件
 * 根据不同的源类型显示不同的配置表单
 */
import { Form, Input, Switch, Button, Alert } from 'antd'
import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
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
  refreshBangumiAuth,
  getTmdbConfig,
  getTvdbConfig,
  getDoubanConfig,
  getTraktAuthStatus,
  logoutTraktAuth,
} from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useModal } from '../../../ModalContext'
import dayjs from 'dayjs'

/**
 * Bangumi 配置组件
 */
export function BangumiConfig({ form }) {
  const { t } = useTranslation()
  const messageApi = useMessage()
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
        bangumiApiBaseUrl: config.bangumiApiBaseUrl || 'https://api.bgm.tv',
        bangumiImageBaseUrl: config.bangumiImageBaseUrl || 'https://lain.bgm.tv',
        authMode: mode, // 保存到表单中
      })

      setAuthInfo(auth || {})

      // 如果 token 被自动刷新,显示提示
      if (auth?.refreshed) {
        messageApi.success(t('metadataConfig.authExtended'))
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
        messageApi.error(t('metadataConfig.getAuthUrlFailedEmpty'))
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
        messageApi.error(t('metadataConfig.popupBlocked'))
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
      const detail = error.response?.data?.detail || error.message
      messageApi.error(`${t('metadataConfig.getAuthUrlFailed')}: ${detail}`)
    }
  }

  const handleLogout = () => {
    showModal({
      title: t('metadataConfig.logout'),
      content: t('metadataConfig.confirmLogoutBangumi'),
      onOk: async () => {
        try {
          await logoutBangumiAuth()
          loadConfig()
          messageApi.success(t('metadataConfig.loggedOut'))
        } catch (error) {
          messageApi.error(`${t('metadataConfig.logoutFailed')}: ${error.message}`)
        }
      },
    })
  }

  const [refreshing, setRefreshing] = useState(false)

  const handleRefreshToken = async () => {
    try {
      setRefreshing(true)
      const res = await refreshBangumiAuth()
      if (res.data?.success) {
        messageApi.success(t('metadataConfig.authRenewed'))
        loadConfig()
      } else {
        messageApi.error(res.data?.message || t('metadataConfig.renewFailedReauth'))
      }
    } catch (error) {
      messageApi.error(`${t('metadataConfig.renewFailed')}: ${error?.response?.data?.detail || error.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="space-y-4">
      <Alert
        message={t('metadataConfig.bangumiApiConfig')}
        description={
          <div className="space-y-1">
            <div>{t('metadataConfig.bangumiDesc')}</div>
            <div className="text-xs space-y-1 mt-2">
              <div>• <span className="font-medium">Access Token</span>: {t('metadataConfig.bangumiTokenDescBody')}</div>
              <div>• <span className="font-medium">OAuth 授权</span>: {t('metadataConfig.bangumiOAuthDescBody')}</div>
            </div>
          </div>
        }
        type="info"
        showIcon
      />

      <Form.Item
        name="bangumiApiBaseUrl"
        label={t('metadataConfig.apiDomain')}
        tooltip={t('metadataConfig.bangumiApiDomainTip')}
      >
        <Input placeholder="https://api.bgm.tv" />
      </Form.Item>

      <Form.Item
        name="bangumiImageBaseUrl"
        label={t('metadataConfig.imageDomain')}
        tooltip={t('metadataConfig.bangumiImageDomainTip')}
      >
        <Input placeholder="https://lain.bgm.tv" />
      </Form.Item>

      {/* 隐藏的 authMode 字段 */}
      <Form.Item name="authMode" hidden>
        <Input />
      </Form.Item>

      {/* 认证方式选择 */}
      <Form.Item label={t('metadataConfig.authMethod')}>
        <Switch
          checkedChildren={t('metadataConfig.oauthAuth')}
          unCheckedChildren={t('metadataConfig.accessToken')}
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
            tooltip={t('metadataConfig.appIdTip')}
          >
            <Input placeholder={t('metadataConfig.inputAppId')} />
          </Form.Item>

          <Form.Item
            name="bangumiClientSecret"
            label="App Secret"
            tooltip={t('metadataConfig.appSecretTip')}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('metadataConfig.inputAppSecret')}
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
          tooltip={t('metadataConfig.accessTokenTip')}
        >
          <Input.Password
            prefix={<KeyOutlined />}
            placeholder={t('metadataConfig.inputAccessToken')}
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
          <div className="font-medium mb-3">{t('metadataConfig.authStatus')}</div>
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
                          {isExpired ? t('metadataConfig.expired') : isExpiringSoon ? t('metadataConfig.expiringSoon', { days: daysLeft }) : t('metadataConfig.valid', { days: daysLeft })}
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
                      {t('metadataConfig.homepage')}
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
                      <Button size="small" type="primary" loading={refreshing} onClick={handleRefreshToken}>
                        {t('metadataConfig.extendAuth')}
                      </Button>
                    )
                  )
                })()}
                <Button size="small" danger onClick={handleLogout}>
                  {t('metadataConfig.logout')}
                </Button>
              </div>
            </div>
          ) : authInfo.isExpired ? (
            <div className="text-center py-4">
              <div className="mb-2 text-orange-500 font-medium">{t('metadataConfig.authExpiredTitle')}</div>
              <div className="mb-3 text-sm text-gray-500">{t('metadataConfig.reauthTip')}</div>
              <Button type="primary" onClick={handleOAuthLogin}>
                {t('metadataConfig.reauth')}
              </Button>
            </div>
          ) : (
            <div className="text-center py-4">
              <div className="mb-3 text-sm text-gray-500">{t('metadataConfig.notAuthorizedTip')}</div>
              <Button type="primary" onClick={handleOAuthLogin}>
                {t('metadataConfig.loginViaBangumi')}
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
  const { t } = useTranslation()
  useEffect(() => {
    loadConfig()
  }, [])

  const loadConfig = async () => {
    try {
      const response = await getTmdbConfig()
      const config = response.data || response
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
        message={t('metadataConfig.tmdbConfig')}
        description={t('metadataConfig.tmdbDesc')}
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
        rules={[{ required: true, message: t('metadataConfig.inputTmdbApiKey') }]}
      >
        <Input.Password
          placeholder={t('metadataConfig.inputTmdbApiKey')}
          prefix={<KeyOutlined />}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        {t('metadataConfig.atText')}{' '}
        <a
          href="https://www.themoviedb.org/settings/api"
          target="_blank"
          rel="noopener noreferrer"
        >
          {t('metadataConfig.tmdbSettingsPage')}
        </a>{' '}
        {t('metadataConfig.getApiKey')}
      </div>

      <Form.Item
        name="tmdbApiBaseUrl"
        label={t('metadataConfig.apiDomain')}
        rules={[{ required: true, message: t('metadataConfig.inputTmdbApiDomain') }]}
      >
        <Input placeholder="https://api.themoviedb.org" />
      </Form.Item>

      <Form.Item
        name="tmdbImageBaseUrl"
        label={t('metadataConfig.imageDomain')}
        rules={[{ required: true, message: t('metadataConfig.inputTmdbImageDomain') }]}
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
  const { t } = useTranslation()
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
        message={t('metadataConfig.tvdbConfig')}
        description={t('metadataConfig.tvdbDesc')}
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
        rules={[{ required: true, message: t('metadataConfig.inputTvdbApiKey') }]}
      >
        <Input.Password
          placeholder={t('metadataConfig.inputTvdbApiKey')}
          prefix={<KeyOutlined />}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        {t('metadataConfig.atText')}{' '}
        <a
          href="https://thetvdb.com/dashboard/account/apikeys"
          target="_blank"
          rel="noopener noreferrer"
        >
          {t('metadataConfig.tvdbApiKeysPage')}
        </a>{' '}
        {t('metadataConfig.getApiKey')}
      </div>
    </div>
  )
}

/**
 * 豆瓣配置组件
 */
export function DoubanConfig({ form }) {
  const { t } = useTranslation()
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
        message={t('metadataConfig.doubanConfig')}
        description={t('metadataConfig.doubanDesc')}
        type="info"
        showIcon
      />

      <Form.Item
        name="doubanCookie"
        label="Cookie"
      >
        <Input.TextArea
          placeholder={t('metadataConfig.inputDoubanCookie')}
          rows={4}
        />
      </Form.Item>
      <div className="text-gray-500 text-sm -mt-2">
        <div>{t('metadataConfig.doubanCookieTip')}</div>
        <div className="mt-1">{t('metadataConfig.doubanCookieExample')}</div>
      </div>
    </div>
  )
}

/**
 * IMDb配置组件
 */
export function ImdbConfig({ form }) {
  const { t } = useTranslation()
  const [useApi, setUseApi] = useState(true)

  useEffect(() => {
    // 从表单获取初始值
    const initialValue = form.getFieldValue('imdbUseApi')
    setUseApi(initialValue ?? true)
  }, [form])

  return (
    <div className="space-y-4">
      <Alert
        message={t('metadataConfig.imdbConfig')}
        description={
          <div className="space-y-1">
            <div>{t('metadataConfig.imdbDesc')}</div>
            <div className="text-xs space-y-1 mt-2">
              <div>• <span className="font-medium">{t('metadataConfig.thirdPartyApi')}</span>: {t('metadataConfig.imdbApiDescBody')}</div>
              <div>• <span className="font-medium">{t('metadataConfig.officialWebsite')}</span>: {t('metadataConfig.imdbWebDescBody')}</div>
            </div>
          </div>
        }
        type="info"
        showIcon
      />

      {/* 数据源选择 */}
      <Form.Item label={t('metadataConfig.dataSource')}>
        <Switch
          checkedChildren={t('metadataConfig.thirdPartyApi')}
          unCheckedChildren={t('metadataConfig.officialWebsite')}
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
          label={t('metadataConfig.enableFallback')}
          valuePropName="checked"
          className="min-w-[100px] shrink-0 !mb-0"
        >
          <Switch />
        </Form.Item>
        <div className="w-full text-gray-500">
          {t('metadataConfig.fallbackTip')}
        </div>
      </div>
    </div>
  )
}



/**
 * Trakt 配置组件 — CF Worker OAuth 认证方式
 */
const TRAKT_OAUTH_WORKER_URL = 'https://danmu-api.misaka10876.top'

export function TraktConfig({ form }) {
  const { t } = useTranslation()
  const messageApi = useMessage()
  const { confirm: showModal } = useModal()
  const [authInfo, setAuthInfo] = useState({})

  useEffect(() => {
    loadAuth()

    // 监听 OAuth 回调弹窗的完成消息
    const handleMessage = (event) => {
      if (event.data === 'TRAKT-OAUTH-COMPLETE') {
        loadAuth()
        messageApi.success(t('metadataConfig.traktAuthSuccess'))
      }
    }
    window.addEventListener('message', handleMessage)

    return () => {
      window.removeEventListener('message', handleMessage)
    }
  }, [])

  const loadAuth = async () => {
    try {
      const res = await getTraktAuthStatus()
      setAuthInfo(res.data || res)
    } catch (e) { console.error('Failed to load Trakt auth:', e) }
  }

  const handleOAuthLogin = () => {
    const redirectUri = `${window.location.origin}/trakt-oauth-callback`
    const loginUrl = `${TRAKT_OAUTH_WORKER_URL}/oauth/login?provider=trakt&redirect_uri=${encodeURIComponent(redirectUri)}`

    const width = 600
    const height = 700
    const left = window.screen.width / 2 - width / 2
    const top = window.screen.height / 2 - height / 2
    window.open(loginUrl, 'trakt-oauth', `width=${width},height=${height},top=${top},left=${left},resizable=yes,scrollbars=yes`)
  }

  const handleLogout = () => {
    showModal({
      title: t('metadataConfig.logout'),
      content: t('metadataConfig.confirmLogoutTrakt'),
      onOk: async () => {
        try {
          await logoutTraktAuth()
          loadAuth()
          messageApi.success(t('metadataConfig.loggedOut'))
        } catch (e) { messageApi.error(e.message) }
      }
    })
  }

  return (
    <div className="space-y-4">
      <Alert
        message="Trakt API"
        description={
          <div className="space-y-1">
            <div>{t('metadataConfig.traktDesc')}</div>
            <div className="text-xs mt-1 text-gray-400">{t('metadataConfig.traktAuthPopupTip')}</div>
          </div>
        }
        type="info"
        showIcon
      />

      {/* 授权状态 */}
      <div className="border rounded p-4">
        <div className="font-medium mb-3">{t('metadataConfig.authStatus')}</div>
        {authInfo.isAuthenticated ? (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-green-500">✓</span>
              <span className="font-medium">{t('metadataConfig.authorized')}</span>
              {authInfo.providerUsername && <span className="text-gray-500 text-sm">({authInfo.providerUsername})</span>}
            </div>
            <Button danger size="small" onClick={handleLogout}>{t('metadataConfig.logout')}</Button>
          </div>
        ) : (
          <div className="text-center py-4">
            <div className="mb-3 text-sm text-gray-500">{t('metadataConfig.traktNotAuthorized')}</div>
            <Button type="primary" onClick={handleOAuthLogin}>
              {t('metadataConfig.loginViaTrakt')}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

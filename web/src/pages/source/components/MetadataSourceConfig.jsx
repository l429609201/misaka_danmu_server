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
  const { showModal } = useModal()
  const [authMode, setAuthMode] = useState('token') // 'token' or 'oauth'
  const [authInfo, setAuthInfo] = useState({})
  const [showPassword, setShowPassword] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const oauthPopupRef = useRef(null)

  // 加载配置
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
    return () => {
      window.removeEventListener('message', handleMessage)
    }
  }, [])

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
      if (oauthPopupRef.current && !oauthPopupRef.current.closed) {
        oauthPopupRef.current.focus()
      } else {
        const res = await getBangumiAuthUrl()
        const width = 600
        const height = 700
        const left = window.screen.width / 2 - width / 2
        const top = window.screen.height / 2 - height / 2
        oauthPopupRef.current = window.open(
          res.url,
          'BangumiAuth',
          `width=${width},height=${height},top=${top},left=${left}`
        )
      }
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
          <div className="font-medium mb-2">授权状态</div>
          {authInfo.isAuthenticated ? (
            <div className="space-y-2">
              <div className="text-sm">
                <span className="text-gray-500">用户ID:</span> {authInfo.bangumiUserId}
              </div>
              <div className="text-sm">
                <span className="text-gray-500">昵称:</span> {authInfo.nickname}
              </div>
              <div className="text-sm">
                <span className="text-gray-500">授权于:</span>{' '}
                {dayjs(authInfo.authorizedAt).format('YYYY-MM-DD HH:mm')}
              </div>
              <div className="text-sm">
                <span className="text-gray-500">过期于:</span>{' '}
                {dayjs(authInfo.expiresAt).format('YYYY-MM-DD HH:mm')}
              </div>
              {(() => {
                const now = dayjs()
                const expiresAt = dayjs(authInfo.expiresAt)
                const daysLeft = expiresAt.diff(now, 'day')
                const isExpiringSoon = daysLeft <= 7 && daysLeft > 0
                const isExpired = daysLeft < 0

                return (
                  <div
                    className={`text-xs font-medium ${
                      isExpired
                        ? 'text-red-500'
                        : isExpiringSoon
                        ? 'text-orange-500'
                        : 'text-green-500'
                    }`}
                  >
                    {isExpired ? (
                      <>⚠️ 授权已过期</>
                    ) : isExpiringSoon ? (
                      <>⚠️ 剩余 {daysLeft} 天过期</>
                    ) : (
                      <>✓ 剩余 {daysLeft} 天</>
                    )}
                  </div>
                )
              })()}
              <div className="mt-2 space-x-2">
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
            <div className="text-center py-2">
              <div className="mb-2 text-orange-500 font-medium">⚠️ 授权已过期</div>
              <div className="mb-2 text-sm text-gray-500">请重新授权以继续使用 Bangumi 功能</div>
              <Button type="primary" onClick={handleOAuthLogin}>
                重新授权
              </Button>
            </div>
          ) : (
            <div className="text-center py-2">
              <div className="mb-2 text-sm">当前未授权。授权后可使用更多功能。</div>
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


import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, Button, Card, Divider, Dropdown } from 'antd'
import {
  UserOutlined,
  LockOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  KeyOutlined,
  ClearOutlined,
  GlobalOutlined,
  DownOutlined,
} from '@ant-design/icons'
import { login, autoLogin, getUserInfo, getPasskeyLoginOptions, verifyPasskeyLogin } from '../../apis'
import { useNavigate } from 'react-router-dom'
import Cookies from 'js-cookie'
import { useMessage } from '../../MessageContext'
import { MfaVerifyModal, base64urlToBuffer, bufferToBase64url } from '../../components/MfaVerifyModal'
import { clearBrowserCache } from '../../utils/clearCache'
import { isPasskeySupported } from '../../utils/passkey'
import { SUPPORTED_LANGUAGES } from '../../i18n'

export const Login = () => {
  const { t, i18n } = useTranslation()
  const [form] = Form.useForm()
  const [isLoading, setIsLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [checkingWhitelist, setCheckingWhitelist] = useState(true)
  const [mfaModalOpen, setMfaModalOpen] = useState(false)
  const [mfaTypes, setMfaTypes] = useState([])
  const [mfaToken, setMfaToken] = useState('')
  const [mfaUsername, setMfaUsername] = useState('')
  const [passkeyLoginLoading, setPasskeyLoginLoading] = useState(false)
  const navigate = useNavigate()
  const messageApi = useMessage()

  const currentLanguage = SUPPORTED_LANGUAGES.find(lang => lang.key === i18n.resolvedLanguage)
    || SUPPORTED_LANGUAGES.find(lang => lang.key === i18n.language)
    || SUPPORTED_LANGUAGES[0]
  const getLanguageSymbol = (key) => {
    if (key === 'en') return 'EN'
    if (key === 'zh-TW') return '繁'
    return '简'
  }
  const languageMenuItems = SUPPORTED_LANGUAGES.map(lang => {
    const selected = currentLanguage.key === lang.key
    return {
      key: lang.key,
      label: (
        <span className={`flex items-center gap-2 min-w-32 ${selected ? 'font-semibold text-primary' : ''}`}>
          <span className={`inline-flex h-5 w-5 items-center justify-center rounded-md text-[10px] ${selected ? 'bg-primary text-white' : 'bg-gray-100 text-gray-500 dark:bg-white/10 dark:text-gray-300'}`}>
            {selected ? '✓' : getLanguageSymbol(lang.key)}
          </span>
          <span>{lang.name}</span>
        </span>
      ),
    }
  })
  const handleLanguageChange = ({ key }) => {
    i18n.changeLanguage(key)
  }


  // 页面加载时先校验已保存登录状态，失效后再尝试白名单自动登录
  useEffect(() => {
    let cancelled = false

    const checkLoginState = async () => {
      const token = Cookies.get('danmu_token')

      // 如果已有 token，必须先校验有效性，避免残留旧 token 导致跳首页后 401 循环
      if (token) {
        try {
          const res = await getUserInfo()
          if (!cancelled && res.data?.username) {
            navigate('/')
          }
          return
        } catch (error) {
          Cookies.remove('danmu_token', { path: '/' })
          if (!cancelled) {
            setCheckingWhitelist(false)
          }
          return
        }
      }

      // 尝试白名单自动登录
      try {
        const res = await autoLogin()
        const { accessToken, expiresIn } = res.data
        const expiresInMinutes = (!expiresIn || expiresIn <= 0) ? (365 * 24 * 60) : expiresIn
        const expiresInDays = expiresInMinutes / (60 * 24)
        Cookies.set('danmu_token', accessToken, {
          expires: expiresInDays,
          path: '/',
          secure: location.protocol === 'https:',
          sameSite: 'lax'
        })
        if (!cancelled) {
          messageApi.success(t('login.whitelistLoginSuccess'))
          navigate('/')
        }
      } catch (error) {
        // 不在白名单中，显示登录表单
        if (!cancelled) {
          setCheckingWhitelist(false)
        }
      }
    }

    checkLoginState()

    return () => {
      cancelled = true
    }
  }, [messageApi, navigate])

  // 保存 token 并跳转
  const saveTokenAndNavigate = useCallback((accessToken, expiresIn) => {
    // expiresIn 为 -1 表示永不过期，使用 365 天；为 0/undefined 使用默认 3 天
    const expiresInMinutes = (!expiresIn || expiresIn <= 0) ? (365 * 24 * 60) : expiresIn
    const expiresInDays = expiresInMinutes / (60 * 24)
    Cookies.set('danmu_token', accessToken, {
      expires: expiresInDays,
      path: '/',
      secure: location.protocol === 'https:',
      sameSite: 'lax'
    })
    messageApi.success(t('login.loginSuccess'))
    navigate('/')
  }, [messageApi, navigate])

  // 处理登录逻辑
  const handleLogin = async values => {
    try {
      setIsLoading(true)
      const res = await login(values)

      if (res.data.accessToken) {
        saveTokenAndNavigate(res.data.accessToken, res.data.expiresIn)
      } else {
        messageApi.error(t('login.loginFailed'))
      }
    } catch (error) {
      // 检查是否是 403 MFA 要求
      if (error.code === 403 && error.mfaRequired) {
        setMfaTypes(error.mfaTypes || [])
        setMfaToken(error.mfaToken || '')
        setMfaUsername(values.username || '')
        setMfaModalOpen(true)
      } else if (error.code === 429) {
        // 暴力破解防护：登录次数过多
        messageApi.error(error.message || t('login.tooManyAttempts'))
      } else {
        console.error('登录失败:', error)
        messageApi.error(t('login.loginFailed'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  // MFA 验证成功回调（MfaVerifyModal 直接返回 JWT 数据）
  const handleMfaSuccess = useCallback((tokenData) => {
    setMfaModalOpen(false)
    if (tokenData.accessToken) {
      saveTokenAndNavigate(tokenData.accessToken, tokenData.expiresIn)
    }
  }, [saveTokenAndNavigate])

  // PassKey 无密码直接登录
  const handlePasskeyLogin = useCallback(async () => {
    if (!isPasskeySupported()) {
      messageApi.error(t('login.passkeyHttpsOnly'))
      return
    }
    setPasskeyLoginLoading(true)
    try {
      // 1. 获取认证选项
      const optionsRes = await getPasskeyLoginOptions()
      const options = JSON.parse(optionsRes.data.options)
      const passkeySessionId = optionsRes.data.sessionId
      options.challenge = base64urlToBuffer(options.challenge)
      if (options.allowCredentials) {
        options.allowCredentials = options.allowCredentials.map(c => ({
          ...c, id: base64urlToBuffer(c.id),
        }))
      }

      // 2. 浏览器 WebAuthn
      const credential = await navigator.credentials.get({ publicKey: options })
      const credJSON = JSON.stringify({
        id: credential.id,
        rawId: credential.id,
        type: credential.type,
        response: {
          authenticatorData: bufferToBase64url(credential.response.authenticatorData),
          clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
          signature: bufferToBase64url(credential.response.signature),
          userHandle: credential.response.userHandle
            ? bufferToBase64url(credential.response.userHandle)
            : null,
        },
      })

      // 3. 服务端验证 → 直接拿 JWT
      const res = await verifyPasskeyLogin({ credential: credJSON, session_id: passkeySessionId })
      if (res.data.accessToken) {
        saveTokenAndNavigate(res.data.accessToken, res.data.expiresIn)
      }
    } catch (err) {
      if (err.name === 'NotAllowedError') {
        messageApi.info(t('login.passkeyCancelled'))
      } else {
        console.error('PassKey 登录失败:', err)
        messageApi.error(t('login.passkeyFailed'))
      }
    } finally {
      setPasskeyLoginLoading(false)
    }
  }, [saveTokenAndNavigate, messageApi])

  return (
    <div className="my-6 flex items-center justify-center relative">
      {/* 白名单检查中显示加载状态 */}
      {checkingWhitelist ? (
        <Card className="w-full max-w-md rounded-xl shadow-lg overflow-hidden mx-auto">
          <div className="text-center py-12">
            <p className="text-base-text text-lg">{t('login.checkingWhitelist')}</p>
          </div>
        </Card>
      ) : (
        /* 登录卡片容器 */
        <Card className="w-full max-w-md rounded-xl shadow-lg overflow-hidden mx-auto relative px-2 sm:px-0">
          {/* 卡片左上角：清理浏览器缓存 */}
          <Button
            type="link"
            size="small"
            icon={<ClearOutlined />}
            onClick={clearBrowserCache}
            className="!absolute top-3 left-3 z-10 !px-1 !text-gray-400 hover:!text-primary sm:top-4 sm:left-4"
          >
            {t('login.clearBrowserCache')}
          </Button>

          {/* 卡片右上角：语言切换 */}
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            menu={{
              items: languageMenuItems,
              selectedKeys: [currentLanguage.key],
              onClick: handleLanguageChange,
            }}
          >
            <button
              type="button"
              className="absolute top-3 right-3 z-10 inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white/90 px-2.5 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition hover:border-primary/40 hover:text-primary dark:border-white/10 dark:bg-white/8 dark:text-gray-300 dark:hover:border-primary/50 sm:top-4 sm:right-4"
            >
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-primary/10 px-1 text-[10px] font-bold text-primary">
                {getLanguageSymbol(currentLanguage.key)}
              </span>
              <span>{currentLanguage.name}</span>
              <DownOutlined className="text-[10px] opacity-70" />
            </button>
          </Dropdown>

          {/* 登录标题区域 */}
          <div className="text-center mb-8 pt-4">
            <h2 className="text-[clamp(1.5rem,3vw,2rem)] font-bold text-base-text">
              {t('login.accountLogin')}
            </h2>
            <p className="text-base-text mt-2">{t('login.inputAccountTip')}</p>
          </div>

          {/* 表单区域 */}
          <Form
            form={form}
            layout="vertical"
            onFinish={handleLogin}
            className="px-6 pb-6"
            size="large"
          >
            {/* 用户名输入 */}
            <Form.Item
              name="username"
              label={t('login.username')}
              rules={[{ required: true, message: t('login.inputUsername') }]}
              className="mb-4"
            >
              <Input
                prefix={<UserOutlined className="text-gray-400" />}
                placeholder={t('login.inputUsername')}
                autoComplete="username"
              />
            </Form.Item>

            {/* 密码输入 */}
            <Form.Item
              name="password"
              label={t('login.password')}
              rules={[{ required: true, message: t('login.inputPassword') }]}
              className="mb-6"
            >
              <Input.Password
                prefix={<LockOutlined className="text-gray-400" />}
                placeholder={t('login.inputPassword')}
                autoComplete="current-password"
                visibilityToggle={{
                  visible: showPassword,
                  onVisibleChange: setShowPassword,
                }}
                iconRender={visible =>
                  visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
                }
              />
            </Form.Item>

            {/* 登录按钮 */}
            <Form.Item className="!mb-2">
              <Button block type="primary" htmlType="submit" loading={isLoading}>
                {t('login.login')}
              </Button>
            </Form.Item>

          </Form>

          {/* PassKey 无密码登录（仅 HTTPS 模式可用） */}
          {isPasskeySupported() && (
            <>
              <Divider plain className="!mt-0 !mb-3 px-6">{t('login.or')}</Divider>
              <div className="px-6 pb-6">
                <Button
                  block
                  icon={<KeyOutlined />}
                  loading={passkeyLoginLoading}
                  onClick={handlePasskeyLogin}
                >
                  {t('login.loginWithPasskey')}
                </Button>
              </div>
            </>
          )}
        </Card>      )}

      {/* MFA 验证弹窗 */}
      <MfaVerifyModal
        open={mfaModalOpen}
        onCancel={() => setMfaModalOpen(false)}
        onSuccess={handleMfaSuccess}
        mfaTypes={mfaTypes}
        mfaToken={mfaToken}
        username={mfaUsername}
      />
    </div>
  )
}

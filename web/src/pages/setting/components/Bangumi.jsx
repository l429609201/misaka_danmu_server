import {
  Avatar,
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Switch,
  Tooltip,
} from 'antd'
import {
  getBangumiAuth,
  getBangumiAuthUrl,
  getBangumiConfig,
  logoutBangumiAuth,
  setBangumiConfig,
} from '../../../apis'
import { useEffect, useRef, useState } from 'react'
import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
  QuestionCircleOutlined,
  KeyOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

export const Bangumi = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(true)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const [authInfo, setAuthInfo] = useState({})
  const [authMode, setAuthMode] = useState('token') // 'token' or 'oauth'
  const oauthPopup = useRef()

  const modalApi = useModal()
  const messageApi = useMessage()

  const getConfig = async () => {
    const res = await getBangumiConfig()
    return res.data || {}
  }
  const getAuth = async () => {
    const res = await getBangumiAuth()
    return res.data || {}
  }

  const getInfo = async () => {
    try {
      setLoading(true)
      const [config, auth] = await Promise.all([getConfig(), getAuth()])
      form.setFieldsValue(config)
      // 新增：直接使用后端返回的 authMode 字段来设置认证模式
      if (config.authMode) {
        setAuthMode(config.authMode)
      } else {
        // Fallback for older backend versions or if field is missing
        setAuthMode(config.bangumiToken ? 'token' : 'oauth')
      }
      setAuthInfo(auth)
      setLoading(false)
    } catch (error) {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      // 根据当前模式，清空另一种模式的配置
      if (authMode === 'token') {
        values.bangumiClientId = ''
        values.bangumiClientSecret = ''
      } else {
        values.bangumiToken = ''
      }
      await setBangumiConfig(values)
      setIsSaveLoading(false)
      messageApi.success('保存成功')
    } catch (error) {
      messageApi.error('保存失败')
    } finally {
      setIsSaveLoading(false)
    }
  }

  const handleLogout = () => {
    modalApi.confirm({
      title: '注销',
      zIndex: 1002,
      content: <div>确定要注销Bangumi授权吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await logoutBangumiAuth()
          getInfo()
        } catch (error) {
          alert(`注销失败: ${error.message}`)
        }
      },
    })
  }

  const handleLogin = async () => {
    try {
      if (oauthPopup.current && !oauthPopup.current?.closed) {
        oauthPopup.current?.focus?.()
      } else {
        const res = await getBangumiAuthUrl()
        const width = 600,
          height = 700
        const left = window.screen.width / 2 - width / 2
        const top = window.screen.height / 2 - height / 2
        oauthPopup.current = window.open(
          res.data.url,
          'BangumiAuth',
          `width=${width},height=${height},top=${top},left=${left}`
        )
      }
    } catch (error) {
      alert(`获取授权链接失败: ${error.message}`)
    }
  }

  useEffect(() => {
    getInfo()
    const handleMessage = event => {
      if (event.data === 'BANGUMI-OAUTH-COMPLETE') {
        if (oauthPopup.current) oauthPopup.current?.close?.()
        getInfo()
      }
    }
    window.addEventListener('message', handleMessage)
    return () => {
      // 正确地移除之前添加的同一个函数引用
      window.removeEventListener('message', handleMessage)
    }
  }, [])

  return (
    <div className="my-6">
      <Card loading={loading} title="Bangumi API 配置">
        <div className="mb-4">
          选择一种认证方式。优先推荐使用 Access Token，因为它更简单且不易过期。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item label="认证方式" className="mb-6">
            <Switch
              checkedChildren="OAuth 授权"
              unCheckedChildren="Access Token"
              checked={authMode === 'oauth'}
              onChange={checked => setAuthMode(checked ? 'oauth' : 'token')}
            />
          </Form.Item>

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
                      <QuestionCircleOutlined className="cursor-pointer text-gray-400" />
                    </a>
                  </span>
                }
                rules={[{ required: true, message: '请输入App ID' }]}
                className="mb-4"
              >
                <Input placeholder="请输入App ID" />
              </Form.Item>

              <Form.Item
                name="bangumiClientSecret"
                label="App Secret"
                rules={[{ required: true, message: '请输入App Secret' }]}
                className="mb-6"
              >
                <Input.Password
                  prefix={<LockOutlined className="text-gray-400" />}
                  placeholder="请输入App Secret"
                  visibilityToggle={{
                    visible: showPassword,
                    onVisibleChange: setShowPassword,
                  }}
                  iconRender={visible =>
                    visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
                  }
                />
              </Form.Item>
            </>
          )}

          {authMode === 'token' && (
            <Form.Item
              name="bangumiToken"
              label={
                <span>
                  Access Token
                  <a
                    href="https://next.bgm.tv/demo/access-token"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-1"
                  >
                    <QuestionCircleOutlined className="cursor-pointer text-gray-400" />
                  </a>
                </span>
              }
              className="mb-6"
            >
              <Input.Password
                prefix={<KeyOutlined className="text-gray-400" />}
                placeholder="请输入 Access Token"
                visibilityToggle={{
                  visible: showToken,
                  onVisibleChange: setShowToken,
                }}
                iconRender={visible =>
                  visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
                }
              />
            </Form.Item>
          )}
          <Form.Item>
            <div className="flex justify-end">
              <Button type="primary" htmlType="submit" loading={isSaveLoading}>
                保存修改
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Card>
      <Card
        loading={loading}
        title="Bangumi 授权状态"
        className="mt-6"
        style={{ display: authMode === 'oauth' ? 'block' : 'none' }}
      >
        {authInfo.isAuthenticated ? (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Avatar size={64} src={authInfo.avatarUrl} />
              <div>
                <div className="font-bold text-lg">{authInfo.nickname}</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">
                  用户ID: {authInfo.bangumiUserId}
                </div>
                <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                  授权于:{' '}
                  {dayjs(authInfo.authorizedAt).format('YYYY-MM-DD HH:mm')}
                </div>
                <div className="text-xs text-gray-400 dark:text-gray-500">
                  过期于: {dayjs(authInfo.expiresAt).format('YYYY-MM-DD HH:mm')}
                </div>
              </div>
            </div>
            <Button type="primary" danger onClick={handleLogout}>
              注销
            </Button>
          </div>
        ) : (
          <div className="text-center py-4">
            <div className="mb-4">当前未授权。授权后可使用更多功能。</div>
            <Button type="primary" onClick={handleLogin}>
              通过 Bangumi 登录
            </Button>
          </div>
        )}
      </Card>
    </div>
  )
}

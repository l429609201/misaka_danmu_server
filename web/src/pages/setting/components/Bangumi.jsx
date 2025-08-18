import { Button, Card, Form, Input, message, Modal } from 'antd'
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
} from '@ant-design/icons'
import dayjs from 'dayjs'

export const Bangumi = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(true)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [configInfo, setConfigInfo] = useState({})
  const [authInfo, setAuthInfo] = useState({})
  const oauthPopup = useRef()

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
      setConfigInfo(config)
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
      await setBangumiConfig(values)
      setIsSaveLoading(false)
      message.success('保存成功')
    } catch (error) {
      message.error('保存失败')
    } finally {
      setIsSaveLoading(false)
    }
  }

  const handleLogout = () => {
    Modal.confirm({
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
    window.addEventListener('message', event => {
      if (event.data === 'BANGUMI-OAUTH-COMPLETE') {
        if (oauthPopup.current) oauthPopup.current?.close?.()
        getInfo()
      }
    })
    return () => {
      window.removeEventListener('message', event => {
        if (event.data === 'BANGUMI-OAUTH-COMPLETE') {
          if (oauthPopup.current) oauthPopup.current?.close?.()
          getInfo()
        }
      })
    }
  }, [])

  return (
    <div className="my-6">
      <Card loading={loading} title="Bangumi API 配置">
        <div className="mb-4">
          请从{' '}
          <a
            href="https://bgm.tv/dev/app"
            target="_blank"
            rel="noopener noreferrer"
          >
            Bangumi开发者中心
          </a>{' '}
          创建应用以获取您自己的 App ID 和 App Secret。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
          initialValues={{
            bangumiClientId: configInfo?.bangumiClientId,
            bangumiClientSecret: configInfo?.bangumiClientSecret,
          }}
        >
          {/* 用户名输入 */}
          <Form.Item
            name="bangumiClientId"
            label="App ID"
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

          <Form.Item>
            <div className="flex justify-end">
              <Button type="primary" htmlType="submit" loading={isSaveLoading}>
                保存修改
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Card>
      <Card loading={loading} title="Bangumi 授权">
        {authInfo.isAuthenticated ? (
          <div>
            <p className="my-2">
              状态: 已作为 <strong>{authInfo.nickname}</strong> 授权
            </p>
            <p className="my-2">
              用户ID: <span>{authInfo.bangumiUserId}</span>
            </p>
            <p className="my-2">
              授权时间:{' '}
              <span>
                {dayjs(authInfo.authorizedAt).format('YYYY-MM-DD HH:mm:ss')}
              </span>
            </p>
            <p className="my-2">
              过期时间:{' '}
              <span>
                {dayjs(authInfo.expiresAt).format('YYYY-MM-DD HH:mm:ss')}
              </span>
            </p>
            <div className="flex justify-end mt-4">
              <Button type="primary" danger onClick={handleLogout}>
                注销
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <div className="mb-4">当前未授权。授权后可使用更多功能。</div>
            <div className="flex justify-end">
              <Button type="primary" onClick={handleLogin}>
                通过 Bangumi 登录
              </Button>
            </div>
          </div>
        )}
        <div></div>
      </Card>
    </div>
  )
}

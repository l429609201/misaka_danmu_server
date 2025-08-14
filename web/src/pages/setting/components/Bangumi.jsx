import { Button, Card, Form, Input, message } from 'antd'
import {
  getBangumiAuth,
  getBangumiConfig,
  setBangumiConfig,
} from '../../../apis'
import { useEffect, useState } from 'react'
import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
} from '@ant-design/icons'

export const Bangumi = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [configInfo, setConfigInfo] = useState({})
  const [authInfo, setAuthInfo] = useState({})

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
      setTimeout(() => {
        form.setFieldsValue({
          bangumi_client_id: config?.bangumi_client_id,
          bangumi_client_secret: config?.bangumi_client_secret,
        })
      }, 50)
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
    }
  }

  useEffect(() => {
    getInfo()
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
            bangumi_client_id: configInfo?.bangumi_client_id,
            bangumi_client_secret: configInfo?.bangumi_client_secret,
          }}
        >
          {/* 用户名输入 */}
          <Form.Item
            name="bangumi_client_id"
            label="App ID"
            rules={[{ required: true, message: '请输入App ID' }]}
            className="mb-4"
          >
            <Input placeholder="请输入App ID" />
          </Form.Item>

          <Form.Item
            name="bangumi_client_secret"
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
            <Button type="primary" htmlType="submit" loading={isSaveLoading}>
              保存修改
            </Button>
          </Form.Item>
        </Form>
      </Card>
      <Card loading={loading} title="Bangumi 授权"></Card>
    </div>
  )
}

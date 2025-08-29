import { Button, Card, Form, Input, message } from 'antd'
import { useEffect, useState } from 'react'
import { getTmdbConfig, setTmdbConfig } from '../../../apis'
import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'

export const TMDB = () => {
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [showPassword, setShowPassword] = useState(false)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const messageApi = useMessage()

  useEffect(() => {
    setLoading(true)
    getTmdbConfig()
      .then(res => {
        form.setFieldsValue(res.data ?? {})
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      await setTmdbConfig(values)
      setIsSaveLoading(false)
      messageApi.success('保存成功')
    } catch (error) {
      messageApi.error('保存失败')
    } finally {
      setIsSaveLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="TMDB API 配置">
        <div className="mb-4">
          请从{' '}
          <a
            href="https://www.themoviedb.org/settings/api"
            target="_blank"
            rel="noopener noreferrer"
          >
            TMDB官网
          </a>{' '}
          获取您的 API Key (v3 auth)。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item
            name="tmdbApiKey"
            label="API Key (v3)"
            rules={[{ required: true, message: 'API Key (v3)' }]}
            className="mb-6"
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入API Key (v3)"
              visibilityToggle={{
                visible: showPassword,
                onVisibleChange: setShowPassword,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
          <Form.Item name="tmdbApiBaseUrl" label="API 域名" className="mb-4">
            <Input placeholder="请输入API 域名" />
          </Form.Item>

          <Form.Item name="tmdbImageBaseUrl" label="图片域名" className="mb-4">
            <Input placeholder="请输入图片域名" />
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
    </div>
  )
}

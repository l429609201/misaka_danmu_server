import { Button, Card, Form, Input, message } from 'antd'
import { useEffect, useState } from 'react'
import { getTvdbConfig, setTvdbConfig } from '../../../apis'
import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'

export const TVDB = () => {
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [showPassword, setShowPassword] = useState(false)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const messageApi = useMessage()

  useEffect(() => {
    setLoading(true)
    getTvdbConfig()
      .then(res => {
        form.setFieldsValue({ tvdbApiKey: res.data?.value ?? '' })
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      await setTvdbConfig({
        value: values.tvdbApiKey,
      })
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
      <Card loading={loading} title="TVDB API 配置">
        <div className="mb-4">
          此项目需要 TheTVDB V4 API Key。您可以从{' '}
          <a
            href="https://thetvdb.com/subscribe"
            target="_blank"
            rel="noopener noreferrer"
          >
            TheTVDB官网
          </a>{' '}
          获取您自己的 Key。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item name="tvdbApiKey" label="API Key" className="mb-6">
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

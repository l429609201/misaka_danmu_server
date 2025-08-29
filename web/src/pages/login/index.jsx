import { useState } from 'react'
import { Form, Input, Button, Card, message } from 'antd'
import {
  UserOutlined,
  LockOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons'
import { login } from '../../apis'
import { useNavigate } from 'react-router-dom'
import Cookies from 'js-cookie'
import { useMessage } from '../../MessageContext'

export const Login = () => {
  const [form] = Form.useForm()
  const [isLoading, setIsLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const navigate = useNavigate()
  const messageApi = useMessage()

  // 处理登录逻辑
  const handleLogin = async values => {
    try {
      setIsLoading(true)
      const res = await login(values)

      if (res.data.accessToken) {
        Cookies.set('token', res.data.accessToken, { expires: 7 })
        messageApi.success('登录成功！')
        navigate('/')
      } else {
        messageApi.error('登录失败，请检查用户名或密码')
      }
    } catch (error) {
      console.error('登录失败:', error)
      messageApi.error('登录失败，请检查用户名或密码')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="my-6 flex items-center justify-center">
      {/* 登录卡片容器 */}
      <Card className="w-full max-w-md rounded-xl shadow-lg overflow-hidden mx-auto">
        {/* 登录标题区域 */}
        <div className="text-center mb-8 pt-4">
          <h2 className="text-[clamp(1.5rem,3vw,2rem)] font-bold text-base-text">
            账户登录
          </h2>
          <p className="text-base-text mt-2">请输入您的账号信息以继续</p>
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
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
            className="mb-4"
          >
            <Input
              prefix={<UserOutlined className="text-gray-400" />}
              placeholder="请输入用户名"
            />
          </Form.Item>

          {/* 密码输入 */}
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
            className="mb-6"
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入密码"
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
          <Form.Item>
            <Button block type="primary" htmlType="submit" loading={isLoading}>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

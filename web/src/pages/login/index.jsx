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
import { setStorage } from '../../utils'
import { DANMU_API_TOKEN_KEY } from '../../configs'

export const Login = () => {
  const [form] = Form.useForm()
  const [isLoading, setIsLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const navigate = useNavigate()

  // 处理登录逻辑
  const handleLogin = async values => {
    try {
      setIsLoading(true)
      // 模拟登录请求
      const res = await login(values)

      // console.log('登录信息:', res)
      navigate('/')
      setStorage(DANMU_API_TOKEN_KEY, res.data.access_token)
      message.success('登录成功！')
    } catch (error) {
      console.error('登录失败:', error)
      message.error('登录失败，请检查用户名或密码')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div>
      {/* 登录卡片容器 */}
      <Card className="w-full max-w-md rounded-xl shadow-lg overflow-hidden transform transition-all duration-300 hover:shadow-xl">
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
              className="h-11 rounded-lg border-gray-300 focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
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
              className="h-11 rounded-lg border-gray-300 focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
            />
          </Form.Item>

          {/* 登录按钮 */}
          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={isLoading}
              className="w-full h-11 text-base font-medium rounded-lg bg-primary hover:bg-primary/90 transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98]"
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

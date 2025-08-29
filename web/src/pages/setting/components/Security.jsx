import {
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { Button, Card, Form, Input, message } from 'antd'
import { useState } from 'react'
import { changePassword } from '../../../apis'
import { useMessage } from '../../../MessageContext'

export const Security = () => {
  const [form] = Form.useForm()
  const [showPassword1, setShowPassword1] = useState(false)
  const [showPassword2, setShowPassword2] = useState(false)
  const [showPassword3, setShowPassword3] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const messageApi = useMessage()

  const onSave = async () => {
    try {
      setIsLoading(true)
      const values = await form.validateFields()
      await changePassword(values)
      form.resetFields()
      messageApi.success('修改成功')
    } catch (error) {
      messageApi.error('修改失败')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card title="修改密码">
        <div className="mb-4">
          如果您是使用初始随机密码登录的，建议您在此修改为自己的密码。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={onSave}
          className="px-6 pb-6"
        >
          {/* 密码输入 */}
          <Form.Item
            name="oldPassword"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入当前密码"
              visibilityToggle={{
                visible: showPassword1,
                onVisibleChange: setShowPassword1,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[{ required: true, message: '请输入新密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入新密码"
              visibilityToggle={{
                visible: showPassword2,
                onVisibleChange: setShowPassword2,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
          <Form.Item
            name="checkPassword"
            label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              {
                required: true,
                message: '请输入新密码',
              },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('新密码不匹配'))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入新密码"
              visibilityToggle={{
                visible: showPassword3,
                onVisibleChange: setShowPassword3,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>

          <Form.Item>
            <div className="flex justify-end">
              <Button type="primary" htmlType="submit" loading={isLoading}>
                确认修改
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

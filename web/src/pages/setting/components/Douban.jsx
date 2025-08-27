import { Button, Card, Form, Input, message } from 'antd'
import { useEffect, useState } from 'react'
import { getDoubanConfig, setDoubanConfig } from '../../../apis'

export const Douban = () => {
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [isSaveLoading, setIsSaveLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    getDoubanConfig()
      .then(res => {
        form.setFieldsValue({ cookie: res.data?.value ?? '' })
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      await setDoubanConfig({
        value: values.cookie,
      })
      setIsSaveLoading(false)
      message.success('保存成功')
    } catch (error) {
      message.error('保存失败')
    } finally {
      setIsSaveLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="豆瓣 Cookie 配置">
        <div className="mb-4">
          豆瓣搜索通常无需配置即可使用。如果遇到搜索失败或403错误，可以尝试在此处配置您的豆瓣账户Cookie以提高请求成功率。请从浏览器开发者工具中获取。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item
            name="cookie"
            label="Cookie"
            rules={[{ required: true, message: '请输入豆瓣 Cookie' }]}
            className="mb-6"
          >
            <Input.TextArea rows={8} />
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

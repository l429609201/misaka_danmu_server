import {
  Button,
  Card,
  Col,
  Form,
  Input,
  message,
  Row,
  Tooltip,
  Select,
  Switch,
} from 'antd'
import { useEffect, useState } from 'react'
import { getProxyConfig, setProxyConfig } from '../../../apis'
import { useMessage } from '../../../MessageContext'

export const Proxy = () => {
  const [proxyEnabled, setProxyEnabled] = useState(false)
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const messageApi = useMessage()

  useEffect(() => {
    getProxyConfig()
      .then(res => {
        setProxyEnabled(res.data?.proxyEnabled ?? false)
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
      setProxyEnabled(values.proxyEnabled)
      await setProxyConfig(values)
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
      <Card loading={loading} title="代理配置">
        <div className="mb-4">
          配置一个全局代理，可用于访问受限的网络资源。支持 http, https, socks5
          协议。
        </div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item name="proxyProtocol" label="协议" className="mb-6">
            <Select
              options={[
                {
                  value: 'http',
                  label: 'http',
                },
                {
                  value: 'https',
                  label: 'https',
                },
                {
                  value: 'socks5',
                  label: 'socks5',
                },
              ]}
            />
          </Form.Item>
          <Row gutter={[12, 12]}>
            <Col md={12} xs={24}>
              <Form.Item name="proxyHost" label="主机" className="mb-4">
                <Input placeholder="例如：127.0.0.1" />
              </Form.Item>
            </Col>
            <Col md={12} xs={24}>
              <Form.Item name="proxyPort" label="端口" className="mb-4">
                <Input placeholder="例如：7890" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={[12, 12]}>
            <Col md={12} xs={24}>
              <Form.Item
                name="proxyUsername"
                label="用户名(可选)"
                className="mb-4"
              >
                <Input />
              </Form.Item>
            </Col>
            <Col md={12} xs={24}>
              <Form.Item
                name="proxyPassword"
                label="密码(可选)"
                className="mb-4"
              >
                <Input />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={[12, 12]}>
            <Col md={12} xs={24}>
              <Form.Item
                name="proxyEnabled"
                label="开启全局代理"
                className="mb-4"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
            </Col>
            <Col md={12} xs={24}>
              <Form.Item
                label="跳过SSL证书验证"
                name="proxySslVerify"
                valuePropName="checked"
                tooltip="当您的HTTPS代理使用自签名证书时，请开启此项以避免SSL验证错误。"
                className="mb-4"
              >
                <Switch disabled={!proxyEnabled} />
              </Form.Item>
            </Col>
          </Row>

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

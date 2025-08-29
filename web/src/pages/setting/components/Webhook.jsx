import { Button, Card, Input, message, Space } from 'antd'
import { useEffect, useState } from 'react'
import {
  getWebhookApikey,
  getWebhookDomain,
  getWebhookServices,
  refreshWebhookApikey,
  setWebhookDomain,
} from '../../../apis'
import { CopyOutlined, ReloadOutlined } from '@ant-design/icons'
import copy from 'copy-to-clipboard'
import { useMessage } from '../../../MessageContext'

export const Webhook = () => {
  const [isLoading, setLoading] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [domain, setDomain] = useState('')
  const [services, setServices] = useState([])
  const messageApi = useMessage()

  const getApiKey = async () => {
    const res = await getWebhookApikey()
    return res.data?.value || ''
  }
  const getDomain = async () => {
    const res = await getWebhookDomain()
    return res.data?.value || ''
  }
  const getServices = async () => {
    const res = await getWebhookServices()
    return res.data
  }

  const getInfo = async () => {
    try {
      setLoading(true)
      const [apiKey, domain, services] = await Promise.all([
        getApiKey(),
        getDomain(),
        getServices(),
      ])
      setApiKey(apiKey)
      setDomain(domain)
      setServices(services)
      setLoading(false)
    } catch (error) {
      setLoading(false)
    }
  }

  const onRefresh = async () => {
    const res = await refreshWebhookApikey()
    setApiKey(res.data.value)
  }

  const onSaveDoamin = async () => {
    try {
      await setWebhookDomain({ value: domain })
      messageApi.success('保存成功')
    } catch (error) {
      messageApi.error('保存失败')
    }
  }

  useEffect(() => {
    getInfo()
  }, [])

  return (
    <div className="my-6">
      <Card loading={isLoading} title="Webhook 配置">
        <div className="mb-3">
          Webhook
          用于接收来自外部服务的通知，以实现自动化导入。请将下方对应服务的 URL
          填入其 Webhook 通知设置中。
        </div>
        <div className="mb-4">{`URL 格式为：http(s)://域名(ip):端口(port)/api/webhook/{服务名}?api_key={你的API Key}`}</div>
        <div className="flex items-center justify-start gap-3 mb-4">
          <div className="shrink-0 w-auto md:w-[120px]">API Key:</div>
          <div className="w-full">
            <Space.Compact style={{ width: '100%' }}>
              <Input block readOnly value={apiKey} />
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                onClick={onRefresh}
              />
            </Space.Compact>
          </div>
        </div>
        <div className="flex items-center justify-start gap-3 mb-4 flex-wrap md:flex-nowrap">
          <div className="shrink-0 w-[120px]">自定义域名(可选):</div>
          <div className="w-full">
            <Input
              block
              value={domain}
              onChange={e => setDomain(e.target.value)}
            />
          </div>
          <Button
            type="primary"
            className="w-full md:w-[120px]"
            onClick={onSaveDoamin}
          >
            保存域名
          </Button>
        </div>
        {services.map(it => (
          <div
            key={it}
            className="flex items-center justify-start gap-3 mb-4 flex-wrap md:flex-nowrap"
          >
            <div className="shrink-0 w-auto md:w-[120px]">
              {it} Webhook地址:
            </div>
            <div className="w-full">
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  block
                  readOnly
                  value={`${domain || window.location.origin}/api/webhook/${it}?api_key=${apiKey}`}
                />
                <Button
                  type="primary"
                  icon={<CopyOutlined />}
                  onClick={() => {
                    copy(
                      `${domain || window.location.origin}/api/webhook/${it}?api_key=${apiKey}`
                    )
                  }}
                />
              </Space.Compact>
            </div>
          </div>
        ))}
      </Card>
    </div>
  )
}

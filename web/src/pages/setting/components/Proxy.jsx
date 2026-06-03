import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Select,
  Switch,
  Spin,
  Tag,
  Divider,
  Space,
} from 'antd'
import { useEffect, useState } from 'react'
import { getProxyConfig, setProxyConfig, testProxy } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

export const Proxy = () => {
  const { t } = useTranslation()
  const [proxyMode, setProxyMode] = useState('none')
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [isTestLoading, setIsTestLoading] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const messageApi = useMessage()

  useEffect(() => {
    getProxyConfig()
      .then(res => {
        const mode = res.data?.proxyMode ?? 'none'
        setProxyMode(mode)
        form.setFieldsValue({
          ...res.data,
          proxyMode: mode,
          proxySslVerify: res.data?.proxySslVerify ?? true,
        })
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      setProxyMode(values.proxyMode)
      await setProxyConfig(values)
      setIsSaveLoading(false)
      messageApi.success(t('proxy.saveSuccess'))
    } catch (error) {
      messageApi.error(t('proxy.saveFailed'))
    } finally {
      setIsSaveLoading(false)
    }
  }

  const handleTest = async () => {
    try {
      setIsTestLoading(true)
      setTestResult(null)
      const values = await form.validateFields()

      // 构建测试请求参数
      let proxyUrl = ''
      if (
        values.proxyMode === 'http_socks' &&
        values.proxyHost &&
        values.proxyPort &&
        values.proxyProtocol
      ) {
        let userinfo = ''
        if (values.proxyUsername) {
          userinfo = encodeURIComponent(values.proxyUsername)
          if (values.proxyPassword) {
            userinfo += ':' + encodeURIComponent(values.proxyPassword)
          }
          userinfo += '@'
        }
        proxyUrl = `${values.proxyProtocol}://${userinfo}${values.proxyHost}:${values.proxyPort}`
      }

      const res = await testProxy({
        proxy_mode: values.proxyMode,
        proxy_url: proxyUrl,
        accelerate_proxy_url: values.accelerateProxyUrl || ''
      })
      setTestResult(res.data)
    } catch (error) {
      messageApi.error(t('proxy.testRequestFailed'))
    } finally {
      setIsTestLoading(false)
    }
  }

  const ResultTag = ({ result }) => {
    const isSuccess = result.status === 'success'
    const color = isSuccess ? 'green' : 'red'
    const text = isSuccess
      ? t('proxy.testSuccess', { latency: result.latency.toFixed(0) })
      : t('proxy.testFailed', { error: result.error })
    return <Tag color={color}>{text}</Tag>
  }

  return (
    <div className="my-6">
      <Card loading={loading} title={t('proxy.title')}>
        <div className="mb-4">
          {t('proxy.desc')}
        </div>

        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item
            name="proxyMode"
            label={t('proxy.proxyMode')}
            className="mb-6"
          >
            <Select
              onChange={value => setProxyMode(value)}
              options={[
                { value: 'none', label: t('proxy.noProxy') },
                { value: 'http_socks', label: t('proxy.httpSocks') },
                { value: 'accelerate', label: t('proxy.accelerate') },
              ]}
            />
          </Form.Item>

          {/* HTTP/SOCKS 代理配置 */}
          {proxyMode === 'http_socks' && (
            <>
              <Form.Item name="proxyProtocol" label={t('proxy.protocol')} className="mb-6">
                <Select
                  options={[
                    { value: 'http', label: 'http' },
                    { value: 'https', label: 'https' },
                    { value: 'socks5', label: 'socks5' },
                  ]}
                />
              </Form.Item>
              <Row gutter={[12, 12]}>
                <Col md={12} xs={24}>
                  <Form.Item name="proxyHost" label={t('proxy.host')} className="mb-4">
                    <Input placeholder={t('proxy.hostPlaceholder')} />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item name="proxyPort" label={t('proxy.port')} className="mb-4">
                    <Input placeholder={t('proxy.portPlaceholder')} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={[12, 12]}>
                <Col md={12} xs={24}>
                  <Form.Item
                    name="proxyUsername"
                    label={t('proxy.username')}
                    className="mb-4"
                  >
                    <Input />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item
                    name="proxyPassword"
                    label={t('proxy.password')}
                    className="mb-4"
                  >
                    <Input />
                  </Form.Item>
                </Col>
                <Col md={12} xs={24}>
                  <Form.Item
                    label={t('proxy.skipSslVerify')}
                    name="proxySslVerify"
                    valuePropName="checked"
                    tooltip={t('proxy.skipSslVerifyTip')}
                    className="mb-4"
                  >
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
            </>
          )}

          {/* 加速代理配置 */}
          {proxyMode === 'accelerate' && (
            <Form.Item
              name="accelerateProxyUrl"
              label={t('proxy.accelerateUrl')}
              className="mb-6"
            >
              <Input placeholder={t('proxy.accelerateUrlPlaceholder')} />
            </Form.Item>
          )}

          <Form.Item>
            <div className="flex justify-end">
              <Space>
                <Button onClick={handleTest} loading={isTestLoading}>
                  {t('proxy.testConnection')}
                </Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={isSaveLoading}
                >
                  {t('proxy.saveChanges')}
                </Button>
              </Space>
            </div>
          </Form.Item>
        </Form>
        {isTestLoading && (
          <div className="text-center">
            <Spin />
            <p>{t('proxy.testing')}</p>
          </div>
        )}
        {testResult && (
          <div>
            <Divider>{t('proxy.testResult')}</Divider>
            <div className="flex flex-col gap-2">
              {testResult.proxy_connectivity &&
                testResult.proxy_connectivity.status !== 'skipped' && (
                  <div className="flex justify-between">
                    <span>{t('proxy.proxyConnectivity')}</span>
                    <ResultTag result={testResult.proxy_connectivity} />
                  </div>
                )}
              {Object.entries(testResult.target_sites).map(([site, result]) => (
                <div key={site} className="flex justify-between">
                  <span>
                    {site.replace('https://', '').replace('http://', '')}:
                  </span>
                  <ResultTag result={result} />
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

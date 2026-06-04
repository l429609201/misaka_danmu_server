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

  const getLatencyColor = (ms) => {
    if (ms <= 300) return { bar: 'bg-emerald-400/70', text: 'text-emerald-400', dot: 'bg-emerald-400' }
    if (ms <= 1000) return { bar: 'bg-orange-400/70', text: 'text-orange-400', dot: 'bg-orange-400' }
    return { bar: 'bg-red-400/70', text: 'text-red-400', dot: 'bg-red-400' }
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
              <Button
                type="primary"
                htmlType="submit"
                loading={isSaveLoading}
              >
                {t('proxy.saveChanges')}
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Card>

      {/* 测速结果卡片 - 常驻显示 */}
      <Card
        className="mt-4"
        title={
          <div className="flex items-center gap-2">
            <span>{t('proxy.connectivityCheck')}</span>
            {testResult && (() => {
              const entries = Object.entries(testResult.target_sites)
              const successCount = entries.filter(([, r]) => r.status === 'success').length
              const failCount = entries.length - successCount
              return (
                <div className="flex items-center gap-2 ml-2">
                  <span className="text-xs font-semibold px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-500">✓ {successCount}</span>
                  {failCount > 0 && <span className="text-xs font-semibold px-2 py-0.5 rounded-md bg-red-500/10 text-red-500">✗ {failCount}</span>}
                </div>
              )
            })()}
          </div>
        }
        extra={
          <Button onClick={handleTest} loading={isTestLoading} size="small">
            {t('proxy.testConnection')}
          </Button>
        }
      >
          {!isTestLoading && !testResult && (
            <div className="flex items-center justify-center py-10 text-sm text-gray-400">
              {t('proxy.testHint') || '点击右上角「测试连接」开始测速'}
            </div>
          )}
          {isTestLoading && (
            <div className="flex items-center justify-center gap-3 py-8">
              <Spin />
              <span className="text-sm text-gray-500">{t('proxy.testing')}</span>
            </div>
          )}
          {testResult && (() => {
            const entries = Object.entries(testResult.target_sites)
            const successEntries = entries.filter(([, r]) => r.status === 'success')
            const maxLatency = Math.max(...successEntries.map(([, r]) => r.latency), 500)
            const successCount = successEntries.length
            const totalCount = entries.length
            const pct = totalCount > 0 ? Math.round((successCount / totalCount) * 100) : 0
            const avgLatency = successEntries.length > 0 ? Math.round(successEntries.reduce((s, [, r]) => s + r.latency, 0) / successEntries.length) : 0

            return (
              <div>
                {/* 成功率进度条 */}
                <div className="flex items-center gap-3 mb-4">
                  <div className="flex-1 h-2 rounded-full bg-gray-100 dark:bg-white/5 overflow-hidden">
                    <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-all duration-500" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-xs font-semibold text-emerald-500 whitespace-nowrap">{pct}% ({successCount}/{totalCount})</span>
                  {avgLatency > 0 && <span className="text-xs text-gray-400 whitespace-nowrap">⏱ {avgLatency}ms</span>}
                </div>

                {/* 代理连通性 */}
                {testResult.proxy_connectivity && testResult.proxy_connectivity.status !== 'skipped' && (
                  <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-gray-50 dark:bg-white/[0.03] mb-2">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${testResult.proxy_connectivity.status === 'success' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                    <span className="text-xs text-gray-600 dark:text-gray-300 w-44 truncate font-medium">{t('proxy.proxyConnectivity')}</span>
                    <div className="flex-1" />
                    {testResult.proxy_connectivity.status === 'success'
                      ? <span className={`text-xs font-bold ${getLatencyColor(testResult.proxy_connectivity.latency).text} w-16 text-right`}>{testResult.proxy_connectivity.latency.toFixed(0)}ms</span>
                      : <span className="text-[10px] font-semibold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">{testResult.proxy_connectivity.error}</span>
                    }
                  </div>
                )}

                {/* 站点列表 */}
                <div className="space-y-1">
                  {entries.map(([site, result], idx) => {
                    const domain = site.replace('https://', '').replace('http://', '')
                    const isSuccess = result.status === 'success'
                    const colors = isSuccess ? getLatencyColor(result.latency) : null
                    const barWidth = isSuccess ? Math.min(100, Math.round((result.latency / maxLatency) * 100)) : 100
                    return (
                      <div key={site} className={`flex items-center gap-3 px-3 py-2 rounded-lg transition ${idx % 2 === 0 ? 'bg-gray-50/60 dark:bg-white/[0.02]' : ''} hover:bg-gray-100/70 dark:hover:bg-white/[0.04]`}>
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isSuccess ? colors.dot : 'bg-red-400'}`} />
                        <span className={`text-xs w-44 truncate ${isSuccess ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400'}`}>{domain}</span>
                        <div className="flex-1 h-1.5 rounded-full bg-gray-100 dark:bg-white/5 overflow-hidden">
                          <div className={`h-full rounded-full transition-all duration-500 ${isSuccess ? colors.bar : 'bg-red-400/50'}`} style={{ width: `${barWidth}%` }} />
                        </div>
                        {isSuccess
                          ? <span className={`text-xs font-bold ${colors.text} w-16 text-right`}>{result.latency.toFixed(0)}ms</span>
                          : <span className="text-[10px] font-semibold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded w-16 text-center truncate" title={result.error}>{result.error?.length > 8 ? result.error.slice(0, 8) + '…' : result.error}</span>
                        }
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })()}
        </Card>
    </div>
  )
}

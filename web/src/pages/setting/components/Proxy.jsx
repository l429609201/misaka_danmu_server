import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Popover,
  Row,
  Select,
  Switch,
  Spin,
  Tag,
} from 'antd'
import { useEffect, useState } from 'react'
import { getProxyConfig, setProxyConfig, testProxy, testSingleTarget } from '../../../apis'
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
  // 单域名测速 / DNS 解析
  const [singleUrl, setSingleUrl] = useState('')
  const [singleTesting, setSingleTesting] = useState(false)
  const [singleResult, setSingleResult] = useState(null)
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

  // 单独测试某个域名的速度 / DNS 解析
  const handleTestSingle = async () => {
    const url = singleUrl.trim()
    if (!url) {
      messageApi.warning(t('proxy.singleUrlRequired'))
      return
    }
    try {
      setSingleTesting(true)
      setSingleResult(null)
      const values = form.getFieldsValue()
      // 构建代理 URL（与 handleTest 一致）
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
      const res = await testSingleTarget({
        url,
        proxy_mode: values.proxyMode || 'none',
        proxy_url: proxyUrl,
        accelerate_proxy_url: values.accelerateProxyUrl || '',
        check_dns: true,
        check_http: true,
      })
      setSingleResult(res.data)
    } catch (error) {
      messageApi.error(error?.detail || error?.message || t('proxy.testRequestFailed'))
    } finally {
      setSingleTesting(false)
    }
  }

  const getLatencyColor = (ms) => {
    if (ms <= 300) return { bar: 'bg-emerald-400/70', text: 'text-emerald-400', dot: 'bg-emerald-400' }
    if (ms <= 1000) return { bar: 'bg-orange-400/70', text: 'text-orange-400', dot: 'bg-orange-400' }
    return { bar: 'bg-red-400/70', text: 'text-red-400', dot: 'bg-red-400' }
  }

  // 单域名测试 Popover 内容（输入框 + DNS/HTTP 结果），由头部「单域名测试」按钮触发弹出
  const singleTestContent = (
    <div style={{ width: 360 }} className="max-w-[80vw]">
      <div className="text-gray-500 dark:text-gray-400 text-xs mb-3">
        {t('proxy.singleTestDesc')}
      </div>
      <Input.Search
        value={singleUrl}
        onChange={e => setSingleUrl(e.target.value)}
        placeholder={t('proxy.singleUrlPlaceholder')}
        enterButton={
          <Button type="primary" loading={singleTesting}>
            {t('proxy.singleTestBtn')}
          </Button>
        }
        onSearch={handleTestSingle}
        allowClear
      />

      {singleTesting && (
        <div className="flex items-center justify-center gap-3 py-6">
          <Spin />
          <span className="text-sm text-gray-500">{t('proxy.testing')}</span>
        </div>
      )}

      {singleResult && !singleTesting && (() => {
        const r = singleResult.result || {}
        const httpOk = r.status === 'success'
        const dnsOk = r.dns_status === 'success'
        return (
          <div className="mt-4 space-y-3">
            {/* 主机信息 */}
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-400">{t('proxy.singleHost')}</span>
              <span className="font-mono dark:text-gray-200">{singleResult.host}</span>
            </div>

            {/* DNS 解析结果 */}
            <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-gray-50 dark:bg-white/[0.03]">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${r.dns_status == null ? 'bg-gray-300' : dnsOk ? 'bg-emerald-400' : 'bg-red-400'}`} />
              <span className="text-xs text-gray-600 dark:text-gray-300 w-28 font-medium">{t('proxy.dnsResolve')}</span>
              <div className="flex-1 flex items-center gap-2 flex-wrap">
                {dnsOk ? (
                  <>
                    <Tag color="green" className="!m-0">{r.resolved_ip}</Tag>
                    <span className="text-xs text-gray-400">{Math.round(r.dns_latency)}ms</span>
                  </>
                ) : r.dns_status === 'failure' ? (
                  <span className="text-[11px] font-semibold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">{r.dns_error || t('proxy.dnsFailed')}</span>
                ) : (
                  <span className="text-xs text-gray-400">{t('proxy.notChecked')}</span>
                )}
              </div>
            </div>

            {/* HTTP 连通性结果 */}
            <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-gray-50 dark:bg-white/[0.03]">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${httpOk ? 'bg-emerald-400' : 'bg-red-400'}`} />
              <span className="text-xs text-gray-600 dark:text-gray-300 w-28 font-medium">{t('proxy.httpConnect')}</span>
              <div className="flex-1 flex items-center justify-end">
                {httpOk
                  ? <span className={`text-xs font-bold ${getLatencyColor(r.latency).text}`}>{Math.round(r.latency)}ms</span>
                  : <span className="text-[11px] font-semibold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">{r.error || t('proxy.connectFailed')}</span>
                }
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )

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
          <div className="flex items-center gap-2">
            {/* 单域名测试：点击弹出 Popover（输入域名/URL 测 DNS+连接），与整体连通性检测并列 */}
            <Popover
              content={singleTestContent}
              title={t('proxy.singleTestTitle')}
              trigger="click"
              placement="bottomRight"
            >
              <Button size="small">{t('proxy.singleTestTitle')}</Button>
            </Popover>
            <Button onClick={handleTest} loading={isTestLoading} size="small">
              {t('proxy.testConnection')}
            </Button>
          </div>
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

                {/* 站点列表（按 domain_map 分组，同源聚合） */}
                {(() => {
                  const domainMap = testResult.domain_map || {}
                  // 按 group 分组
                  const groups = {}
                  entries.forEach(([site, result]) => {
                    const info = domainMap[site]
                    const groupName = info?.group || '其他'
                    if (!groups[groupName]) groups[groupName] = []
                    groups[groupName].push([site, result, info?.source || ''])
                  })
                  // 每个 group 内部按 source 名称排序，同源域名聚在一起
                  Object.values(groups).forEach(items => {
                    items.sort((a, b) => a[2].localeCompare(b[2]))
                  })
                  // 组间排序：弹幕源 > 元数据源 > AI 服务 > 通知服务 > 资源下载 > 图片服务 > 其他
                  const groupOrder = ['弹幕源', '元数据源', 'AI 服务', '通知服务', '资源下载', '图片服务', '其他']
                  const sortedGroupNames = Object.keys(groups).sort((a, b) => {
                    const ia = groupOrder.indexOf(a), ib = groupOrder.indexOf(b)
                    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
                  })

                  return (
                    <div className="space-y-4">
                      {sortedGroupNames.map(groupName => {
                        const items = groups[groupName]
                        // 二级分组：按 source 聚合
                        const subGroups = {}
                        items.forEach(([site, result, sourceName]) => {
                          const key = sourceName || site
                          if (!subGroups[key]) subGroups[key] = []
                          subGroups[key].push([site, result])
                        })
                        const subGroupNames = Object.keys(subGroups).sort()

                        return (
                          <div key={groupName}>
                            <div className="text-[11px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-1.5 px-1">{groupName}</div>
                            <div className="space-y-2">
                              {subGroupNames.map(sourceName => {
                                const domains = subGroups[sourceName]
                                const allSuccess = domains.every(([, r]) => r.status === 'success')
                                const anySuccess = domains.some(([, r]) => r.status === 'success')
                                const sourceColor = allSuccess ? 'text-emerald-500' : anySuccess ? 'text-orange-400' : 'text-red-400'

                                return (
                                  <div key={sourceName} className="rounded-lg bg-gray-50/60 dark:bg-white/[0.02] overflow-hidden">
                                    {/* 源名称子标题 */}
                                    <div className={`flex items-center gap-2 px-3 py-1.5 text-xs font-semibold ${sourceColor}`}>
                                      <span className={`w-1.5 h-1.5 rounded-full ${allSuccess ? 'bg-emerald-400' : anySuccess ? 'bg-orange-400' : 'bg-red-400'}`} />
                                      {sourceName}
                                    </div>
                                    {/* 该源下的域名列表 */}
                                    <div className="space-y-0">
                                      {domains.map(([site, result], idx) => {
                                        const domain = site.replace('https://', '').replace('http://', '')
                                        const isSuccess = result.status === 'success'
                                        const colors = isSuccess ? getLatencyColor(result.latency) : null
                                        const barWidth = isSuccess ? Math.min(100, Math.round((result.latency / maxLatency) * 100)) : 100
                                        return (
                                          <div key={site} className={`flex items-center gap-3 px-3 pl-6 py-1.5 transition hover:bg-gray-100/70 dark:hover:bg-white/[0.04]`}>
                                            <span className={`text-xs flex-1 truncate ${isSuccess ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400'}`}>{domain}</span>
                                            {/* DNS 解析状态：成功显示首个 IP（hover 查看），失败显示红点 */}
                                            {result.dns_status === 'success' ? (
                                              <span
                                                className="text-[10px] font-mono text-sky-500 bg-sky-500/10 px-1.5 py-0.5 rounded flex-shrink-0 max-w-[120px] truncate"
                                                title={`DNS: ${result.resolved_ip} (${Math.round(result.dns_latency)}ms)`}
                                              >
                                                {result.resolved_ip}
                                              </span>
                                            ) : result.dns_status === 'failure' ? (
                                              <span
                                                className="text-[10px] font-semibold text-amber-500 bg-amber-500/10 px-1.5 py-0.5 rounded flex-shrink-0"
                                                title={result.dns_error || 'DNS 解析失败'}
                                              >
                                                DNS✗
                                              </span>
                                            ) : null}
                                            <div className="w-24 h-1.5 rounded-full bg-gray-100 dark:bg-white/5 overflow-hidden flex-shrink-0">
                                              <div className={`h-full rounded-full transition-all duration-500 ${isSuccess ? colors.bar : 'bg-red-400/50'}`} style={{ width: `${barWidth}%` }} />
                                            </div>
                                            {isSuccess
                                              ? <span className={`text-xs font-bold ${colors.text} w-14 text-right`}>{result.latency.toFixed(0)}ms</span>
                                              : <span className="text-[10px] font-semibold text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded w-14 text-center truncate" title={result.error}>{result.error?.length > 8 ? result.error.slice(0, 8) + '…' : result.error}</span>
                                            }
                                          </div>
                                        )
                                      })}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )
                })()}
              </div>
            )
          })()}
        </Card>
    </div>
  )
}

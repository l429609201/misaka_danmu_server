import { Card, Descriptions, Tag, List, Spin, Alert, Button, Space, Statistic, Row, Col, Collapse } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getFullDiagnostics, analyzeLogDiagnostics } from '@/apis'
import { CheckCircleOutlined, WarningOutlined, CloseCircleOutlined, ReloadOutlined, BugOutlined, DesktopOutlined } from '@ant-design/icons'

const severityColor = { ok: 'green', warning: 'orange', error: 'red', info: 'blue' }
const errTypeLabel = {
  proxy_error: '代理异常', timeout: '超时', source_error: '弹幕源异常',
  db_error: '数据库异常', ai_error: 'AI接口异常', auth_error: '认证异常',
  disk_error: '磁盘异常', memory_error: '内存异常',
}

export const DiagnosticsPanel = () => {
  const { t } = useTranslation()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  const reload = () => {
    setLoading(true)
    getFullDiagnostics().then(res => { setData(res?.data); setLoading(false) }).catch(() => setLoading(false))
  }

  useEffect(() => { reload() }, [])

  if (loading) return <Spin className="w-full flex justify-center py-8" />
  if (!data) return <Alert type="warning" message={t('diagnostics.loadFailed')} />

  const env = data.environment || {}

  return (
    <div className="space-y-4">
      <Card title={<><DesktopOutlined className="mr-2" />{t('diagnostics.envTitle')}</>} size="small"
        extra={<Button icon={<ReloadOutlined />} onClick={reload} size="small">{t('diagnostics.refresh')}</Button>}>
        <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" bordered>
          <Descriptions.Item label={t('diagnostics.appVersion')}>{env.appVersion}</Descriptions.Item>
          <Descriptions.Item label="Python">{env.pythonVersion}</Descriptions.Item>
          <Descriptions.Item label={t('diagnostics.platform')}>{env.osName} ({env.architecture})</Descriptions.Item>
          <Descriptions.Item label={t('diagnostics.dbType')}>{env.dbType}</Descriptions.Item>
          <Descriptions.Item label={t('diagnostics.cacheBackend')}>{env.cacheBackend}</Descriptions.Item>
          <Descriptions.Item label={t('diagnostics.timezone')}>{env.timezone}</Descriptions.Item>
          <Descriptions.Item label="Docker">{env.isDocker ? <Tag color="blue">Yes</Tag> : 'No'}</Descriptions.Item>
          <Descriptions.Item label="uvloop">{env.uvloopEnabled ? <Tag color="green">Yes</Tag> : 'No'}</Descriptions.Item>
          <Descriptions.Item label={t('diagnostics.configDir')}>
            {env.configDir} {env.configDirWritable ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag>}
          </Descriptions.Item>
        </Descriptions>
        {data.checks?.length > 0 && (
          <div className="mt-3">
            <Space wrap>
              {data.checks.map((c, i) => (
                <Tag key={i} color={severityColor[c.status]} icon={c.status === 'ok' ? <CheckCircleOutlined /> : <WarningOutlined />}>
                  {c.label}: {c.detail || c.status}
                </Tag>
              ))}
            </Space>
          </div>
        )}
      </Card>

      <Card title={<><BugOutlined className="mr-2" />{t('diagnostics.logTitle')}</>} size="small">
        {data.logDiagnostics?.length > 0 ? (
          <Collapse size="small" items={data.logDiagnostics.map((item, i) => ({
            key: i,
            label: (
              <div className="flex items-center gap-2">
                <Tag color={item.count > 10 ? 'red' : item.count > 3 ? 'orange' : 'blue'}>{item.count}</Tag>
                <span>{errTypeLabel[item.errorType] || item.errorType}</span>
                {item.latestTime && <span className="text-xs text-gray-400">{item.latestTime}</span>}
              </div>
            ),
            children: (
              <div>
                <div className="text-xs text-gray-500 mb-2">{t('diagnostics.suggestion')}: {item.suggestion}</div>
                <div className="text-xs font-mono bg-gray-50 dark:bg-gray-800 p-2 rounded break-all">{item.latestMessage}</div>
              </div>
            ),
          }))} />
        ) : (
          <Alert type="success" message={t('diagnostics.noIssues')} showIcon />
        )}
      </Card>
    </div>
  )
}

import { Card, Descriptions, Statistic, Tag, Row, Col, Progress, Tooltip, Spin, Modal, Tabs, List, Alert, Button, Space, Collapse } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getSystemHealthSummary, getFullDiagnostics, analyzeLogDiagnostics } from '@/apis'
import {
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined,
  DatabaseOutlined, CloudServerOutlined, ThunderboltOutlined,
  SafetyCertificateOutlined, PlayCircleOutlined,
  ReloadOutlined, BugOutlined, DesktopOutlined
} from '@ant-design/icons'

const severityColor = { ok: 'green', warning: 'orange', error: 'red', info: 'blue' }
const errTypeLabel = {
  proxy_error: '代理异常', timeout: '超时', source_error: '弹幕源异常',
  db_error: '数据库异常', ai_error: 'AI接口异常', auth_error: '认证异常',
  disk_error: '磁盘异常', memory_error: '内存异常',
}

const HealthOverviewModal = ({ open, onClose }) => {
  const { t } = useTranslation()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  // 系统诊断数据
  const [diagData, setDiagData] = useState(null)
  const [diagLoading, setDiagLoading] = useState(true)

  useEffect(() => {
    if (open) {
      setLoading(true)
      getSystemHealthSummary().then(res => {
        setData(res?.data)
        setLoading(false)
      }).catch(() => setLoading(false))
      reloadDiagnostics()
    }
  }, [open])

  const reloadDiagnostics = () => {
    setDiagLoading(true)
    getFullDiagnostics().then(res => { setDiagData(res?.data); setDiagLoading(false) }).catch(() => setDiagLoading(false))
  }

  const taskTotal = data ? Object.values(data.taskSummary || {}).reduce((a, b) => a + b, 0) : 0
  const taskSuccess = data ? (data.taskSummary?.completed || 0) + (data.taskSummary?.success || 0) : 0
  const taskFail = data ? (data.taskSummary?.failed || 0) + (data.taskSummary?.error || 0) : 0
  const scoreColor = data?.configScore >= 80 ? '#52c41a' : data?.configScore >= 50 ? '#faad14' : '#ff4d4f'

  // ===== Tab1: 健康概览 =====
  const healthContent = loading ? <Spin className="w-full flex justify-center py-8" /> : !data ? null : (
    <>
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={8}>
          <Statistic title={t('healthOverview.todayDanmaku')} value={data.todayNewDanmaku} prefix={<ThunderboltOutlined />} />
        </Col>
        <Col xs={12} sm={8}>
          <Statistic title={t('healthOverview.missingEp')} value={data.missingEpisodes} valueStyle={{ color: data.missingEpisodes > 0 ? '#faad14' : '#52c41a' }} prefix={<WarningOutlined />} />
        </Col>
        <Col xs={12} sm={8}>
          <Statistic title={t('healthOverview.taskSuccess')} value={taskSuccess} suffix={`/ ${taskTotal}`} prefix={<CheckCircleOutlined />} valueStyle={{ color: '#52c41a' }} />
        </Col>
        <Col xs={12} sm={8}>
          <Statistic title={t('healthOverview.taskFail')} value={taskFail} prefix={<CloseCircleOutlined />} valueStyle={{ color: taskFail > 0 ? '#ff4d4f' : '#52c41a' }} />
        </Col>
        <Col xs={12} sm={8}>
          <Tooltip title={`${data.scraperSummary?.enabled || 0} / ${data.scraperSummary?.total || 0}`}>
            <Statistic title={t('healthOverview.scrapers')} value={data.scraperSummary?.enabled || 0} suffix={`/ ${data.scraperSummary?.total || 0}`} prefix={<CloudServerOutlined />} />
          </Tooltip>
          {(data.scraperSummary?.unhealthy || 0) > 0 && <Tag color="orange" className="mt-1">{data.scraperSummary.unhealthy} {t('healthOverview.unhealthy')}</Tag>}
        </Col>
        <Col xs={12} sm={8}>
          <Tooltip title={`${t('healthOverview.configScore')}: ${data.configScore}%`}>
            <div className="text-center">
              <div className="text-xs text-gray-500 mb-1">{t('healthOverview.configScore')}</div>
              <Progress type="circle" percent={data.configScore} size={48} strokeColor={scoreColor} />
            </div>
          </Tooltip>
        </Col>
      </Row>
      {data.backupStatus?.lastBackup && (
        <div className="mt-3 text-xs text-gray-400">
          <SafetyCertificateOutlined className="mr-1" />
          {t('healthOverview.lastBackup')}: {new Date(data.backupStatus.lastBackup).toLocaleString()}
        </div>
      )}
    </>
  )

  // ===== Tab2: 系统诊断 =====
  const env = diagData?.environment || {}
  const diagContent = diagLoading ? <Spin className="w-full flex justify-center py-8" /> : !diagData ? (
    <Alert type="warning" message={t('diagnostics.loadFailed')} />
  ) : (
    <div className="space-y-4">
      <Card title={<><DesktopOutlined className="mr-2" />{t('diagnostics.envTitle')}</>} size="small"
        extra={<Button icon={<ReloadOutlined />} onClick={reloadDiagnostics} size="small">{t('diagnostics.refresh')}</Button>}>
        <Descriptions column={{ xs: 1, sm: 2 }} size="small" bordered>
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
        {diagData.checks?.length > 0 && (
          <div className="mt-3">
            <Space wrap>
              {diagData.checks.map((c, i) => (
                <Tag key={i} color={severityColor[c.status]} icon={c.status === 'ok' ? <CheckCircleOutlined /> : <WarningOutlined />}>
                  {c.label}: {c.detail || c.status}
                </Tag>
              ))}
            </Space>
          </div>
        )}
      </Card>

      <Card title={<><BugOutlined className="mr-2" />{t('diagnostics.logTitle')}</>} size="small">
        {diagData.logDiagnostics?.length > 0 ? (
          <Collapse size="small" items={diagData.logDiagnostics.map((item, i) => ({
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

  return (
    <Modal title={t('healthOverview.title')} open={open} onCancel={onClose} footer={null} width={720}>
      <Tabs
        items={[
          { key: 'health', label: <><CheckCircleOutlined className="mr-1" />{t('healthOverview.tabHealth')}</>, children: healthContent },
          { key: 'diagnostics', label: <><BugOutlined className="mr-1" />{t('healthOverview.tabDiagnostics')}</>, children: diagContent },
        ]}
      />
    </Modal>
  )
}

export default HealthOverviewModal

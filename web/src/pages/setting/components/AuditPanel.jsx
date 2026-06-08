import { Card, Button, Table, Tag, Space, Statistic, Row, Col, Popconfirm, message, Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getAuditLogs, getSessionStats, clearAuditLogs } from '@/apis'
import { SafetyCertificateOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'

const eventTypeColors = {
  login_success: 'green', login_failed: 'red', mfa_verify: 'blue',
  password_change: 'orange', session_revoke: 'purple', passkey_register: 'cyan',
}

export const AuditPanel = () => {
  const { t } = useTranslation()
  const [logs, setLogs] = useState([])
  const [stats, setStats] = useState({})
  const [loading, setLoading] = useState(true)

  const reload = () => {
    setLoading(true)
    Promise.all([
      getAuditLogs({ limit: 50 }),
      getSessionStats(),
    ]).then(([logRes, statRes]) => {
      setLogs(Array.isArray(logRes) ? logRes : [])
      setStats(statRes || {})
      setLoading(false)
    }).catch(() => setLoading(false))
  }

  useEffect(() => { reload() }, [])

  const columns = [
    {
      title: t('audit.time'), dataIndex: 'timestamp', width: 160,
      render: v => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: t('audit.event'), dataIndex: 'eventType', width: 120,
      render: v => <Tag color={eventTypeColors[v] || 'default'}>{v}</Tag>,
    },
    { title: 'IP', dataIndex: 'ipAddress', width: 140 },
    {
      title: t('audit.status'), dataIndex: 'success', width: 80,
      render: v => v ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag>,
    },
    {
      title: t('audit.detail'), dataIndex: 'detail', ellipsis: true,
    },
  ]

  return (
    <div className="space-y-4">
      <Row gutter={16}>
        <Col span={12}>
          <Card size="small">
            <Statistic title={t('audit.activeSessions')} value={stats.activeSessions || 0} />
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small">
            <Statistic title={t('audit.totalSessions')} value={stats.totalSessions || 0} />
          </Card>
        </Col>
      </Row>
      <Card title={<><SafetyCertificateOutlined className="mr-2" />{t('audit.logTitle')}</>} size="small"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={reload} size="small" />
            <Popconfirm title={t('audit.confirmClear')} onConfirm={async () => { await clearAuditLogs(); reload(); message.success('ok') }}>
              <Button icon={<DeleteOutlined />} size="small" danger />
            </Popconfirm>
          </Space>
        }>
        <Table columns={columns} dataSource={[...logs].reverse()} rowKey={(_, i) => i} size="small" pagination={{ pageSize: 10 }} loading={loading} />
      </Card>
    </div>
  )
}

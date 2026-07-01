import { Card, Table, Tag, Statistic, Row, Col, Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getAIMatchExplainStats, getRecentAIMatches } from '@/apis'
import { RobotOutlined, ThunderboltOutlined } from '@ant-design/icons'

export const AIExplainPanel = () => {
  const { t } = useTranslation()
  const [stats, setStats] = useState({})
  const [matches, setMatches] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      getAIMatchExplainStats(24),
      getRecentAIMatches(20),
    ]).then(([s, m]) => {
      setStats(s || {})
      setMatches(Array.isArray(m) ? m : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <Spin className="flex justify-center py-8" />

  const columns = [
    {
      title: t('aiExplain.time'), dataIndex: 'timestamp', width: 140,
      render: v => v ? new Date(v).toLocaleString() : '-',
    },
    { title: t('aiExplain.method'), dataIndex: 'method', width: 140, ellipsis: true },
    {
      title: t('aiExplain.status'), dataIndex: 'success', width: 80,
      render: (v, r) => v ? <Tag color="green">✓</Tag> : <Tag color="red">✗</Tag>,
    },
    { title: t('aiExplain.model'), dataIndex: 'model', width: 120, ellipsis: true },
    {
      title: 'Tokens', dataIndex: 'tokensUsed', width: 80,
      render: v => v > 0 ? v : '-',
    },
    {
      title: t('aiExplain.duration'), dataIndex: 'durationMs', width: 90,
      render: v => `${v}ms`,
    },
    {
      title: t('aiExplain.cache'), dataIndex: 'cacheHit', width: 70,
      render: v => v ? <Tag color="blue">HIT</Tag> : '-',
    },
  ]

  return (
    <div className="space-y-4">
      <Row gutter={16}>
        <Col xs={12} sm={6}><Card size="small"><Statistic title={t('aiExplain.totalCalls')} value={stats.totalCalls || 0} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title={t('aiExplain.successRate')} value={stats.successRate || 0} suffix="%" valueStyle={{ color: (stats.successRate || 0) >= 80 ? '#52c41a' : '#faad14' }} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title={t('aiExplain.totalTokens')} value={stats.totalTokens || 0} prefix={<ThunderboltOutlined />} /></Card></Col>
        <Col xs={12} sm={6}><Card size="small"><Statistic title={t('aiExplain.cacheHitRate')} value={stats.cacheHitRate || 0} suffix="%" /></Card></Col>
      </Row>
      <Card title={<><RobotOutlined className="mr-2" />{t('aiExplain.recentTitle')}</>} size="small">
        <Table columns={columns} dataSource={matches} rowKey="id" size="small" pagination={{ pageSize: 10 }} />
      </Card>
    </div>
  )
}

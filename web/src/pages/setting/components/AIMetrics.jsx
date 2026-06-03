import React, { useState, useEffect } from 'react'
import { Card, Row, Col, Statistic, Button, Select, message, Spin, Empty } from 'antd'
import { ReloadOutlined, DeleteOutlined, DownloadOutlined } from '@ant-design/icons'
import { getAIMetrics, clearAICache } from '@/apis'
import { MyIcon } from '@/components/MyIcon'
import { useTranslation } from 'react-i18next'

const { Option } = Select

const AIMetrics = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [metricsData, setMetricsData] = useState(null)
  const [timeRange, setTimeRange] = useState(24)
  const [clearing, setClearing] = useState(false)

  // 加载统计数据
  const loadMetrics = async () => {
    try {
      setLoading(true)
      const res = await getAIMetrics(timeRange)
      setMetricsData(res.data)
    } catch (error) {
      console.error('加载AI统计失败:', error)
      message.error(t('aiMetrics.loadFailed', { error: error?.message || t('common.unknown') }))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadMetrics()
  }, [timeRange])

  // 清空缓存
  const handleClearCache = async () => {
    try {
      setClearing(true)
      await clearAICache()
      message.success(t('aiMetrics.cacheCleared'))
      loadMetrics() // 重新加载统计
    } catch (error) {
      console.error('清空缓存失败:', error)
      message.error(t('aiMetrics.clearFailed', { error: error?.message || t('common.unknown') }))
    } finally {
      setClearing(false)
    }
  }

  if (loading && !metricsData) {
    return (
      <div style={{ textAlign: 'center', padding: '50px' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!metricsData) {
    return <Empty description={t('aiMetrics.noData')} />
  }

  const { ai_stats, cache_stats, source } = metricsData
  const summary = ai_stats?.summary

  return (
    <div>
      {/* 操作栏 */}
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          <span>{t('aiMetrics.timeRange')}</span>
          <Select value={timeRange} onChange={setTimeRange} style={{ width: 150 }}>
            <Option value={1}>{t('aiMetrics.last1h')}</Option>
            <Option value={24}>{t('aiMetrics.last24h')}</Option>
            <Option value={168}>{t('aiMetrics.last7d')}</Option>
            <Option value={720}>{t('aiMetrics.last30d')}</Option>
          </Select>
          {source && (
            <span style={{ color: '#888', fontSize: 12 }}>
              {t('aiMetrics.dataSource', { source: source === 'db' ? t('aiMetrics.sourceDb') : t('aiMetrics.sourceMem') })}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button icon={<ReloadOutlined />} onClick={loadMetrics} loading={loading}>
            {t('aiMetrics.btnRefresh')}
          </Button>
          <Button
            icon={<DeleteOutlined />}
            onClick={handleClearCache}
            loading={clearing}
            danger
          >
            {t('aiMetrics.btnClearCache')}
          </Button>
        </div>
      </div>

      {/* 调用统计 */}
      <Card title={<span><MyIcon icon="liukongcelve" size={16} style={{ marginRight: 6 }} />{t('aiMetrics.callStats')}</span>} style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col xs={24} sm={12} md={6}>
            <Statistic title={t('aiMetrics.totalCalls')} value={ai_stats?.total_calls || 0} />
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Statistic title={t('aiMetrics.successCount')} value={Math.round((ai_stats?.total_calls || 0) * (ai_stats?.success_rate || 0))} valueStyle={{ color: '#3f8600' }} />
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Statistic title={t('aiMetrics.failCount')} value={Math.round((ai_stats?.total_calls || 0) * (1 - (ai_stats?.success_rate || 0)))} valueStyle={{ color: '#cf1322' }} />
          </Col>
          <Col xs={24} sm={12} md={6}>
            <Statistic title={t('aiMetrics.successRate')} value={(ai_stats?.success_rate || 0) * 100} precision={1} suffix="%" valueStyle={{ color: ((ai_stats?.success_rate || 0) * 100) >= 90 ? '#3f8600' : '#faad14' }} />
          </Col>
        </Row>
      </Card>

      {/* Token 统计 */}
      <Card title={t('aiMetrics.tokenStats')} style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col xs={24} sm={12} md={8}>
            <Statistic title={t('aiMetrics.totalTokens')} value={ai_stats?.total_tokens || 0} />
          </Col>
          <Col xs={24} sm={12} md={8}>
            <Statistic title={t('aiMetrics.avgResponseTime')} value={((ai_stats?.avg_duration_ms || 0) / 1000).toFixed(2)} suffix="s" />
          </Col>
          <Col xs={24} sm={12} md={8}>
            <Statistic title={t('aiMetrics.cacheHitRate')} value={(ai_stats?.cache_hit_rate || 0) * 100} precision={1} suffix="%" valueStyle={{ color: ((ai_stats?.cache_hit_rate || 0) * 100) >= 30 ? '#3f8600' : '#faad14' }} />
          </Col>
        </Row>
      </Card>

      {/* 缓存统计 */}
      {cache_stats && (
        <Card title={t('aiMetrics.cacheStats')} style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.cacheHits')} value={cache_stats.hits || 0} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.cacheMisses')} value={cache_stats.misses || 0} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.cacheHitRateLabel')} value={(cache_stats.hit_rate || 0) * 100} precision={1} suffix="%" valueStyle={{ color: ((cache_stats.hit_rate || 0) * 100) >= 30 ? '#3f8600' : '#faad14' }} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.cacheSize')} value={`${cache_stats.size || 0} / ${cache_stats.max_size || 1000}`} />
            </Col>
          </Row>
        </Card>
      )}

      {/* 历史总计（仅数据库模式） */}
      {summary && source === 'db' && (
        <Card title={t('aiMetrics.historySummary')}>
          <Row gutter={16}>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.totalCallsAllTime')} value={summary.total_calls_all_time || 0} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.totalTokensAllTime')} value={summary.total_tokens_all_time || 0} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.firstCall')} value={summary.first_call ? new Date(summary.first_call).toLocaleString() : '-'} valueStyle={{ fontSize: 14 }} />
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Statistic title={t('aiMetrics.lastCall')} value={summary.last_call ? new Date(summary.last_call).toLocaleString() : '-'} valueStyle={{ fontSize: 14 }} />
            </Col>
          </Row>
        </Card>
      )}
    </div>
  )
}

export default AIMetrics


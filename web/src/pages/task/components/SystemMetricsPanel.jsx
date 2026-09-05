import { Card, Row, Col, Spin, Empty, Alert, Segmented, Modal, Tag } from 'antd'
import { useEffect, useRef, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { getSystemMetrics, getSystemMetricHistory } from '@/apis'

/** 按状态取主题色（normal/warning/critical） */
function statusColor(status) {
  if (status === 'critical') return '#dc2626'
  if (status === 'warning') return '#d97706'
  return '#16a34a'
}

/** 格式化指标值 + 单位 */
function fmtValue(m) {
  if (m.value == null && m.valueText == null) return '-'
  if (m.valueText != null && m.value == null) return m.valueText
  const v = m.value
  const unit = m.unit || ''
  if (unit === 'percent') return `${Number(v).toFixed(1)}%`
  if (unit === 'MB') return `${Number(v).toFixed(1)} MB`
  if (unit === 'GB') return `${Number(v).toFixed(2)} GB`
  if (unit === 'count') return `${v}`
  if (unit === 'ms') return `${Math.round(v)} ms`
  return `${v}${unit ? ' ' + unit : ''}`
}

/** 迷你 SVG 趋势折线（复用任务画像面板的视觉风格） */
const TrendSvg = ({ points, color }) => {
  const vals = points.map((p) => p.value).filter((x) => x != null)
  if (vals.length < 2) {
    return (
      <div style={{ height: 40, lineHeight: '40px', fontSize: 11, color: '#9ca3af', textAlign: 'center' }}>
        暂无足够趋势数据
      </div>
    )
  }
  const W = 260, H = 40, p = 4
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1
  const pts = points.map((pt, i) => ({
    x: p + (i / (points.length - 1)) * (W - p * 2),
    y: pt.value == null ? null : H - p - ((pt.value - mn) / rng) * (H - p * 2),
  }))
  let d = '', prev = null
  for (const pt of pts) {
    if (pt.y == null) { prev = null; continue }
    d += prev ? `L${pt.x.toFixed(1)},${pt.y.toFixed(1)}` : `M${pt.x.toFixed(1)},${pt.y.toFixed(1)}`
    prev = pt
  }
  const fi = pts.find((q) => q.y != null)
  const la = pts.filter((q) => q.y != null).at(-1)
  const area = fi && la ? `${d}L${la.x},${H - p}L${fi.x},${H - p}Z` : ''
  const id = `sg${Math.random().toString(36).slice(2, 6)}`
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.18} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={la.x.toFixed(1)} cy={la.y.toFixed(1)} r={3} fill={color} />
    </svg>
  )
}

export { statusColor, fmtValue, TrendSvg }

/** 单个指标卡：显示当前值 + 状态色条，可点击查看趋势 */
const MetricCard = ({ metric, onClick }) => {
  const color = statusColor(metric.status)
  return (
    <Card
      size="small"
      hoverable
      onClick={() => onClick(metric)}
      style={{ borderLeft: `3px solid ${color}`, cursor: 'pointer' }}
      styles={{ body: { padding: '12px 14px' } }}
    >
      <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {metric.displayName}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>
        {fmtValue(metric)}
      </div>
      {metric.status !== 'normal' && (
        <Tag color={metric.status === 'critical' ? 'error' : 'warning'} style={{ marginTop: 6, fontSize: 10 }}>
          {metric.status === 'critical' ? '严重' : '警告'}
        </Tag>
      )}
    </Card>
  )
}

/** 组的显示顺序与标题（key 对应后端 groups 的分类） */
const GROUP_ORDER = [
  { key: 'system', tKey: 'systemMetrics.groupSystem' },
  { key: 'database', tKey: 'systemMetrics.groupDatabase' },
  { key: 'task', tKey: 'systemMetrics.groupTask' },
  { key: 'cache', tKey: 'systemMetrics.groupCache' },
]

export const SystemMetricsPanel = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [groups, setGroups] = useState({})
  const [alerts, setAlerts] = useState([])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [updatedAt, setUpdatedAt] = useState(null)
  // 趋势弹窗
  const [trendOpen, setTrendOpen] = useState(false)
  const [trendMetric, setTrendMetric] = useState(null)
  const [trendPoints, setTrendPoints] = useState([])
  const [trendHours, setTrendHours] = useState(24)
  const [trendLoading, setTrendLoading] = useState(false)
  const timerRef = useRef(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await getSystemMetrics()
      setGroups(res?.groups || {})
      setAlerts(res?.alerts || [])
      setUpdatedAt(new Date())
    } catch (e) {
      // 静默：保留上一轮数据，避免刷新失败清空页面
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // 自动刷新（60 秒，与采集周期对齐）
  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = setInterval(fetchData, 60000)
      return () => clearInterval(timerRef.current)
    }
  }, [autoRefresh, fetchData])

  // 加载某指标的历史趋势
  const loadTrend = useCallback(async (metric, hours) => {
    setTrendLoading(true)
    try {
      const res = await getSystemMetricHistory(metric.category, metric.metricName, hours)
      setTrendPoints(res?.points || [])
    } catch (e) {
      setTrendPoints([])
    } finally {
      setTrendLoading(false)
    }
  }, [])

  const openTrend = useCallback((metric) => {
    setTrendMetric(metric)
    setTrendHours(24)
    setTrendOpen(true)
    loadTrend(metric, 24)
  }, [loadTrend])

  const changeTrendHours = useCallback((h) => {
    setTrendHours(h)
    if (trendMetric) loadTrend(trendMetric, h)
  }, [trendMetric, loadTrend])

  const hasAnyData = GROUP_ORDER.some((g) => (groups[g.key] || []).length > 0)

  return (
    <div>
      {/* 顶部：刷新控制 + 更新时间 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 12, color: '#9ca3af' }}>
          {updatedAt ? `${t('systemMetrics.updatedAt')}: ${updatedAt.toLocaleTimeString()}` : ''}
        </div>
        <Segmented
          size="small"
          value={autoRefresh ? 'on' : 'off'}
          onChange={(v) => setAutoRefresh(v === 'on')}
          options={[
            { label: t('systemMetrics.autoRefreshOn'), value: 'on' },
            { label: t('systemMetrics.autoRefreshOff'), value: 'off' },
          ]}
        />
      </div>

      {/* 告警条 */}
      {alerts.length > 0 && (
        <Alert
          type={alerts.some((a) => a.level === 'critical') ? 'error' : 'warning'}
          showIcon
          style={{ marginBottom: 16 }}
          message={`${t('systemMetrics.activeAlerts')} (${alerts.length})`}
          description={
            <div style={{ maxHeight: 120, overflowY: 'auto' }}>
              {alerts.map((a) => (
                <div key={a.id} style={{ fontSize: 12, marginBottom: 2 }}>
                  <Tag color={a.level === 'critical' ? 'error' : 'warning'} style={{ fontSize: 10 }}>
                    {a.level === 'critical' ? '严重' : '警告'}
                  </Tag>
                  {a.message}
                </div>
              ))}
            </div>
          }
        />
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : !hasAnyData ? (
        <Empty description={t('systemMetrics.noData')} style={{ padding: 40 }} />
      ) : (
        GROUP_ORDER.map((g) => (
          <GroupSection
            key={g.key}
            title={t(g.tKey)}
            metrics={groups[g.key]}
            onCardClick={openTrend}
          />
        ))
      )}

      {/* 趋势弹窗 */}
      <Modal
        open={trendOpen}
        onCancel={() => setTrendOpen(false)}
        footer={null}
        title={trendMetric?.displayName}
        width={640}
      >
        <Segmented
          size="small"
          value={trendHours}
          onChange={changeTrendHours}
          options={[
            { label: t('systemMetrics.range6h'), value: 6 },
            { label: t('systemMetrics.range24h'), value: 24 },
            { label: t('systemMetrics.range7d'), value: 168 },
          ]}
          style={{ marginBottom: 16 }}
        />
        {trendLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : (
          <TrendSvg
            points={trendPoints}
            color={statusColor(trendMetric?.status)}
          />
        )}
      </Modal>
    </div>
  )
}

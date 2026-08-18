import { Select, Spin, Tooltip, Empty, Card, Row, Col, Statistic, Typography, Button } from 'antd'
import { useEffect, useRef, useState, useCallback } from 'react'
import { getPerfStats } from '@/apis'

const { Title, Paragraph } = Typography

/** 格式化毫秒 */
function fmtMs(ms) {
  if (ms == null) return '-'
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

/** 按耗时返回颜色 */
function dc(ms) {
  if (ms > 30000) return '#ef4444'
  if (ms > 5000) return '#f59e0b'
  if (ms > 1000) return '#eab308'
  return '#22c55e'
}

/** 加权均耗时：∑(avgMs × runs) / ∑runs，比简单算术平均更准确 */
function weightedAvg(flows) {
  const tot = flows.reduce((s, f) => s + f.totalRuns, 0)
  if (!tot) return 0
  return flows.reduce((s, f) => s + f.avgTotalMs * f.totalRuns, 0) / tot
}

/** 成功率徽章 */
const SrBadge = ({ sr }) => {
  const [cls, color] =
    sr >= 95 ? ['badge-ok', '#16a34a'] :
    sr >= 70 ? ['badge-warn', '#d97706'] :
               ['badge-err', '#dc2626']
  return (
    <span style={{
      display: 'inline-block', fontSize: 10, padding: '1px 7px',
      borderRadius: 99, fontWeight: 700,
      background: sr >= 95 ? '#f0fdf4' : sr >= 70 ? '#fffbeb' : '#fef2f2',
      color,
      border: `1px solid ${sr >= 95 ? '#bbf7d0' : sr >= 70 ? '#fde68a' : '#fecaca'}`,
    }}>
      {sr.toFixed(0)}%
    </span>
  )
}

/** 迷你 SVG 趋势折线（带面积渐变 + 末端高亮点） */
const TrendSvg = ({ vals, color }) => {
  const v = vals.filter(x => x != null)
  if (v.length < 2) return <div style={{ height: 28, lineHeight: '28px', fontSize: 10, color: '#e5e7eb' }}>暂无数据</div>
  const W = 200, H = 28, p = 4
  const mn = Math.min(...v), mx = Math.max(...v), rng = mx - mn || 1
  const pts = vals.map((val, i) => ({
    x: p + i / (vals.length - 1) * (W - p * 2),
    y: val == null ? null : H - p - (val - mn) / rng * (H - p * 2),
  }))
  let d = '', prev = null
  for (const pt of pts) {
    if (pt.y == null) { prev = null; continue }
    d += prev ? `L${pt.x.toFixed(1)},${pt.y.toFixed(1)}` : `M${pt.x.toFixed(1)},${pt.y.toFixed(1)}`
    prev = pt
  }
  const fi = pts.find(p => p.y != null), la = pts.filter(p => p.y != null).at(-1)
  const area = fi && la ? `${d}L${la.x},${H - p}L${fi.x},${H - p}Z` : ''
  const id = `tg${Math.random().toString(36).slice(2, 6)}`
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.15} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={la.x.toFixed(1)} cy={la.y.toFixed(1)} r={3} fill={color} />
    </svg>
  )
}

/** 单步骤进度条行 */
const StepRow = ({ step, maxMs }) => {
  const pct = Math.max(4, Math.round(step.avgMs / maxMs * 100))
  const color = dc(step.avgMs)
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, marginBottom: 4 }}>
        <span style={{ fontWeight: 600, color: '#374151', flex: 1, paddingRight: 6 }}>{step.stepName}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <span style={{ fontWeight: 700, color }}>{fmtMs(step.avgMs)}</span>
          <SrBadge sr={step.successRate} />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, height: 5, background: '#f3f4f6', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3, transition: 'width .4s cubic-bezier(.4,0,.2,1)' }} />
        </div>
        <Tooltip title={`最大耗时: ${fmtMs(step.maxMs)} · 调用 ${step.callCount} 次`}>
          <span style={{ fontSize: 10, color: '#d1d5db', minWidth: 72, textAlign: 'right', whiteSpace: 'nowrap', cursor: 'default' }}>
            max {fmtMs(step.maxMs)} · {step.callCount}次
          </span>
        </Tooltip>
      </div>
    </div>
  )
}

/** 单流程卡片 — 使用 Ant Design Card type="inner"，默认展开步骤 */
const FlowCard = ({ flow }) => {
  const [open, setOpen] = useState(true)
  const steps = flow.steps ?? []
  const hasSteps = steps.length > 0
  const color = dc(flow.avgTotalMs)
  const maxMs = hasSteps ? Math.max(...steps.map(s => s.avgMs || 0), 1) : 1

  return (
    <Card
      type="inner"
      style={{ marginBottom: 14, breakInside: 'avoid' }}
      styles={{ header: { borderBottom: `3px solid ${color}` } }}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: '#111827' }}>{flow.flowType}</span>
          <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 400 }}>{flow.totalRuns} 次</span>
        </div>
      }
      extra={
        hasSteps ? (
          <span
            style={{ fontSize: 12, color: '#6b7280', cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setOpen(o => !o)}
          >
            {steps.length} 个步骤{' '}
            <span style={{ fontSize: 9, display: 'inline-block', transition: 'transform .25s', transform: open ? 'rotate(180deg)' : 'none' }}>▼</span>
          </span>
        ) : null
      }
    >
      {/* 耗时大字 */}
      <div style={{ fontSize: 28, fontWeight: 900, color, marginBottom: 3, letterSpacing: '-.8px' }}>{fmtMs(flow.avgTotalMs)}</div>

      {/* 趋势折线 */}
      <div style={{ marginBottom: hasSteps && open ? 14 : 0 }}>
        <div style={{ fontSize: 10, color: '#d1d5db', marginBottom: 3 }}>近期趋势</div>
        <TrendSvg vals={flow.trend ?? []} color={color} />
      </div>

      {/* 步骤展开区 */}
      {hasSteps && (
        <div
          style={{
            borderTop: open ? '1px solid #f3f4f6' : 'none',
            maxHeight: open ? 800 : 0,
            overflow: 'hidden',
            paddingTop: open ? 14 : 0,
            transition: 'max-height .32s cubic-bezier(.4,0,.2,1), padding-top .32s',
          }}
        >
          {steps.map(s => <StepRow key={s.stepName} step={s} maxMs={maxMs} />)}
        </div>
      )}
    </Card>
  )
}

/** 轮询间隔：30 秒 */
const POLL_INTERVAL_MS = 30_000

export const TaskProfilePanel = () => {
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)
  const [lastUpdated, setLastUpdated] = useState(null)
  const timerRef = useRef(null)

  // why：useCallback 避免 fetchData 每次渲染都是新引用，导致 useEffect 循环触发
  const fetchData = useCallback((showLoading = false) => {
    if (showLoading) setLoading(true)
    getPerfStats(days)
      .then(res => {
        setStats(Array.isArray(res?.data) ? res.data : [])
        setLastUpdated(new Date())
        setLoading(false)
      })
      .catch(() => {
        setStats([])
        setLoading(false)
      })
  }, [days])

  useEffect(() => {
    // 切换天数或首次挂载：立即拉取并显示 loading
    fetchData(true)

    // why：仅页面可见时启动轮询，切到后台后暂停，避免无效请求
    const startPolling = () => {
      timerRef.current = setInterval(() => {
        if (document.visibilityState === 'visible') fetchData(false)
      }, POLL_INTERVAL_MS)
    }
    const stopPolling = () => {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    }
    const onVisibilityChange = () => {
      document.visibilityState === 'hidden' ? stopPolling() : startPolling()
    }

    startPolling()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      stopPolling()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [fetchData])

  // 按均耗时降序排列
  const sorted = [...stats].sort((a, b) => b.avgTotalMs - a.avgTotalMs)
  const totalRuns = stats.reduce((s, f) => s + f.totalRuns, 0)
  // why：加权均耗时 = ∑(avgMs × runs) / ∑runs，比简单算术平均更准确
  const avgDur = weightedAvg(stats)
  const slowest = stats.length > 0 ? stats.reduce((a, b) => a.avgTotalMs > b.avgTotalMs ? a : b, stats[0]) : null

  return (
    <div className="my-6">
      <Card>
        <Typography>
          <Title level={4}>性能统计</Title>
          <Paragraph style={{ color: '#9ca3af' }}>
            各任务流程实际耗时 · 步骤分析 · 近期趋势
          </Paragraph>
        </Typography>

        {/* KPI 条 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          {[
            { label: '总执行次数', value: totalRuns,           sub: `近 ${days} 天`,          color: '#6366f1' },
            { label: '加权均耗时', value: fmtMs(avgDur),       sub: '执行次数加权',            color: dc(avgDur) },
            { label: '最慢流程',   value: slowest?.flowType ?? '-',
                                   sub: slowest ? `${fmtMs(slowest.avgTotalMs)} 均耗时` : '-',
                                   color: slowest ? dc(slowest.avgTotalMs) : '#9ca3af' },
          ].map(k => (
            <Col xs={24} sm={8} key={k.label}>
              <Card type="inner">
                <Statistic
                  title={k.label}
                  value={k.value}
                  valueStyle={{ color: k.color, fontWeight: 800 }}
                  suffix={<span style={{ fontSize: 12, color: '#d1d5db' }}>{k.sub}</span>}
                />
              </Card>
            </Col>
          ))}
        </Row>

        {/* 工具栏 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#6b7280' }}>执行流程 · 按均耗时排序</span>
            {lastUpdated && (
              <span style={{ fontSize: 11, color: '#d1d5db' }}>
                更新于 {lastUpdated.toLocaleTimeString()} · 每 30s 自动刷新
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Button
              size="small"
              loading={loading}
              onClick={() => fetchData(true)}
            >
              刷新
            </Button>
            <Select
              value={days}
              onChange={setDays}
              size="small"
              style={{ width: 100 }}
              options={[
                { value: 1, label: '近 1 天' },
                { value: 7, label: '近 7 天' },
                { value: 30, label: '近 30 天' },
              ]}
            />
          </div>
        </div>

        {/* 卡片区 */}
        <Spin spinning={loading}>
          {sorted.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无性能数据，任务执行后将自动记录" />
          ) : (
            <div style={{ columns: '4 260px', columnGap: 14 }}>
              {sorted.map(flow => (
                <FlowCard key={flow.flowType} flow={flow} />
              ))}
            </div>
          )}
        </Spin>
      </Card>
    </div>
  )
}

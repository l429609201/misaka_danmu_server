import { Select, Spin, Tooltip, Empty } from 'antd'
import { useEffect, useState } from 'react'
import { getPerfStats } from '@/apis'

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

/** 单流程卡片 — 对标 perf_demo.html 样式 */
const FlowCard = ({ flow }) => {
  const [open, setOpen] = useState(false)
  const steps = flow.steps ?? []
  const hasSteps = steps.length > 0
  const color = dc(flow.avgTotalMs)
  const maxMs = hasSteps ? Math.max(...steps.map(s => s.avgMs || 0), 1) : 1

  return (
    <div
      onClick={() => hasSteps && setOpen(o => !o)}
      style={{
        background: '#fff',
        borderRadius: 18,
        padding: '18px 18px 16px',
        marginBottom: 14,
        border: '1px solid rgba(255,255,255,.9)',
        boxShadow: '0 1px 3px rgba(0,0,0,.05), 0 8px 24px rgba(0,0,0,.06)',
        transition: 'box-shadow .22s, transform .22s',
        position: 'relative',
        overflow: 'hidden',
        cursor: hasSteps ? 'pointer' : 'default',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,.08), 0 16px 40px rgba(0,0,0,.08)'
        e.currentTarget.style.transform = 'translateY(-2px)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.boxShadow = '0 1px 3px rgba(0,0,0,.05), 0 8px 24px rgba(0,0,0,.06)'
        e.currentTarget.style.transform = 'translateY(0)'
      }}
    >
      {/* 顶部彩条 */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 3, background: color, borderRadius: '18px 18px 0 0', opacity: .8 }} />

      {/* 流程名 + 执行次数 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 14, color: '#111827', lineHeight: 1.3, flex: 1, paddingRight: 6 }}>{flow.flowType}</span>
        <span style={{ fontSize: 10, color: '#d1d5db', whiteSpace: 'nowrap', paddingTop: 2 }}>{flow.totalRuns} 次</span>
      </div>

      {/* 耗时大字 */}
      <div style={{ fontSize: 30, fontWeight: 900, color, marginBottom: 3, letterSpacing: '-.8px' }}>{fmtMs(flow.avgTotalMs)}</div>

      {/* 步骤提示 */}
      <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
        {hasSteps ? (
          <>
            <span>{steps.length} 个步骤</span>
            <span style={{ fontSize: 9, display: 'inline-block', transition: 'transform .25s', transform: open ? 'rotate(180deg)' : 'none' }}>▼</span>
          </>
        ) : <span style={{ color: '#e5e7eb' }}>无步骤详情</span>}
      </div>

      {/* 趋势折线 */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 10, color: '#d1d5db', marginBottom: 3 }}>近期趋势</div>
        <TrendSvg vals={flow.trend ?? []} color={color} />
      </div>

      {/* 步骤展开区（max-height 动画） */}
      <div
        style={{
          borderTop: '1px solid #f3f4f6',
          maxHeight: open ? 700 : 0,
          overflow: 'hidden',
          paddingTop: open ? 14 : 0,
          transition: 'max-height .32s cubic-bezier(.4,0,.2,1), padding-top .32s',
        }}
        onClick={e => e.stopPropagation()}
      >
        {hasSteps
          ? steps.map(s => <StepRow key={s.stepName} step={s} maxMs={maxMs} />)
          : <div style={{ fontSize: 11, color: '#e5e7eb' }}>此流程暂无步骤数据</div>
        }
      </div>
    </div>
  )
}

export const TaskProfilePanel = () => {
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)

  useEffect(() => {
    setLoading(true)
    getPerfStats(days)
      .then(res => { setStats(Array.isArray(res?.data) ? res.data : []); setLoading(false) })
      .catch(() => { setStats([]); setLoading(false) })
  }, [days])

  // 按均耗时降序排列
  const sorted = [...stats].sort((a, b) => b.avgTotalMs - a.avgTotalMs)
  const totalRuns = stats.reduce((s, f) => s + f.totalRuns, 0)
  // why：加权均耗时 = ∑(avgMs × runs) / ∑runs，比简单算术平均更准确
  const avgDur = weightedAvg(stats)
  const slowest = stats.length > 0 ? stats.reduce((a, b) => a.avgTotalMs > b.avgTotalMs ? a : b, stats[0]) : null

  return (
    <div className="my-6">
      {/* 页头 */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: '-.3px', color: '#111827', display: 'flex', alignItems: 'center', gap: 8 }}>
          ⚡ 性能统计
        </div>
        <div style={{ fontSize: 13, color: '#9ca3af', marginTop: 4 }}>
          各任务流程实际耗时 · 步骤分析 · 近期趋势 · 点击卡片展开步骤详情
        </div>
      </div>

      {/* KPI 条 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        {[
          { label: '总执行次数', value: totalRuns, sub: `近 ${days} 天`, color: '#6366f1' },
          { label: '加权均耗时', value: fmtMs(avgDur), sub: '执行次数加权', color: dc(avgDur) },
          { label: '最慢流程',   value: slowest?.flowType ?? '-', sub: slowest ? `${fmtMs(slowest.avgTotalMs)} 均耗时` : '-', color: slowest ? dc(slowest.avgTotalMs) : '#9ca3af' },
        ].map(k => (
          <div key={k.label} style={{ background: '#fff', borderRadius: 14, padding: '16px 20px', flex: 1, minWidth: 150, boxShadow: '0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.04)', border: '1px solid rgba(0,0,0,.05)' }}>
            <div style={{ fontSize: 11, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '.5px', marginBottom: 8 }}>{k.label}</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: k.color }}>{k.value}</div>
            <div style={{ fontSize: 11, color: '#d1d5db', marginTop: 4 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#6b7280' }}>执行流程 · 按均耗时排序</span>
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

      {/* 卡片区 — columns 瀑布流，展开单卡不影响其他列高度 */}
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
    </div>
  )
}

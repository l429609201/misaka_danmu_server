import { Card, Select, Spin, Tooltip, Progress, Typography, Row, Col, Statistic, Empty } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getPerfStats } from '@/apis'
import { ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, RocketOutlined } from '@ant-design/icons'

const { Title, Paragraph } = Typography

/** 格式化毫秒为可读字符串 */
function fmtMs(ms) {
  if (ms == null) return '-'
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

/** 根据平均耗时返回颜色 */
function getDurColor(ms) {
  if (ms > 30000) return '#ff4d4f'
  if (ms > 5000) return '#fa8c16'
  if (ms > 1000) return '#faad14'
  return '#52c41a'
}

/** 单步骤水平条 */
const StepBar = ({ step, maxMs }) => {
  const pct = Math.max(4, Math.round((step.avgMs / maxMs) * 100))
  const color = getDurColor(step.avgMs)
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 12 }}>
        <span style={{ fontWeight: 500, color: 'inherit' }}>{step.stepName}</span>
        <span style={{ color, fontWeight: 600 }}>{fmtMs(step.avgMs)}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, height: 6, background: 'rgba(0,0,0,0.06)', borderRadius: 3 }}>
          <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 3, transition: 'width .3s' }} />
        </div>
        <Tooltip title={`最大耗时: ${fmtMs(step.maxMs)} · 调用: ${step.callCount} 次`}>
          <span style={{ fontSize: 11, color: '#999', minWidth: 28, textAlign: 'right' }}>
            {step.callCount}次
          </span>
        </Tooltip>
        <span style={{
          fontSize: 11, minWidth: 36, textAlign: 'right',
          color: step.successRate >= 95 ? '#52c41a' : step.successRate >= 70 ? '#faad14' : '#ff4d4f',
        }}>
          {step.successRate.toFixed(0)}%
        </span>
      </div>
    </div>
  )
}

/** 单流程卡片 */
const FlowCard = ({ flow }) => {
  const [expanded, setExpanded] = useState(false)
  const durColor = getDurColor(flow.avgTotalMs)
  const maxMs = Math.max(...(flow.steps || []).map(s => s.avgMs || 0), 1)

  return (
    <Card
      size="small"
      className="perf-flow-card"
      style={{ height: '100%', cursor: 'pointer' }}
      onClick={() => setExpanded(e => !e)}
      styles={{ body: { padding: '12px 14px' } }}
    >
      {/* 顶部：流程名 + 执行次数 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <span style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.3, flex: 1, paddingRight: 8 }}>
          {flow.flowType}
        </span>
        <span style={{ fontSize: 11, color: '#999', whiteSpace: 'nowrap' }}>
          {flow.totalRuns} 次
        </span>
      </div>

      {/* 平均耗时大字 */}
      <div style={{ fontSize: 22, fontWeight: 700, color: durColor, marginBottom: 4 }}>
        {fmtMs(flow.avgTotalMs)}
      </div>
      <div style={{ fontSize: 11, color: '#aaa', marginBottom: expanded ? 12 : 0 }}>
        {flow.steps?.length ?? 0} 个步骤 · 点击{expanded ? '收起' : '展开'}
      </div>

      {/* 展开区：步骤条 */}
      {expanded && flow.steps?.length > 0 && (
        <div style={{ marginTop: 4, borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 10 }}
          onClick={e => e.stopPropagation()}>
          {flow.steps.map(s => (
            <StepBar key={s.stepName} step={s} maxMs={maxMs} />
          ))}
        </div>
      )}
    </Card>
  )
}

export const TaskProfilePanel = () => {
  const { t } = useTranslation()
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)

  useEffect(() => {
    setLoading(true)
    getPerfStats(days)
      .then(res => { setStats(Array.isArray(res?.data) ? res.data : []); setLoading(false) })
      .catch(() => { setStats([]); setLoading(false) })
  }, [days])

  // 汇总统计
  const totalRuns = stats.reduce((s, f) => s + f.totalRuns, 0)
  const avgDur = stats.length > 0
    ? stats.reduce((s, f) => s + f.avgTotalMs, 0) / stats.length
    : 0
  const slowest = stats.length > 0
    ? stats.reduce((a, b) => a.avgTotalMs > b.avgTotalMs ? a : b, stats[0])
    : null

  return (
    <div className="my-6">
      {/* why：加 perf-stats-card 类名，壁纸模式下走同流控面板相同的毛玻璃 CSS */}
      <Card loading={loading} className="rate-limit-card">
        <Typography>
          <Title level={4}>
            <ThunderboltOutlined style={{ marginRight: 8 }} />
            性能统计
          </Title>
          <Paragraph>
            记录各任务流程（导入、刷新、搜索等）每个阶段的实际耗时，帮助发现慢点。任务执行后自动写入，点击卡片展开步骤详情。
          </Paragraph>
        </Typography>

        {/* 顶部汇总数字 */}
        <Card type="inner" className="!mb-4">
          <Row gutter={16}>
            <Col xs={24} sm={8}>
              <Statistic
                title="近期总执行次数"
                value={totalRuns}
                suffix="次"
                prefix={<RocketOutlined />}
              />
            </Col>
            <Col xs={24} sm={8}>
              <Statistic
                title="平均流程耗时"
                value={fmtMs(avgDur)}
                prefix={<ClockCircleOutlined />}
                valueStyle={{ color: getDurColor(avgDur) }}
              />
            </Col>
            <Col xs={24} sm={8}>
              <Statistic
                title="最慢流程"
                value={slowest ? `${slowest.flowType}` : '-'}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ fontSize: 14, color: slowest ? getDurColor(slowest.avgTotalMs) : undefined }}
              />
            </Col>
          </Row>
        </Card>

        {/* 时间范围选择 */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
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

        {/* 流程卡片网格 */}
        <Spin spinning={loading}>
          {stats.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无性能数据，任务执行后将自动记录"
            />
          ) : (
            <Row gutter={[12, 12]}>
              {stats.map(flow => (
                <Col key={flow.flowType} xs={24} sm={12} lg={8} xl={6}>
                  <FlowCard flow={flow} />
                </Col>
              ))}
            </Row>
          )}
        </Spin>
      </Card>
    </div>
  )
}

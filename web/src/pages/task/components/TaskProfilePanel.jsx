import { Card, Table, Tag, Select, Spin, Tooltip, Progress } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getPerfStats } from '@/apis'
import { ThunderboltOutlined } from '@ant-design/icons'

/** 格式化毫秒为可读字符串 */
function fmtMs(ms) {
  if (ms == null) return '-'
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms.toFixed(0)}ms`
}

/** 步骤列表（展开行） */
const StepTable = ({ steps }) => {
  if (!steps || steps.length === 0) return <span style={{ color: '#999' }}>暂无步骤数据</span>

  // 找最大平均耗时用于横向比例条
  const maxMs = Math.max(...steps.map(s => s.avgMs || 0), 1)

  return (
    <Table
      dataSource={steps}
      rowKey="stepName"
      size="small"
      pagination={false}
      showHeader
      columns={[
        {
          title: '步骤名称', dataIndex: 'stepName', width: 160,
          render: v => <span style={{ fontWeight: 500 }}>{v}</span>,
        },
        {
          title: '平均耗时', dataIndex: 'avgMs', width: 200,
          render: (v) => (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{
                width: `${Math.round((v / maxMs) * 120)}px`, minWidth: 4,
                height: 8, background: '#1677ff', borderRadius: 4, flexShrink: 0,
              }} />
              <span>{fmtMs(v)}</span>
            </div>
          ),
          sorter: (a, b) => a.avgMs - b.avgMs,
        },
        {
          title: '最大耗时', dataIndex: 'maxMs', width: 100,
          render: v => <Tooltip title="最坏情况耗时"><span style={{ color: v > 10000 ? '#ff4d4f' : undefined }}>{fmtMs(v)}</span></Tooltip>,
        },
        { title: '调用次数', dataIndex: 'callCount', width: 80 },
        {
          title: '步骤成功率', dataIndex: 'successRate', width: 130,
          render: v => (
            <Progress percent={v} size="small"
              strokeColor={v >= 95 ? '#52c41a' : v >= 70 ? '#faad14' : '#ff4d4f'}
              format={p => `${p}%`}
            />
          ),
        },
      ]}
      style={{ background: '#fafafa' }}
    />
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
      .catch(() => setLoading(false))
  }, [days])

  const columns = [
    {
      title: '流程名称', dataIndex: 'flowType', ellipsis: true,
      render: v => <strong>{v}</strong>,
    },
    {
      title: '执行次数', dataIndex: 'totalRuns', width: 90,
      sorter: (a, b) => a.totalRuns - b.totalRuns,
    },
    {
      title: '平均总耗时', dataIndex: 'avgTotalMs', width: 130,
      render: v => <Tag color={v > 30000 ? 'red' : v > 5000 ? 'orange' : 'blue'}>{fmtMs(v)}</Tag>,
      sorter: (a, b) => a.avgTotalMs - b.avgTotalMs,
    },
    {
      title: '步骤数', width: 70,
      render: (_, r) => r.steps?.length ?? 0,
    },
  ]

  return (
    <Card
      title={<><ThunderboltOutlined className="mr-2" />性能统计</>}
      size="small"
      extra={
        <Select value={days} onChange={setDays} size="small" options={[
          { value: 1, label: '近1天' },
          { value: 7, label: '近7天' },
          { value: 30, label: '近30天' },
        ]} />
      }
    >
      <Spin spinning={loading}>
        <Table
          columns={columns}
          dataSource={stats}
          rowKey="flowType"
          size="small"
          pagination={false}
          expandable={{
            expandedRowRender: record => <StepTable steps={record.steps} />,
            rowExpandable: record => record.steps?.length > 0,
          }}
          locale={{ emptyText: '暂无性能数据，任务执行后将自动记录' }}
        />
      </Spin>
    </Card>
  )
}

import { Card, Table, Tag, Progress, Select, Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getTaskProfiles } from '@/apis'
import { DashboardOutlined } from '@ant-design/icons'

export const TaskProfilePanel = () => {
  const { t } = useTranslation()
  const [profiles, setProfiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)

  useEffect(() => {
    setLoading(true)
    getTaskProfiles(days).then(res => { const d = res?.data; setProfiles(Array.isArray(d) ? d : []); setLoading(false) }).catch(() => setLoading(false))
  }, [days])

  const columns = [
    { title: t('taskProfile.jobType'), dataIndex: 'jobType', ellipsis: true },
    { title: t('taskProfile.runs'), dataIndex: 'totalRuns', width: 80, sorter: (a, b) => a.totalRuns - b.totalRuns },
    {
      title: t('taskProfile.successRate'), dataIndex: 'successRate', width: 140,
      render: v => <Progress percent={v} size="small" strokeColor={v >= 80 ? '#52c41a' : v >= 50 ? '#faad14' : '#ff4d4f'} />,
      sorter: (a, b) => a.successRate - b.successRate,
    },
    {
      title: t('taskProfile.avgDur'), dataIndex: 'avgDurationSec', width: 100,
      render: v => v > 60 ? `${(v / 60).toFixed(1)}m` : `${v}s`,
      sorter: (a, b) => a.avgDurationSec - b.avgDurationSec,
    },
    {
      title: t('taskProfile.maxDur'), dataIndex: 'maxDurationSec', width: 100,
      render: v => v > 60 ? `${(v / 60).toFixed(1)}m` : `${v}s`,
    },
    {
      title: t('taskProfile.success'), dataIndex: 'successCount', width: 70,
      render: v => <Tag color="green">{v}</Tag>,
    },
    {
      title: t('taskProfile.fail'), dataIndex: 'failCount', width: 70,
      render: (v) => v > 0 ? <Tag color="red">{v}</Tag> : <Tag>{v}</Tag>,
    },
  ]

  return (
    <Card title={<><DashboardOutlined className="mr-2" />{t('taskProfile.title')}</>} size="small"
      extra={
        <Select value={days} onChange={setDays} size="small" options={[
          { value: 1, label: t('taskProfile.day1') },
          { value: 7, label: t('taskProfile.day7') },
          { value: 30, label: t('taskProfile.day30') },
        ]} />
      }>
      <Table columns={columns} dataSource={profiles} rowKey="jobType" size="small" pagination={false} loading={loading} />
    </Card>
  )
}

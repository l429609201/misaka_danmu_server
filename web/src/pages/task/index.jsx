import { ImportTask } from './components/ImportTask'
import { ScheduleTask } from './components/ScheduleTask'
import { RateLimitPanel } from './components/RateLimitPanel'
import { Tabs } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'

export const Task = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'task'

  const navigate = useNavigate()

  return (
    <Tabs
      defaultActiveKey={key}
      items={[
        {
          label: '进行中任务',
          key: 'task',
          children: <ImportTask />,
        },
        {
          label: '定时任务',
          key: 'schedule',
          children: <ScheduleTask />,
        },
        {
          label: '流控面板',
          key: 'ratelimit',
          children: <RateLimitPanel />,
        },
      ]}
      onChange={key => {
        navigate(`/task?key=${key}`, {
          replace: true,
        })
      }}
    />
  )
}

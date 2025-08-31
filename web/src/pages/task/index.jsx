import { useState } from 'react'
import { ImportTask } from './components/ImportTask'
import { ScheduleTask } from './components/ScheduleTask'
import { RateLimitPanel } from './components/RateLimitPanel'
import { Tabs } from 'antd'

export const Task = () => {
  const [activeKey, setActiveKey] = useState('task')
  return (
    <Tabs
      defaultActiveKey={activeKey}
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
      onChange={key => setActiveKey(key)}
    />
  )
}

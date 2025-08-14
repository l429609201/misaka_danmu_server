import { useState } from 'react'
import { ImportTask } from './components/importTask'
import { ScheduleTask } from './components/scheduleTask'
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
      ]}
      onChange={key => setActiveKey(key)}
    />
  )
}

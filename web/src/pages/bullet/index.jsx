import { useState } from 'react'
import { Tabs } from 'antd'
import { TokenManage } from './components/TokenManage'
import { OutputManage } from './components/OutputManage'

export const Bullet = () => {
  const [activeKey, setActiveKey] = useState('token')
  return (
    <Tabs
      defaultActiveKey={activeKey}
      items={[
        {
          label: 'Token管理',
          key: 'token',
          children: <TokenManage />,
        },
        {
          label: '弹幕输出控制',
          key: 'output',
          children: <OutputManage />,
        },
      ]}
      onChange={key => setActiveKey(key)}
    />
  )
}

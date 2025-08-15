import { useState } from 'react'
import { Tabs } from 'antd'
import { Scrapers } from './components/Scrapers'
import { Metadata } from './components/Metadata'

export const Source = () => {
  const [activeKey, setActiveKey] = useState('scrapers')
  return (
    <Tabs
      defaultActiveKey={activeKey}
      items={[
        {
          label: '弹幕搜索源',
          key: 'scrapers',
          children: <Scrapers></Scrapers>,
        },
        {
          label: '元信息搜索源',
          key: 'metadata',
          children: <Metadata></Metadata>,
        },
      ]}
      onChange={key => setActiveKey(key)}
    />
  )
}

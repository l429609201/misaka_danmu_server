import { useState } from 'react'
import { Tabs } from 'antd'
import { Scrapers } from './components/Scrapers'
import { Metadata } from './components/Metadata'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { GlobalFilter } from './components/GlobalFilter'

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
        {
          label: '设置',
          key: 'global-filter',
          children: <GlobalFilter />,
        },
      ]}
      onChange={key => setActiveKey(key)}
    />
  )
}

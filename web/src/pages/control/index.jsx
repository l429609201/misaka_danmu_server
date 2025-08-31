import { useState } from 'react'
import { Tabs } from 'antd'
import { ApiKey } from './components/ApiKey'
import { ApiDoc } from './components/ApiDoc'
import { ApiLogs } from './components/ApiLogs'

export const Control = () => {
  const [activeKey, setActiveKey] = useState('apikey')
  return (
    <Tabs
      defaultActiveKey={activeKey}
      items={[
        {
          label: 'API密钥',
          key: 'apikey',
          children: <ApiKey />,
        },
        {
          label: 'API访问日志',
          key: 'apilogs',
          children: <ApiLogs />,
        },
        {
          label: 'API文档',
          key: 'apidoc',
          children: <ApiDoc />,
        },
      ]}
      onChange={key => setActiveKey(key)}
    />
  )
}

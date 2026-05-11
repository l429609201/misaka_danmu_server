import { Tabs } from 'antd'
import { ApiKey } from './components/ApiKey'
import { ApiDoc } from './components/ApiDoc'
import { ApiLogs } from './components/ApiLogs'
import { McpInfo } from './components/McpInfo'
import { Settings } from './components/Settings'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'

export const Control = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'apikey'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    {
      label: 'API密钥',
      key: 'apikey',
      children: <ApiKey />,
    },
    {
      label: '设置',
      key: 'settings',
      children: <Settings />,
    },
    {
      label: 'API访问日志',
      key: 'apilogs',
      children: <ApiLogs />,
    },
    {
      label: 'MCP',
      key: 'mcp',
      children: <McpInfo />,
    },
    {
      label: 'API文档',
      key: 'apidoc',
      children: <ApiDoc />,
    },
  ]

  const handleTabChange = (newKey) => {
    navigate(`/control?key=${newKey}`, {
      replace: true,
    })
  }

  return (
    <div className="my-6">
      {isMobile ? (
        <MobileTabs
          items={tabItems}
          defaultActiveKey={key}
          onChange={handleTabChange}
        />
      ) : (
        <Tabs
          activeKey={key}
          items={tabItems}
          onChange={handleTabChange}
        />
      )}
    </div>
  )
}

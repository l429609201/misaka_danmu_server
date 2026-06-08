import { Tabs } from 'antd'
import { ApiKey } from './components/ApiKey'
import { ApiDoc } from './components/ApiDoc'
import { ApiLogs } from './components/ApiLogs'
import { McpInfo } from './components/McpInfo'
import { Settings } from './components/Settings'
import { DiagnosticsPanel } from './components/DiagnosticsPanel'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { useTranslation } from 'react-i18next'

export const Control = () => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'apikey'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    {
      label: t('control.tabApiKey'),
      key: 'apikey',
      children: <ApiKey />,
    },
    {
      label: t('control.tabSettings'),
      key: 'settings',
      children: <Settings />,
    },
    {
      label: t('control.tabApiLogs'),
      key: 'apilogs',
      children: <ApiLogs />,
    },
    {
      label: 'MCP',
      key: 'mcp',
      children: <McpInfo />,
    },
    {
      label: t('control.tabApiDoc'),
      key: 'apidoc',
      children: <ApiDoc />,
    },
    {
      label: t('control.tabDiagnostics'),
      key: 'diagnostics',
      children: <DiagnosticsPanel />,
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

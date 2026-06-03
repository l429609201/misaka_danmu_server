import { Card, Collapse, Typography, Alert } from 'antd'
import { ApiOutlined, ThunderboltOutlined, SafetyOutlined, ToolOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

export const McpInfo = () => {
  const { t } = useTranslation()
  const infoCardStyle = { height: '100%', width: '100%', display: 'flex', flexDirection: 'column' }
  const infoCardBodyStyle = {
    flex: 1,
    height: '100%',
    minHeight: 0,
    padding: '12px 16px',
    boxSizing: 'border-box',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  }


  return (
    <div className="my-6 space-y-4">
      <Card title="MCP Server" size="small">
        <Alert
          type="info"
          showIcon
          icon={<ApiOutlined />}
          message="Model Context Protocol (MCP)"
          description={t('control.mcpDescription')}
          className="mb-6"
        />

        <div className="flex flex-col md:flex-row gap-4 mb-4 mt-2 items-start">
          <div className="w-full md:flex-1" style={{ height: 108 }}>
            <Card size="small" className="text-center" style={infoCardStyle} bodyStyle={infoCardBodyStyle} styles={{ body: infoCardBodyStyle }}>
              <div className="flex items-center justify-center h-8">
                <ThunderboltOutlined className="text-2xl text-blue-500" />
              </div>
              <div className="text-sm font-medium leading-5 h-5">{t('control.mcpTransport')}</div>
              <div className="text-xs text-gray-500 leading-5 h-5">Streamable HTTP</div>
            </Card>
          </div>
          <div className="w-full md:flex-1" style={{ height: 108 }}>
            <Card size="small" className="text-center" style={infoCardStyle} bodyStyle={infoCardBodyStyle} styles={{ body: infoCardBodyStyle }}>
              <div className="flex items-center justify-center h-8">
                <SafetyOutlined className="text-2xl text-green-500" />
              </div>
              <div className="text-sm font-medium leading-5 h-5">{t('control.mcpAuth')}</div>
              <div className="text-xs text-gray-500 leading-5 h-5">{t('control.mcpAuthValue')}</div>
            </Card>
          </div>
          <div className="w-full md:flex-1" style={{ height: 108 }}>
            <Card size="small" className="text-center" style={infoCardStyle} bodyStyle={infoCardBodyStyle} styles={{ body: infoCardBodyStyle }}>
              <div className="flex items-center justify-center h-8">
                <ToolOutlined className="text-2xl text-orange-500" />
              </div>
              <div className="text-sm font-medium leading-5 h-5">{t('control.mcpEndpoint')}</div>
              <code className="text-xs leading-5 h-5 px-1 rounded bg-black/5 dark:bg-white/10">/api/mcp</code>
            </Card>
          </div>
        </div>
      </Card>

      <Card title={t('control.mcpClientConfig')} size="small">
        <div className="mb-2 text-sm text-gray-600 dark:text-gray-400">
          {t('control.mcpClientConfigDesc')}
        </div>
        <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-3 overflow-x-auto whitespace-pre-wrap break-all m-0">
{`{
  "mcpServers": {
    "misaka-danmu": {
      "type": "http",
      "url": "${t('control.mcpUrlPlaceholder')}",
      "headers": {
        "X-API-KEY": "${t('control.mcpKeyPlaceholder')}"
      }
    }
  }
}`}
        </pre>
      </Card>

      <Card title={t('control.mcpToolsTitle')} size="small">
        <div className="mb-2 text-sm text-gray-600 dark:text-gray-400">
          {t('control.mcpToolsDesc')}
        </div>
        <Collapse size="small" items={[
          {
            key: 'search-import',
            label: t('control.mcpCatSearch'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatSearch1')}</li>
                <li>{t('control.mcpCatSearch2')}</li>
                <li>{t('control.mcpCatSearch3')}</li>
                <li>{t('control.mcpCatSearch4')}</li>
              </ul>
            ),
          },
          {
            key: 'library',
            label: t('control.mcpCatLibrary'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatLibrary1')}</li>
                <li>{t('control.mcpCatLibrary2')}</li>
                <li>{t('control.mcpCatLibrary3')}</li>
                <li>{t('control.mcpCatLibrary4')}</li>
              </ul>
            ),
          },
          {
            key: 'token',
            label: t('control.mcpCatToken'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatToken1')}</li>
                <li>{t('control.mcpCatToken2')}</li>
                <li>{t('control.mcpCatToken3')}</li>
              </ul>
            ),
          },
          {
            key: 'task',
            label: t('control.mcpCatTask'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatTask1')}</li>
                <li>{t('control.mcpCatTask2')}</li>
                <li>{t('control.mcpCatTask3')}</li>
              </ul>
            ),
          },
          {
            key: 'config',
            label: t('control.mcpCatConfig'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatConfig1')}</li>
                <li>{t('control.mcpCatConfig2')}</li>
                <li>{t('control.mcpCatConfig3')}</li>
              </ul>
            ),
          },
          {
            key: 'logs',
            label: t('control.mcpCatLog'),
            children: (
              <ul className="text-xs space-y-1 list-disc pl-4 m-0">
                <li>{t('control.mcpCatLog1')}</li>
                <li>{t('control.mcpCatLog2')}</li>
                <li>{t('control.mcpCatLog3')}</li>
              </ul>
            ),
          },
        ]} />
      </Card>

      <Card size="small">
        <div className="text-xs text-gray-500 dark:text-gray-400">
          <strong>{t('control.mcpTipPrefix')}</strong>{t('control.mcpTipContent')} <Typography.Text code>MCP:</Typography.Text> {t('control.mcpTipSuffix')}
        </div>
      </Card>
    </div>
  )
}

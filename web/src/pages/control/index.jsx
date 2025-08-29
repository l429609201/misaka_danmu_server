import { Tabs } from 'antd'
import { ApiKey } from './components/ApiKey'
import { ApiDoc } from './components/ApiDoc'
import { ApiLogs } from './components/ApiLogs'
import { useNavigate, useSearchParams } from 'react-router-dom'

export const Control = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'apikey'
  const navigate = useNavigate()

  return (
    <Tabs
      defaultActiveKey={key}
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
      onChange={key => {
        navigate(`/control?key=${key}`, {
          replace: true,
        })
      }}
    />
  )
}

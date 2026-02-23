import { Tabs } from 'antd'
import { Webhook } from './components/Webhook'
import { Proxy } from './components/Proxy'
import { Parameters } from './components/Parameters'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Recognition } from './components/Recognition'
import { Notification } from './components/Notification'
import AutoMatchSetting from './components/AutoMatchSetting'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store'

export const Setting = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'parameters'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    {
      label: '参数配置',
      key: 'parameters',
      children: <Parameters />,
    },
    {
      label: '代理设置',
      key: 'proxy',
      children: <Proxy />,
    },
    {
      label: 'Webhook',
      key: 'webhook',
      children: <Webhook />,
    },
    {
      label: '通知与交互',
      key: 'notification',
      children: <Notification />,
    },
    {
      label: '识别词配置',
      key: 'recognition',
      children: <Recognition />,
    },
    {
      label: 'AI辅助增强',
      key: 'automatch',
      children: <AutoMatchSetting />,
    },
  ]

  const handleTabChange = (newKey) => {
    navigate(`/setting?key=${newKey}`, {
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

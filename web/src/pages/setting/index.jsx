import { Tabs } from 'antd'
import { Webhook } from './components/Webhook'
import { Proxy } from './components/Proxy'
import { Parameters } from './components/Parameters'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Recognition } from './components/Recognition'
import { Notification } from './components/Notification'
import AutoMatchSetting from './components/AutoMatchSetting'
import Security from './components/Security'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store'
import { useTranslation } from 'react-i18next'

export const Setting = () => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'parameters'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    { label: t('settingPage.parameters'), key: 'parameters', children: <Parameters /> },
    { label: t('settingPage.proxy'), key: 'proxy', children: <Proxy /> },
    { label: t('settingPage.webhook'), key: 'webhook', children: <Webhook /> },
    { label: t('settingPage.notification'), key: 'notification', children: <Notification /> },
    { label: t('settingPage.recognition'), key: 'recognition', children: <Recognition /> },
    { label: t('settingPage.automatch'), key: 'automatch', children: <AutoMatchSetting /> },
    { label: t('settingPage.security'), key: 'security', children: <Security /> },
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

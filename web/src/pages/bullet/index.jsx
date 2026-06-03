import { Tabs } from 'antd'
import { TokenManage } from './components/TokenManage'
import { OutputManage } from './components/OutputManage'
import { MatchFallbackSetting } from './components/MatchFallbackSetting'
import DanmakuStorage from '../setting/components/DanmakuStorage'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { useTranslation } from 'react-i18next'

export const Bullet = () => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'token'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    {
      label: t('bullet.tabToken'),
      key: 'token',
      children: <TokenManage />,
    },
    {
      label: t('bullet.tabOutput'),
      key: 'output',
      children: <OutputManage />,
    },
    {
      label: t('bullet.tabStorage'),
      key: 'storage',
      children: <DanmakuStorage />,
    },
    {
      label: t('bullet.tabFallback'),
      key: 'fallback',
      children: <MatchFallbackSetting />,
    },
  ]

  const handleTabChange = (newKey) => {
    navigate(`/bullet?key=${newKey}`, {
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

import { Tabs } from 'antd'
import { Scrapers } from './components/Scrapers'
import { Metadata } from './components/Metadata'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { GlobalFilter } from './components/GlobalFilter'
import { MobileTabs } from '@/components/MobileTabs'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { useTranslation } from 'react-i18next'

export const Source = () => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'scrapers'
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)

  const tabItems = [
    {
      label: t('sourcePage.danmakuSource'),
      key: 'scrapers',
      children: <Scrapers></Scrapers>,
    },
    {
      label: t('sourcePage.metadataSource'),
      key: 'metadata',
      children: <Metadata></Metadata>,
    },
    {
      label: t('sourcePage.settings'),
      key: 'global-filter',
      children: <GlobalFilter />,
    },
  ]

  const handleTabChange = (newKey) => {
    navigate(`/source?key=${newKey}`, {
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

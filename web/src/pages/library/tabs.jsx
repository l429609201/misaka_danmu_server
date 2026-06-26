import { Tabs } from 'antd'
import { useAtomValue } from 'jotai'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { isMobileAtom } from '../../../store/index.js'
import { MobileTabs } from '@/components/MobileTabs'
import { RoutePaths } from '../../general/RoutePaths'
import { Library } from './index.jsx'
import { BatchManagePage } from './batch-manage.jsx'
import { SubscriptionPage } from '../subscription/index.jsx'

export const LibraryTabsPage = () => {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  // 三态：library / batch / subscriptions
  const key = location.pathname === RoutePaths.BATCH_MANAGE
    ? 'batch'
    : location.pathname === RoutePaths.SUBSCRIPTIONS
      ? 'subscriptions'
      : 'library'

  const tabItems = [
    {
      label: t('libraryPage.pageTitle'),
      key: 'library',
      children: <Library />,
    },
    {
      label: t('libraryPage.btnBatchManage'),
      key: 'batch',
      children: <BatchManagePage />,
    },
    {
      label: t('subscription.title', '订阅'),
      key: 'subscriptions',
      children: <SubscriptionPage />,
    },
  ]

  const handleTabChange = (newKey) => {
    const pathMap = {
      batch: RoutePaths.BATCH_MANAGE,
      subscriptions: RoutePaths.SUBSCRIPTIONS,
      library: RoutePaths.LIBRARY,
    }
    navigate(pathMap[newKey] || RoutePaths.LIBRARY)
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

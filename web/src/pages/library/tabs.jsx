import { Tabs } from 'antd'
import { useAtomValue } from 'jotai'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { isMobileAtom } from '../../../store/index.js'
import { MobileTabs } from '@/components/MobileTabs'
import { RoutePaths } from '../../general/RoutePaths'
import { Library } from './index.jsx'
import { BatchManagePage } from './batch-manage.jsx'

export const LibraryTabsPage = () => {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const key = location.pathname === RoutePaths.BATCH_MANAGE ? 'batch' : 'library'

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
  ]

  const handleTabChange = (newKey) => {
    navigate(newKey === 'batch' ? RoutePaths.BATCH_MANAGE : RoutePaths.LIBRARY)
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

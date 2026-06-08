import { Tabs } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import LibraryScan from './components/LibraryScan';
import LocalScan from './components/LocalScan';
import { MobileTabs } from '@/components/MobileTabs';
import { useAtomValue } from 'jotai';
import { isMobileAtom } from '../../../store/index.js';

const MediaFetch = () => {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const activeTab = searchParams.get('key') || 'library-scan';
  const navigate = useNavigate();
  const isMobile = useAtomValue(isMobileAtom);

  const handleTabChange = (newKey) => {
    navigate(`/media-fetch?key=${newKey}`, { replace: true });
  };

  const items = [
    {
      key: 'library-scan',
      label: t('mediaFetch.libraryScanTab'),
      children: <LibraryScan />,
    },
    {
      key: 'local-scan',
      label: t('mediaFetch.localScanTab'),
      children: <LocalScan />,
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      <div className="my-6">
        {isMobile ? (
          <MobileTabs
            items={items}
            defaultActiveKey={activeTab}
            onChange={handleTabChange}
          />
        ) : (
          <Tabs
            activeKey={activeTab}
            onChange={handleTabChange}
            items={items}
          />
        )}
      </div>
    </div>
  );
};

export default MediaFetch;
import { useState } from 'react';
import { Tabs } from 'antd';
import { useTranslation } from 'react-i18next';
import LibraryScan from './components/LibraryScan';
import LocalScan from './components/LocalScan';
import { MobileTabs } from '@/components/MobileTabs';
import { useAtomValue } from 'jotai';
import { isMobileAtom } from '../../../store/index.js';

const MediaFetch = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('library-scan');
  const isMobile = useAtomValue(isMobileAtom);

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
            onChange={setActiveTab}
          />
        ) : (
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={items}
          />
        )}
      </div>
    </div>
  );
};

export default MediaFetch;
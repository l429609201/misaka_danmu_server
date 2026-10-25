import { Tabs } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import LibraryScan from './components/LibraryScan';
import LocalScan from './components/LocalScan';
import { Webhook } from '../setting/components/Webhook';
import { MobileTabs } from '@/components/MobileTabs';
import { useAtomValue } from 'jotai';
import { isMobileAtom } from '../../../store/index.js';

// 媒体服务联动：连接配置 / Webhook / 媒体服务扫描 / 本地扫描
const TAB_KEYS = ['connection', 'webhook', 'library-scan', 'local-scan'];

const MediaFetch = () => {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const rawTab = searchParams.get('key');
  const activeTab = TAB_KEYS.includes(rawTab) ? rawTab : 'connection';
  const navigate = useNavigate();
  const isMobile = useAtomValue(isMobileAtom);

  const handleTabChange = (newKey) => {
    navigate(`/media-fetch?key=${newKey}`, { replace: true });
  };

  const items = [
    {
      key: 'connection',
      label: t('mediaFetch.connectionTab'),
      children: <LibraryScan mode="connection" />,
    },
    {
      key: 'webhook',
      label: t('mediaFetch.webhookTab'),
      children: <Webhook />,
    },
    {
      key: 'library-scan',
      label: t('mediaFetch.libraryScanTab'),
      children: <LibraryScan mode="scan" />,
    },
    {
      key: 'local-scan',
      label: t('mediaFetch.localScanTab'),
      children: <LocalScan />,
    },
  ];

  // 与设置、弹幕库等页面保持同样的外层间距，卡片宽度随内容区铺满
  return (
    <div className="my-6">
      {isMobile ? (
        <MobileTabs
          className="page-tabs"
          items={items}
          defaultActiveKey={activeTab}
          onChange={handleTabChange}
        />
      ) : (
        <Tabs
          className="page-tabs"
          activeKey={activeTab}
          onChange={handleTabChange}
          items={items}
          // 切换 Tab 时重新挂载，保证在连接配置里增删服务器后，扫描页拿到最新列表
          destroyOnHidden
        />
      )}
    </div>
  );
};

export default MediaFetch;

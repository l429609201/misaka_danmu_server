import { useState, useEffect } from 'react';
import { Card, Input, Button, message, Space } from 'antd';
import { ScanOutlined, FolderOpenOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import LocalItemList from './LocalItemList';
import DirectoryBrowser from './DirectoryBrowser';
import { scanLocalDanmaku, getLastScanPath, saveScanPath } from '../../../apis';

const LocalScan = () => {
  const { t } = useTranslation();
  const [scanPath, setScanPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [browserVisible, setBrowserVisible] = useState(false);

  // 组件加载时获取上次使用的路径
  useEffect(() => {
    loadLastPath();
  }, []);

  const loadLastPath = async () => {
    try {
      const response = await getLastScanPath();
      if (response.data.path) {
        setScanPath(response.data.path);
      }
    } catch (error) {
      console.error(t('mediaFetch.localScan.loadLastPathFailed'), error);
    }
  };

  // 扫描本地弹幕
  const handleScan = async () => {
    if (!scanPath) {
      message.warning(t('mediaFetch.localScan.selectPathWarning'));
      return;
    }

    // 验证路径是否合理
    if (scanPath.includes('node_modules') || scanPath.includes('.git') || scanPath.includes('cache') || scanPath.includes('temp')) {
      message.warning(t('mediaFetch.localScan.systemDirWarning'));
      return;
    }

    setLoading(true);
    try {
      message.info(t('mediaFetch.localScan.scanStarted'));
      const res = await scanLocalDanmaku(scanPath);
      message.success(res.data.message || t('mediaFetch.localScan.scanComplete'));
      // 触发列表刷新
      setRefreshTrigger(prev => prev + 1);
    } catch (error) {
      message.error(t('mediaFetch.localScan.scanFailed') + (error.message || t('mediaFetch.localScan.unknownError')));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  // 打开目录浏览器
  const handleBrowse = () => {
    setBrowserVisible(true);
  };

  // 选择目录
  const handleSelectDirectory = async (path) => {
    setScanPath(path);
    // 自动保存路径
    try {
      await saveScanPath(path);
      message.success(t('mediaFetch.localScan.directorySelected', { path }));
    } catch (error) {
      console.error(t('mediaFetch.localScan.savePathFailed'), error);
      message.success(t('mediaFetch.localScan.directorySelected', { path }));  // 即使保存失败也显示选择成功
    }
  };

  return (
    <div style={{ padding: '8px' }}> {/* 添加移动端内边距 */}
      <Card
        title={<span style={{ fontSize: '16px' }}>{t('mediaFetch.localScan.cardTitle')}</span>}
        extra={
          <Button
            type="primary"
            icon={<ScanOutlined />}
            loading={loading}
            onClick={handleScan}
          >
            {t('mediaFetch.localScan.scan')}
          </Button>
        }
        style={{ marginBottom: '16px' }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="small"> {/* 减小间距 */}
          <div>
            <div style={{ marginBottom: '4px', color: '#666', fontSize: '14px' }}> {/* 调整字体大小 */}
              {t('mediaFetch.localScan.scanPathLabel')}
            </div>
            <div style={{ display: 'flex', gap: '8px' }}>
              <Input
                placeholder={t('mediaFetch.localScan.pathPlaceholder')}
                value={scanPath}
                onChange={(e) => setScanPath(e.target.value)}
                style={{ flex: 1 }}
              />
              <Button
                icon={<FolderOpenOutlined />}
                onClick={handleBrowse}
              >
                {t('mediaFetch.localScan.browse')}
              </Button>
            </div>
          </div>

          <div style={{ fontSize: '12px', color: '#999' }}>
            <div>{t('mediaFetch.localScan.supportedStructures')}</div>
            <div>{t('mediaFetch.localScan.structure1')}</div>
            <div>{t('mediaFetch.localScan.structure2')}</div>
          </div>
        </Space>
      </Card>

      <LocalItemList refreshTrigger={refreshTrigger} />

      <DirectoryBrowser
        visible={browserVisible}
        onClose={() => setBrowserVisible(false)}
        onSelect={handleSelectDirectory}
      />
    </div>
  );
};

export default LocalScan;


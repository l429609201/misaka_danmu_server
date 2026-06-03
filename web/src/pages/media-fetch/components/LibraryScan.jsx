import { useState, useEffect } from 'react';
import { Card, Select, Button, message, Space, Checkbox, Row, Col, Tag, Divider, Typography, Alert, Popconfirm, Grid, Segmented, InputNumber, Popover, Modal } from 'antd';
import { CalendarOutlined } from '@ant-design/icons';
import { ReloadOutlined, PlusOutlined, ScanOutlined, SettingOutlined, SaveOutlined, DatabaseOutlined, DeleteOutlined, ImportOutlined, EyeOutlined, EyeInvisibleOutlined, VideoCameraOutlined, PlaySquareOutlined, EditOutlined, CloudDownloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import ServerConfigPanel from './ServerConfigPanel';
import MediaItemList from './MediaItemList';
import { getMediaServers, scanMediaServer, getMediaServerLibraries, updateMediaServer, batchDeleteMediaItems, importMediaItems, deleteMediaServer, getUnimportedCount, importAllUnimported } from '../../../apis';

const { Option } = Select;
const { Title, Text } = Typography;

const LibraryScan = () => {
  const { t } = useTranslation();
  const [servers, setServers] = useState([]);
  const [selectedServerId, setSelectedServerId] = useState(null);
  const [libraries, setLibraries] = useState([]);
  const [selectedLibraryIds, setSelectedLibraryIds] = useState([]);
  const [loadingLibraries, setLoadingLibraries] = useState(false);
  const [loading, setLoading] = useState(false);
  const [savingLibraries, setSavingLibraries] = useState(false);
  const [configModalVisible, setConfigModalVisible] = useState(false);
  const [editingServer, setEditingServer] = useState(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [selectedMediaItems, setSelectedMediaItems] = useState([]);
  const [showServerUrl, setShowServerUrl] = useState(false);
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all'); // 添加类型过滤状态
  const [yearFrom, setYearFrom] = useState();
  const [yearTo, setYearTo] = useState();

  const screens = Grid.useBreakpoint();

  // 加载服务器列表
  const loadServers = async () => {
    setLoading(true);
    try {
      const res = await getMediaServers();
      const data = res.data;
      setServers(data);

      // 如果有启用的服务器且没有选中,自动选中第一个
      if (!selectedServerId && data.length > 0) {
        const enabledServer = data.find(s => s.isEnabled);
        if (enabledServer) {
          setSelectedServerId(enabledServer.id);
        }
      }
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.loadServersFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadServers();
  }, []);

  // 当选中的服务器变化时,加载媒体库列表
  useEffect(() => {
    if (selectedServerId) {
      // 检查服务器是否启用
      const currentServer = servers.find(s => s.id === selectedServerId);
      if (currentServer && currentServer.isEnabled) {
        loadLibraries();
      } else {
        setLibraries([]);
        setSelectedLibraryIds([]);
      }
    } else {
      setLibraries([]);
      setSelectedLibraryIds([]);
    }
  }, [selectedServerId, servers]);

  // 确保至少选择一个媒体库
  useEffect(() => {
    if (libraries.length > 0 && selectedLibraryIds.length === 0 && !loadingLibraries) {
      // 如果没有选择任何媒体库，默认选中第一个
      setSelectedLibraryIds([libraries[0].id]);
    }
  }, [libraries, selectedLibraryIds, loadingLibraries]);

  // 加载媒体库列表
  const loadLibraries = async () => {
    if (!selectedServerId) return;

    setLoadingLibraries(true);
    try {
      const res = await getMediaServerLibraries(selectedServerId);
      const data = res.data;
      setLibraries(data);

      // 从服务器配置中读取已选择的媒体库
      const currentServer = servers.find(s => s.id === selectedServerId);
      if (currentServer && currentServer.selectedLibraries && currentServer.selectedLibraries.length > 0) {
        // 过滤掉不存在的媒体库ID
        const validSelectedLibraries = currentServer.selectedLibraries.filter(id =>
          data.some(lib => lib.id === id)
        );
        setSelectedLibraryIds(validSelectedLibraries.length > 0 ? validSelectedLibraries : [data[0]?.id].filter(Boolean));
      } else {
        // 如果没有配置,默认选中第一个媒体库
        setSelectedLibraryIds(data.length > 0 ? [data[0].id] : []);
      }
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.loadLibrariesFailed'));
      console.error(error);
      setLibraries([]);
      setSelectedLibraryIds([]);
    } finally {
      setLoadingLibraries(false);
    }
  };

  // 保存媒体库选择
  const handleSaveLibraries = async () => {
    if (!selectedServerId) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoServer'));
      return;
    }

    setSavingLibraries(true);
    try {
      await updateMediaServer(selectedServerId, {
        selectedLibraries: selectedLibraryIds
      });
      message.success(t('mediaFetch.libraryScan.saveLibrariesSuccess'));
      // 重新加载服务器列表以更新配置
      await loadServers();
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.saveFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    } finally {
      setSavingLibraries(false);
    }
  };

  // 扫描媒体库
  const handleScan = async () => {
    if (!selectedServerId) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoServer'));
      return;
    }

    // 检查是否有有效的媒体库选择
    const validSelections = selectedLibraryIds.filter(id => libraries.some(lib => lib.id === id));
    if (validSelections.length === 0) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoLibraryShort'));
      // 自动选择第一个有效的媒体库
      if (libraries.length > 0) {
        setSelectedLibraryIds([libraries[0].id]);
      }
      return;
    }

    setLoading(true);
    try {
      const res = await scanMediaServer(selectedServerId, validSelections);
      const result = res.data;
      message.success(result.message || t('mediaFetch.libraryScan.scanSubmitted'));
      // 触发列表刷新
      setRefreshTrigger(prev => prev + 1);
    } catch (error) {
      // axios拦截器已统一转换为message字段
      message.error(t('mediaFetch.libraryScan.scanFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  // 打开配置面板
  const handleAddServer = () => {
    setEditingServer(null);
    setConfigModalVisible(true);
  };

  const handleEditServer = () => {
    if (!selectedServerId) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoServer'));
      return;
    }
    const server = servers.find(s => s.id === selectedServerId);
    setEditingServer(server);
    setConfigModalVisible(true);
  };

  const handleConfigSaved = () => {
    setConfigModalVisible(false);
    loadServers();
  };

  // 删除服务器
  const handleDeleteServer = async () => {
    if (!selectedServerId) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoServer'));
      return;
    }

    try {
      await deleteMediaServer(selectedServerId);
      message.success(t('mediaFetch.libraryScan.serverDeleted'));
      setSelectedServerId(null);
      // 重新加载服务器列表
      await loadServers();
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.deleteServerFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    }
  };

  // 批量删除媒体项目
  const handleBatchDelete = async () => {
    if (selectedMediaItems.length === 0) {
      message.warning(t('mediaFetch.libraryScan.selectDeleteWarning'));
      return;
    }

    // 分类收集要删除的项目
    const itemIds = [];
    const shows = [];
    const seasons = [];

    // 解析选中的项目key
    selectedMediaItems.forEach(key => {
      // 如果key是数字,说明是电影的id
      if (typeof key === 'number') {
        itemIds.push(key);
        return;
      }

      // 如果key是字符串
      if (typeof key === 'string') {
        if (key.startsWith('movie-') || key.startsWith('episode-')) {
          // 直接删除的电影或剧集
          itemIds.push(parseInt(key.split('-')[1]));
        } else if (key.startsWith('show-')) {
          // 整个剧集组
          const title = key.substring(5); // 移除 'show-' 前缀
          shows.push({
            serverId: selectedServerId,
            title: title
          });
        } else if (key.startsWith('season-')) {
          // 某一季
          // key格式: season-{title}-S{season}
          const parts = key.substring(7); // 移除 'season-' 前缀
          const lastDashIndex = parts.lastIndexOf('-S');
          if (lastDashIndex > 0) {
            const title = parts.substring(0, lastDashIndex);
            const season = parseInt(parts.substring(lastDashIndex + 2));
            seasons.push({
              serverId: selectedServerId,
              title: title,
              season: season
            });
          }
        }
      }
    });

    if (itemIds.length === 0 && shows.length === 0 && seasons.length === 0) {
      message.warning(t('mediaFetch.libraryScan.noDeletableItems'));
      return;
    }

    try {
      const payload = {};
      if (itemIds.length > 0) payload.itemIds = itemIds;
      if (shows.length > 0) payload.shows = shows;
      if (seasons.length > 0) payload.seasons = seasons;

      await batchDeleteMediaItems(payload);
      message.success(t('mediaFetch.libraryScan.batchDeleteSuccess', { count: selectedMediaItems.length }));
      setSelectedMediaItems([]);
      // 触发列表刷新
      setRefreshTrigger(prev => prev + 1);
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.batchDeleteFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    }
  };

  // 批量导入媒体项目
  const handleImport = async () => {
    if (selectedMediaItems.length === 0) {
      message.warning(t('mediaFetch.libraryScan.selectImportWarning'));
      return;
    }

    // 分类收集要导入的项目
    const itemIds = [];
    const shows = [];
    const seasons = [];

    // 解析选中的项目key
    selectedMediaItems.forEach(key => {
      // 如果key是数字,说明是电影的id
      if (typeof key === 'number') {
        itemIds.push(key);
        return;
      }

      // 如果key是字符串
      if (typeof key === 'string') {
        if (key.startsWith('movie-') || key.startsWith('episode-')) {
          // 直接导入的电影或剧集
          itemIds.push(parseInt(key.split('-')[1]));
        } else if (key.startsWith('show-')) {
          // 整个剧集组
          const title = key.substring(5); // 移除 'show-' 前缀
          shows.push({
            serverId: selectedServerId,
            title: title
          });
        } else if (key.startsWith('season-')) {
          // 某一季
          // key格式: season-{title}-S{season}
          const parts = key.substring(7); // 移除 'season-' 前缀
          const lastDashIndex = parts.lastIndexOf('-S');
          if (lastDashIndex > 0) {
            const title = parts.substring(0, lastDashIndex);
            const season = parseInt(parts.substring(lastDashIndex + 2));
            seasons.push({
              serverId: selectedServerId,
              title: title,
              season: season
            });
          }
        }
      }
    });

    if (itemIds.length === 0 && shows.length === 0 && seasons.length === 0) {
      message.warning(t('mediaFetch.libraryScan.noImportableItems'));
      return;
    }

    try {
      const payload = {};
      if (itemIds.length > 0) payload.itemIds = itemIds;
      if (shows.length > 0) payload.shows = shows;
      if (seasons.length > 0) payload.seasons = seasons;

      const res = await importMediaItems(payload);
      const result = res.data;
      message.success(result.message || t('mediaFetch.libraryScan.importSubmitted'));
      setSelectedMediaItems([]);
      // 触发列表刷新
      setRefreshTrigger(prev => prev + 1);
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.batchImportFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    }
  };

  // 一键导入全部未导入
  const handleImportAllUnimported = async () => {
    if (!selectedServerId) {
      message.warning(t('mediaFetch.libraryScan.scanTipNoServer'));
      return;
    }

    try {
      // 先获取未导入数量
      const countRes = await getUnimportedCount(selectedServerId);
      const count = countRes.data.count;

      if (count === 0) {
        message.info(t('mediaFetch.libraryScan.noUnimported'));
        return;
      }

      // 弹出确认框
      Modal.confirm({
        title: t('mediaFetch.libraryScan.importAllTitle'),
        content: t('mediaFetch.libraryScan.importAllContent', { count }),
        okText: t('mediaFetch.libraryScan.confirmImport'),
        cancelText: t('mediaFetch.libraryScan.cancel'),
        onOk: async () => {
          try {
            const res = await importAllUnimported({ serverId: selectedServerId });
            message.success(res.data.message || t('mediaFetch.libraryScan.importSubmitted'));
            setRefreshTrigger(prev => prev + 1);
          } catch (error) {
            message.error(t('mediaFetch.libraryScan.importFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
            console.error(error);
          }
        }
      });
    } catch (error) {
      message.error(t('mediaFetch.libraryScan.getUnimportedFailed') + (error.message || t('mediaFetch.libraryScan.unknownError')));
      console.error(error);
    }
  };

  const currentServer = servers.find(s => s.id === selectedServerId);
  const isServerDisabled = currentServer && !currentServer.isEnabled;

  return (
    <div
      style={{
        maxWidth: '1200px',
        margin: '0 auto',
        padding: '20px'
      }}
      className="mobile-reduced-padding"
    >
      {/* 页面标题 */}
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <Title level={2} style={{ marginBottom: '8px' }}>
          <DatabaseOutlined style={{ marginRight: '12px' }} />
          {t('mediaFetch.libraryScan.pageTitle')}
        </Title>
        <Text type="secondary">{t('mediaFetch.libraryScan.pageSubtitle')}</Text>
      </div>

      {/* 服务器配置卡片 */}
      <Card
        title={
          <Space>
            <SettingOutlined />
            <span>{t('mediaFetch.libraryScan.serverConfig')}</span>
          </Space>
        }
        style={{ marginBottom: '24px' }}
        extra={
          screens.xs ? null : (
            <Space>
              <Button
                icon={<PlusOutlined />}
                onClick={handleAddServer}
              >
                {t('mediaFetch.libraryScan.addServer')}
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={loadServers}
                loading={loading}
              >
                {t('mediaFetch.libraryScan.refresh')}
              </Button>
            </Space>
          )
        }
      >
        {screens.xs && (
          <div style={{ marginBottom: '16px', textAlign: 'center' }}>
            <Space>
              <Button
                icon={<PlusOutlined />}
                onClick={handleAddServer}
                size="large"
              >
                {t('mediaFetch.libraryScan.add')}
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={loadServers}
                loading={loading}
                size="large"
              >
                {t('mediaFetch.libraryScan.refresh')}
              </Button>
            </Space>
          </div>
        )}
        <Row gutter={24}>
          <Col xs={24} md={12}>
            <div style={{ marginBottom: '16px' }}>
              <Text strong style={{ display: 'block', marginBottom: '8px' }}>
                {t('mediaFetch.libraryScan.selectServer')}
              </Text>
              <Select
                style={{ width: '100%' }}
                placeholder={t('mediaFetch.libraryScan.selectServerPlaceholder')}
                value={selectedServerId}
                onChange={setSelectedServerId}
                loading={loading}
                size="large"
              >
                {servers.map(server => (
                  <Option key={server.id} value={server.id}>
                    <Space>
                      <span>{server.name}</span>
                      <Tag size="small" color={server.isEnabled ? 'green' : 'red'}>
                        {server.providerName}
                      </Tag>
                      {!server.isEnabled && <Tag size="small" color="orange">{t('mediaFetch.libraryScan.disabled')}</Tag>}
                    </Space>
                  </Option>
                ))}
              </Select>
            </div>

            {selectedServerId && currentServer && (
              <div
                style={{
                  border: currentServer.isEnabled ? '2px solid #52c41a' : '2px solid #faad14',
                  borderRadius: '12px',
                  padding: '20px',
                  backgroundColor: currentServer.isEnabled ? 'rgba(82, 196, 26, 0.1)' : 'rgba(250, 173, 20, 0.1)',
                  marginBottom: '16px',
                  position: 'relative',
                  overflow: 'hidden'
                }}
              >
                {/* 装饰性背景 */}
                <div
                  style={{
                    position: 'absolute',
                    top: 0,
                    right: 0,
                    width: '80px',
                    height: '80px',
                    backgroundColor: currentServer.isEnabled ? 'rgba(82, 196, 26, 0.15)' : 'rgba(250, 173, 20, 0.15)',
                    borderRadius: '50%',
                    opacity: 0.1,
                    transform: 'translate(30px, -30px)'
                  }}
                />

                <div style={{ position: 'relative', zIndex: 1 }}>
                  {/* 服务器头部信息 */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '2px' }}>
                      <div
                        style={{
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          backgroundColor: currentServer.isEnabled ? '#52c41a' : '#faad14',
                          flexShrink: 0
                        }}
                      />
                      <div>
                        <div style={{ display: 'flex', alignItems: screens.xs ? 'flex-start' : 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
                          <Text strong style={{ fontSize: screens.xs ? '14px' : '16px', wordBreak: 'break-word', flex: '1 1 auto' }}>
                            {currentServer.name}
                          </Text>
                          <div style={{ display: 'flex', gap: '4px', flexWrap: screens.xs ? 'nowrap' : 'wrap', flexShrink: 0 }}>
                            <Tag color={currentServer.isEnabled ? 'green' : 'orange'} size="small">
                              {currentServer.providerName}
                            </Tag>
                            <Tag color={currentServer.isEnabled ? 'success' : 'warning'} size="small">
                              {currentServer.isEnabled ? t('mediaFetch.libraryScan.enabled') : t('mediaFetch.libraryScan.disabled')}
                            </Tag>
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* 操作按钮 */}
                    <Space size="small">
                      <Button
                        type="text"
                        icon={<EditOutlined />}
                        size="small"
                        onClick={handleEditServer}
                        title={t('mediaFetch.libraryScan.editServer')}
                      />
                      <Popconfirm
                        title={t('mediaFetch.libraryScan.deleteServerConfirm', { name: currentServer.name })}
                        description={t('mediaFetch.libraryScan.deleteServerDesc')}
                        onConfirm={handleDeleteServer}
                        okText={t('mediaFetch.libraryScan.confirmDelete')}
                        cancelText={t('mediaFetch.libraryScan.cancel')}
                        okButtonProps={{ danger: true }}
                      >
                        <Button
                          type="text"
                          danger
                          icon={<DeleteOutlined />}
                          size="small"
                          title={t('mediaFetch.libraryScan.deleteServer')}
                        />
                      </Popconfirm>
                    </Space>
                  </div>

                  {/* 服务器地址 */}
                  {currentServer.url && (
                    <div style={{ marginBottom: '16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Text type="secondary" style={{ fontSize: screens.xs ? '11px' : '12px', minWidth: screens.xs ? '50px' : '60px', flexShrink: 0 }}>
                          {t('mediaFetch.libraryScan.serverAddress')}
                        </Text>
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                          <Text
                            style={{
                              fontSize: screens.xs ? '12px' : '13px',
                              color: '#666',
                              wordBreak: 'break-all',
                              flex: 1,
                              whiteSpace: 'normal',
                              overflow: 'visible',
                              textOverflow: 'clip'
                            }}
                          >
                            {showServerUrl ? currentServer.url : '•'.repeat(currentServer.url.length)}
                          </Text>
                          <Button
                            type="text"
                            size="small"
                            icon={showServerUrl ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                            onClick={() => setShowServerUrl(!showServerUrl)}
                            style={{ padding: '2px 4px', height: '24px', minWidth: '24px', flexShrink: 0 }}
                            title={showServerUrl ? t('mediaFetch.libraryScan.hideAddress') : t('mediaFetch.libraryScan.showAddress')}
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* 服务器未启用提示 */}
                  {!currentServer.isEnabled && (
                    <Alert
                      message={t('mediaFetch.libraryScan.serverDisabledTitle')}
                      description={t('mediaFetch.libraryScan.serverDisabledDesc')}
                      type="warning"
                      showIcon
                      action={
                        <Button size="small" onClick={handleEditServer}>
                          {t('mediaFetch.libraryScan.configureNow')}
                        </Button>
                      }
                      style={{ marginTop: '16px' }}
                    />
                  )}
                </div>
              </div>
            )}
          </Col>

          <Col xs={24} md={12}>
            <div style={{ padding: '20px', borderRadius: '8px', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
              <Title level={4} style={{ marginBottom: '12px' }}>{t('mediaFetch.libraryScan.instructions')}</Title>
              <Space direction="vertical" size="small">
                <Text>{t('mediaFetch.libraryScan.instruction1')}</Text>
                <Text>{t('mediaFetch.libraryScan.instruction2')}</Text>
                <Text>{t('mediaFetch.libraryScan.instruction3')}</Text>
                <Text>{t('mediaFetch.libraryScan.instruction4')}</Text>
              </Space>
            </div>
          </Col>
        </Row>
      </Card>

      {/* 媒体库配置卡片 */}
      {selectedServerId && (
        <Card
          title={
            <Space>
              <DatabaseOutlined />
              <span>{t('mediaFetch.libraryScan.libraryConfig')}</span>
            </Space>
          }
          style={{ marginBottom: '24px' }}
          extra={
            screens.xs ? null : (
              <Space>
                <Button
                  icon={<SettingOutlined />}
                  onClick={handleEditServer}
                  disabled={!selectedServerId}
                >
                  {t('mediaFetch.libraryScan.editServer')}
                </Button>
                <Button
                  type="primary"
                  icon={<ScanOutlined />}
                  onClick={handleScan}
                  disabled={!selectedServerId || selectedLibraryIds.length === 0 || !selectedLibraryIds.some(id => libraries.some(lib => lib.id === id)) || isServerDisabled}
                  loading={loading}
                  title={
                    !selectedServerId ? t('mediaFetch.libraryScan.scanTipNoServer') :
                    selectedLibraryIds.length === 0 ? t('mediaFetch.libraryScan.scanTipNoLibrary', { count: selectedLibraryIds.length }) :
                    isServerDisabled ? t('mediaFetch.libraryScan.scanTipDisabled') :
                    t('mediaFetch.libraryScan.scanTipStart')
                  }
                >
                  {screens.xs ? t('mediaFetch.libraryScan.scan') : t('mediaFetch.libraryScan.startScan')}
                </Button>
              </Space>
            )
          }
        >
          {screens.xs && (
            <div style={{ marginBottom: '16px', textAlign: 'center' }}>
              <Space>
                <Button
                  icon={<SettingOutlined />}
                  onClick={handleEditServer}
                  disabled={!selectedServerId}
                  size="large"
                >
                  {t('mediaFetch.libraryScan.edit')}
                </Button>
                <Button
                  type="primary"
                  icon={<ScanOutlined />}
                  onClick={handleScan}
                  disabled={!selectedServerId || selectedLibraryIds.length === 0 || !selectedLibraryIds.some(id => libraries.some(lib => lib.id === id)) || isServerDisabled}
                  loading={loading}
                  size="large"
                  title={
                    !selectedServerId ? t('mediaFetch.libraryScan.scanTipNoServer') :
                    selectedLibraryIds.length === 0 ? t('mediaFetch.libraryScan.scanTipNoLibraryShort') :
                    isServerDisabled ? t('mediaFetch.libraryScan.scanTipDisabled') :
                    t('mediaFetch.libraryScan.scanTipStart')
                  }
                >
                  {t('mediaFetch.libraryScan.scan')}
                </Button>
              </Space>
            </div>
          )}
          {isServerDisabled ? (
            <Alert
              message={t('mediaFetch.libraryScan.serverDisabledTitle')}
              description={t('mediaFetch.libraryScan.serverDisabledDesc2')}
              type="warning"
              showIcon
              action={
                <Button size="small" onClick={handleEditServer}>
                  {screens.xs ? t('mediaFetch.libraryScan.configure') : t('mediaFetch.libraryScan.configureServer')}
                </Button>
              }
            />
          ) : loadingLibraries ? (
            <div style={{ textAlign: 'center', padding: '40px' }}>
              <div style={{ fontSize: '16px', color: '#666', marginBottom: '16px' }}>
                {t('mediaFetch.libraryScan.loadingLibraries')}
              </div>
            </div>
          ) : libraries.length === 0 ? (
            <Alert
              message={t('mediaFetch.libraryScan.noLibraryTitle')}
              description={t('mediaFetch.libraryScan.noLibraryDesc')}
              type="info"
              showIcon
            />
          ) : (
            <>
              <div style={{ marginBottom: '20px' }}>
                <Text strong style={{ fontSize: '16px' }}>
                  {t('mediaFetch.libraryScan.selectedLibraries', { count: selectedLibraryIds.length })}
                </Text>
                <Divider />
              </div>

              <Checkbox.Group
                style={{ width: '100%' }}
                value={selectedLibraryIds}
                onChange={setSelectedLibraryIds}
              >
                <Row gutter={[16, 16]}>
                  {libraries.map(library => (
                    <Col xs={24} sm={12} md={8} lg={6} key={library.id}>
                      <div
                        style={{
                          border: selectedLibraryIds.includes(library.id) ? '2px solid #1890ff' : '1px solid var(--color-border)',
                          borderRadius: '8px',
                          padding: '16px',
                          backgroundColor: selectedLibraryIds.includes(library.id) ? 'rgba(24, 144, 255, 0.15)' : 'transparent',
                          cursor: 'pointer',
                          transition: 'all 0.3s',
                          height: '100%',
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'space-between'
                        }}
                        onClick={(e) => {
                          // 避免触发复选框的onChange
                          if (e.target.type !== 'checkbox') {
                            const newSelected = selectedLibraryIds.includes(library.id)
                              ? selectedLibraryIds.filter(id => id !== library.id)
                              : [...selectedLibraryIds, library.id];
                            setSelectedLibraryIds(newSelected);
                          }
                        }}
                      >
                        <div>
                          <div style={{ display: 'flex', alignItems: 'flex-start', marginBottom: '8px' }}>
                            <Checkbox
                              value={library.id}
                              style={{ marginRight: '8px', marginTop: '2px' }}
                            />
                            <div style={{ flex: 1 }}>
                              <Text strong style={{ fontSize: '14px', display: 'block', marginBottom: '4px', color: 'var(--color-text)' }}>
                                {library.name}
                              </Text>
                              <Tag color="blue" size="small">
                                {library.type}
                              </Tag>
                            </div>
                          </div>
                        </div>
                        {library.episodeCount && (
                          <Text type="secondary" style={{ fontSize: '12px', marginTop: '8px' }}>
                            {t('mediaFetch.libraryScan.itemsCount', { count: library.episodeCount })}
                          </Text>
                        )}
                      </div>
                    </Col>
                  ))}
                </Row>
              </Checkbox.Group>

              <Divider />

              <div style={{ textAlign: 'center' }}>
                <Space size="middle" wrap>
                  <Button
                    type="default"
                    size={screens.xs ? "middle" : "large"}
                    onClick={() => {
                      const allIds = libraries.map(lib => lib.id);
                      setSelectedLibraryIds(allIds);
                    }}
                  >
                    {t('mediaFetch.libraryScan.selectAll')}
                  </Button>
                  <Button
                    type="default"
                    size={screens.xs ? "middle" : "large"}
                    onClick={() => {
                      // 清空所有选择，但保持至少一个选中
                      if (libraries.length > 0) {
                        setSelectedLibraryIds([libraries[0].id]);
                      } else {
                        setSelectedLibraryIds([]);
                      }
                    }}
                  >
                    {t('mediaFetch.libraryScan.clear')}
                  </Button>
                  <Button
                    type="default"
                    size={screens.xs ? "middle" : "large"}
                    icon={<SaveOutlined />}
                    loading={savingLibraries}
                    onClick={handleSaveLibraries}
                  >
                    {screens.xs ? t('mediaFetch.libraryScan.save') : t('mediaFetch.libraryScan.saveConfig')}
                  </Button>
                </Space>
              </div>
            </>
          )}
        </Card>
      )}

      {/* 扫描结果 */}
      {selectedServerId && (
        <Card
          title={
            <Space>
              <ScanOutlined />
              <span>{t('mediaFetch.libraryScan.scanResult')}</span>
              {selectedMediaItems.length > 0 && (
                <Tag color="blue">{t('mediaFetch.libraryScan.selected', { count: selectedMediaItems.length })}</Tag>
              )}
            </Space>
          }
          style={{ marginBottom: '24px' }}
          extra={
            screens.xs ? null : (
              <Space>
                <Segmented
                  value={mediaTypeFilter}
                  onChange={setMediaTypeFilter}
                  options={[
                    { label: t('mediaFetch.libraryScan.filterAll'), value: 'all' },
                    { label: t('mediaFetch.libraryScan.filterMovie'), value: 'movie', icon: <VideoCameraOutlined /> },
                    { label: t('mediaFetch.libraryScan.filterTvSeries'), value: 'tv_series', icon: <PlaySquareOutlined /> },
                  ]}
                />
                <Popover
                  trigger="click"
                  placement="bottomRight"
                  content={
                    <Space direction="vertical" size="small">
                      <Space size="small" align="center">
                        <InputNumber
                          placeholder={t('mediaFetch.libraryScan.yearFrom')}
                          value={yearFrom}
                          onChange={setYearFrom}
                          min={1900}
                          max={2100}
                          controls={false}
                          style={{ width: 100 }}
                        />
                        <span>~</span>
                        <InputNumber
                          placeholder={t('mediaFetch.libraryScan.yearTo')}
                          value={yearTo}
                          onChange={setYearTo}
                          min={1900}
                          max={2100}
                          controls={false}
                          style={{ width: 100 }}
                        />
                      </Space>
                      {(yearFrom || yearTo) && (
                        <Button
                          type="link"
                          size="small"
                          onClick={() => {
                            setYearFrom(undefined);
                            setYearTo(undefined);
                          }}
                          style={{ padding: 0 }}
                        >
                          {t('mediaFetch.libraryScan.clearFilter')}
                        </Button>
                      )}
                    </Space>
                  }
                >
                  <Button
                    icon={<CalendarOutlined />}
                    size="small"
                  >
                    {yearFrom || yearTo
                      ? t('mediaFetch.libraryScan.yearLabel', { from: yearFrom || '?', to: yearTo || '?' })
                      : t('mediaFetch.libraryScan.year')}
                  </Button>
                </Popover>
                <Popconfirm
                  title={t('mediaFetch.libraryScan.deleteSelectedConfirm', { count: selectedMediaItems.length })}
                  onConfirm={handleBatchDelete}
                  okText={t('mediaFetch.libraryScan.confirm')}
                  cancelText={t('mediaFetch.libraryScan.cancel')}
                  disabled={selectedMediaItems.length === 0}
                >
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    disabled={selectedMediaItems.length === 0}
                  >
                    {t('mediaFetch.libraryScan.deleteSelected')}
                  </Button>
                </Popconfirm>
                <Button
                  type="primary"
                  icon={<ImportOutlined />}
                  onClick={handleImport}
                  disabled={selectedMediaItems.length === 0}
                >
                  {t('mediaFetch.libraryScan.importSelected')}
                </Button>
                <Button
                  icon={<CloudDownloadOutlined />}
                  onClick={handleImportAllUnimported}
                  disabled={!selectedServerId}
                >
                  {t('mediaFetch.libraryScan.importAllUnimported')}
                </Button>
              </Space>
            )
          }
        >
          {screens.xs && (
            <div style={{ marginBottom: '16px', textAlign: 'center' }}>
              <Space wrap size="middle">
                <Popover
                  trigger="click"
                  placement="bottomLeft"
                  content={
                    <Space direction="vertical" size="small">
                      <Space size="small" align="center">
                        <InputNumber
                          placeholder={t('mediaFetch.libraryScan.yearFrom')}
                          value={yearFrom}
                          onChange={setYearFrom}
                          min={1900}
                          max={2100}
                          controls={false}
                          style={{ width: 100 }}
                        />
                        <span>~</span>
                        <InputNumber
                          placeholder={t('mediaFetch.libraryScan.yearTo')}
                          value={yearTo}
                          onChange={setYearTo}
                          min={1900}
                          max={2100}
                          controls={false}
                          style={{ width: 100 }}
                        />
                      </Space>
                      {(yearFrom || yearTo) && (
                        <Button
                          type="link"
                          size="small"
                          onClick={() => {
                            setYearFrom(undefined);
                            setYearTo(undefined);
                          }}
                          style={{ padding: 0 }}
                        >
                          {t('mediaFetch.libraryScan.clearFilter')}
                        </Button>
                      )}
                    </Space>
                  }
                >
                  <Button
                    icon={<CalendarOutlined />}
                    size="middle"
                  >
                    {yearFrom || yearTo
                      ? t('mediaFetch.libraryScan.yearLabelShort', { from: yearFrom || '?', to: yearTo || '?' })
                      : t('mediaFetch.libraryScan.year')}
                  </Button>
                </Popover>
                <Popconfirm
                  title={t('mediaFetch.libraryScan.deleteSelectedConfirm', { count: selectedMediaItems.length })}
                  onConfirm={handleBatchDelete}
                  okText={t('mediaFetch.libraryScan.confirm')}
                  cancelText={t('mediaFetch.libraryScan.cancel')}
                  disabled={selectedMediaItems.length === 0}
                >
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    disabled={selectedMediaItems.length === 0}
                    size="middle"
                  >
                    {t('mediaFetch.libraryScan.delete')}
                  </Button>
                </Popconfirm>
                <Button
                  type="primary"
                  icon={<ImportOutlined />}
                  onClick={handleImport}
                  disabled={selectedMediaItems.length === 0}
                  size="middle"
                >
                  {t('mediaFetch.libraryScan.import')}
                </Button>
                <Button
                  icon={<CloudDownloadOutlined />}
                  onClick={handleImportAllUnimported}
                  disabled={!selectedServerId}
                  size="middle"
                >
                  {t('mediaFetch.libraryScan.importAll')}
                </Button>
              </Space>
            </div>
          )}
          <MediaItemList
            serverId={selectedServerId}
            refreshTrigger={refreshTrigger}
            selectedItems={selectedMediaItems}
            onSelectionChange={setSelectedMediaItems}
            mediaTypeFilter={mediaTypeFilter}
            yearFrom={yearFrom}
            yearTo={yearTo}
          />
        </Card>
      )}

      <ServerConfigPanel
        visible={configModalVisible}
        server={editingServer}
        onClose={() => setConfigModalVisible(false)}
        onSaved={handleConfigSaved}
      />
    </div>
  );
};

export default LibraryScan;

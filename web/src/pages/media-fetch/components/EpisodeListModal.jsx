import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Space, Input, message, Checkbox, Popconfirm, Tag } from 'antd';
import { SearchOutlined, DeleteOutlined, EditOutlined, ImportOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { getSeasonEpisodes, deleteMediaItem, batchDeleteMediaItems, importMediaItems } from '../../../apis';
import MediaItemEditor from './MediaItemEditor';

const { Search } = Input;

const EpisodeListModal = ({ visible, onClose, serverId, title, season, onRefresh }) => {
  const { t } = useTranslation();
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [searchText, setSearchText] = useState('');
  const [pagination, setPagination] = useState({ current: 1, pageSize: 100, total: 0 });
  const [editorVisible, setEditorVisible] = useState(false);
  const [editingItem, setEditingItem] = useState(null);

  // 加载分集列表
  const loadEpisodes = async (page = 1, pageSize = 100) => {
    if (!serverId || !title || season === null || season === undefined) return;
    
    setLoading(true);
    try {
      const res = await getSeasonEpisodes(title, season, serverId, page, pageSize);
      const data = res.data;
      
      setEpisodes(data.list || []);
      setPagination({
        current: page,
        pageSize,
        total: data.total || 0,
      });
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.loadFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (visible) {
      loadEpisodes();
      setSelectedRowKeys([]);
      setSearchText('');
    }
  }, [visible, serverId, title, season]);

  // 处理表格变化
  const handleTableChange = (newPagination) => {
    loadEpisodes(newPagination.current, newPagination.pageSize);
  };

  // 处理删除
  const handleDelete = async (record) => {
    try {
      await deleteMediaItem(record.id);
      message.success(t('mediaFetch.episodeListModal.deleteSuccess'));
      loadEpisodes(pagination.current, pagination.pageSize);
      if (onRefresh) onRefresh();
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.deleteFailed').replace(': ', ''));
      console.error(error);
    }
  };

  // 批量删除
  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning(t('mediaFetch.episodeListModal.selectDeleteWarning'));
      return;
    }

    try {
      await batchDeleteMediaItems({ itemIds: selectedRowKeys });
      message.success(t('mediaFetch.episodeListModal.deleteSuccess'));
      setSelectedRowKeys([]);
      loadEpisodes(pagination.current, pagination.pageSize);
      if (onRefresh) onRefresh();
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.batchDeleteFailed'));
      console.error(error);
    }
  };

  // 处理编辑
  const handleEdit = (record) => {
    setEditingItem(record);
    setEditorVisible(true);
  };

  const handleEditorSaved = () => {
    setEditorVisible(false);
    loadEpisodes(pagination.current, pagination.pageSize);
    if (onRefresh) onRefresh();
  };

  // 批量导入
  const handleBatchImport = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning(t('mediaFetch.episodeListModal.selectImportWarning'));
      return;
    }

    try {
      const res = await importMediaItems({ itemIds: selectedRowKeys });
      message.success(res.data.message || t('mediaFetch.episodeListModal.importSubmitted'));
      setSelectedRowKeys([]);
      loadEpisodes(pagination.current, pagination.pageSize);
      if (onRefresh) onRefresh();
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.batchImportFailed'));
      console.error(error);
    }
  };

  // 过滤数据
  const filteredEpisodes = episodes.filter(ep => {
    if (!searchText) return true;
    const searchLower = searchText.toLowerCase();
    return (
      ep.title?.toLowerCase().includes(searchLower) ||
      ep.episode?.toString().includes(searchText) ||
      ep.tmdbId?.toLowerCase().includes(searchLower) ||
      ep.tvdbId?.toLowerCase().includes(searchLower) ||
      ep.imdbId?.toLowerCase().includes(searchLower)
    );
  });

  const columns = [
    {
      title: t('mediaFetch.episodeListModal.colEpisode'),
      dataIndex: 'episode',
      key: 'episode',
      width: '10%',
      sorter: (a, b) => a.episode - b.episode,
      render: (episode) => t('mediaFetch.episodeListModal.episodeNum', { ep: episode }),
    },
    {
      title: t('mediaFetch.episodeListModal.colTitle'),
      dataIndex: 'title',
      key: 'title',
      width: '25%',
    },
    {
      title: 'TMDB ID',
      dataIndex: 'tmdbId',
      key: 'tmdbId',
      width: '15%',
      render: (tmdbId) => tmdbId || '-',
    },
    {
      title: 'TVDB ID',
      dataIndex: 'tvdbId',
      key: 'tvdbId',
      width: '15%',
      render: (tvdbId) => tvdbId || '-',
    },
    {
      title: 'IMDB ID',
      dataIndex: 'imdbId',
      key: 'imdbId',
      width: '15%',
      render: (imdbId) => imdbId || '-',
    },
    {
      title: t('mediaFetch.episodeListModal.colStatus'),
      dataIndex: 'isImported',
      key: 'isImported',
      width: '10%',
      render: (isImported) => {
        return isImported ? (
          <Tag color="success">{t('mediaFetch.episodeListModal.imported2')}</Tag>
        ) : (
          <Tag>{t('mediaFetch.episodeListModal.notImported2')}</Tag>
        );
      },
    },
    {
      title: t('mediaFetch.episodeListModal.colAction'),
      key: 'action',
      width: '16%',
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            {t('mediaFetch.episodeListModal.edit')}
          </Button>
          <Button
            type="link"
            size="small"
            icon={<ImportOutlined />}
            onClick={() => {
              importMediaItems({ itemIds: [record.id] })
                .then((res) => {
                  message.success(res.data.message || t('mediaFetch.episodeListModal.importSubmitted'));
                  loadEpisodes(pagination.current, pagination.pageSize);
                  if (onRefresh) onRefresh();
                })
                .catch(() => message.error(t('mediaFetch.episodeListModal.importFailed').replace(': ', '')));
            }}
          >
            {t('mediaFetch.episodeListModal.import')}
          </Button>
          <Popconfirm
            title={t('mediaFetch.episodeListModal.confirmDeleteEpisode')}
            onConfirm={() => handleDelete(record)}
            okText={t('mediaFetch.episodeListModal.confirm')}
            cancelText={t('mediaFetch.episodeListModal.cancel')}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              {t('mediaFetch.episodeListModal.delete')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys) => {
      setSelectedRowKeys(newSelectedRowKeys);
    },
  };

  return (
    <>
      <Modal
        title={t('mediaFetch.episodeListModal.serverSeasonTitle', { title, season })}
        open={visible}
        onCancel={onClose}
        width={1200}
        footer={[
          <Button key="close" onClick={onClose}>
            {t('mediaFetch.episodeListModal.close')}
          </Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 搜索和操作栏 */}
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Search
              placeholder={t('mediaFetch.episodeListModal.searchPlaceholder')}
              allowClear
              style={{ width: 300 }}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              prefix={<SearchOutlined />}
            />
            <Space>
              <Button
                type="primary"
                icon={<ImportOutlined />}
                onClick={handleBatchImport}
                disabled={selectedRowKeys.length === 0}
              >
                {t('mediaFetch.episodeListModal.batchImport', { count: selectedRowKeys.length })}
              </Button>
              <Popconfirm
                title={t('mediaFetch.episodeListModal.confirmBatchDelete', { count: selectedRowKeys.length })}
                onConfirm={handleBatchDelete}
                okText={t('mediaFetch.episodeListModal.confirm')}
                cancelText={t('mediaFetch.episodeListModal.cancel')}
                disabled={selectedRowKeys.length === 0}
              >
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={selectedRowKeys.length === 0}
                >
                  {t('mediaFetch.episodeListModal.batchDelete', { count: selectedRowKeys.length })}
                </Button>
              </Popconfirm>
            </Space>
          </Space>

          {/* 表格 */}
          <Table
            rowSelection={rowSelection}
            columns={columns}
            dataSource={filteredEpisodes}
            rowKey="id"
            loading={loading}
            pagination={{
              ...pagination,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total) => t('mediaFetch.episodeListModal.totalEpisodes', { total }),
            }}
            onChange={handleTableChange}
            size="small"
          />
        </Space>
      </Modal>

      {/* 编辑弹窗 */}
      {editorVisible && (
        <MediaItemEditor
          visible={editorVisible}
          item={editingItem}
          onClose={() => setEditorVisible(false)}
          onSaved={handleEditorSaved}
        />
      )}
    </>
  );
};

export default EpisodeListModal;


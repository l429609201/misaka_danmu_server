import { useState, useEffect } from 'react';
import { Modal, Table, Button, Space, message, Popconfirm } from 'antd';
import { DeleteOutlined, EditOutlined, ImportOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { getLocalSeasonEpisodes, deleteLocalItem, importLocalItems } from '../../../apis';
import MediaItemEditor from './MediaItemEditor';

const LocalEpisodeListModal = ({ visible, season, onClose, onRefresh }) => {
  const { t } = useTranslation();
  const [episodes, setEpisodes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 100,
    total: 0,
  });
  const [editingItem, setEditingItem] = useState(null);
  const [editorVisible, setEditorVisible] = useState(false);

  useEffect(() => {
    if (visible && season) {
      loadEpisodes(1, pagination.pageSize);
    }
  }, [visible, season]);

  const loadEpisodes = async (page = 1, pageSize = 100) => {
    if (!season) return;

    setLoading(true);
    try {
      const res = await getLocalSeasonEpisodes(season.title, season.season, page, pageSize);
      const data = res.data;
      setEpisodes(data.list);
      setPagination({
        current: page,
        pageSize,
        total: data.total,
      });
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.loadFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (record) => {
    setEditingItem(record);
    setEditorVisible(true);
  };

  const handleDelete = async (record) => {
    try {
      await deleteLocalItem(record.id);
      message.success(t('mediaFetch.episodeListModal.deleteSuccess'));
      loadEpisodes(pagination.current, pagination.pageSize);
      onRefresh?.();
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.deleteFailed') + (error.message || t('mediaFetch.episodeListModal.unknownError')));
    }
  };

  const handleImport = async (record) => {
    try {
      const res = await importLocalItems({ itemIds: [record.id] });
      message.success(res.data.message || t('mediaFetch.episodeListModal.importSubmitted'));
      loadEpisodes(pagination.current, pagination.pageSize);
      onRefresh?.();
    } catch (error) {
      message.error(t('mediaFetch.episodeListModal.importFailed') + (error.message || t('mediaFetch.episodeListModal.unknownError')));
    }
  };

  const columns = [
    {
      title: t('mediaFetch.episodeListModal.colEpisode'),
      dataIndex: 'episode',
      key: 'episode',
      width: '15%',
      render: (ep) => t('mediaFetch.episodeListModal.episodeNum', { ep }),
    },
    {
      title: t('mediaFetch.episodeListModal.colFilePath'),
      dataIndex: 'filePath',
      key: 'filePath',
      width: '37.5%',
      ellipsis: true,
    },
    {
      title: t('mediaFetch.episodeListModal.colStatus'),
      dataIndex: 'isImported',
      key: 'isImported',
      width: '11.25%',
      render: (imported) => (imported ? t('mediaFetch.episodeListModal.imported') : t('mediaFetch.episodeListModal.notImported')),
    },
    {
      title: t('mediaFetch.episodeListModal.colAction'),
      key: 'action',
      width: '20%',
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<ImportOutlined />}
            onClick={() => handleImport(record)}
            disabled={record.isImported}
          >
            {t('mediaFetch.episodeListModal.import')}
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            {t('mediaFetch.episodeListModal.edit')}
          </Button>
          <Popconfirm title={t('mediaFetch.episodeListModal.confirmDelete')} onConfirm={() => handleDelete(record)} okText={t('mediaFetch.episodeListModal.confirm')} cancelText={t('mediaFetch.episodeListModal.cancel')}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              {t('mediaFetch.episodeListModal.delete')}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Modal
        title={season ? t('mediaFetch.episodeListModal.seasonTitle', { title: season.title, season: season.season }) : t('mediaFetch.episodeListModal.defaultTitle')}
        open={visible}
        onCancel={onClose}
        footer={null}
        width={1000}
      >
        <Table
          columns={columns}
          dataSource={episodes}
          loading={loading}
          rowKey="id"
          pagination={{
            ...pagination,
            showSizeChanger: true,
            showTotal: (total) => t('mediaFetch.episodeListModal.totalEpisodes', { total }),
            onChange: (page, pageSize) => loadEpisodes(page, pageSize),
          }}
        />
      </Modal>

      <MediaItemEditor
        visible={editorVisible}
        item={editingItem}
        isLocal={true}
        onClose={() => {
          setEditorVisible(false);
          setEditingItem(null);
        }}
        onSaved={() => {
          setEditorVisible(false);
          setEditingItem(null);
          loadEpisodes(pagination.current, pagination.pageSize);
          onRefresh?.();
        }}
      />
    </>
  );
};

export default LocalEpisodeListModal;


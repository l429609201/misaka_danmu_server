import { useState, useEffect, useMemo } from 'react';
import { Modal, Table, Button, Space, message, Popconfirm, Radio, Select } from 'antd';
import { DeleteOutlined, EditOutlined, ImportOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { getLocalMovieFiles, deleteLocalItem, importLocalItems, addSourceToAnime } from '../../../apis';
import MediaItemEditor from './MediaItemEditor';

// 来源标签选项(仅用于显示，标签文案通过 t 国际化)
const getSourceLabels = (t) => [
  { value: 'unknown', label: t('mediaFetch.localMovieFiles.unknownSource') },
  { value: 'bilibili', label: 'Bilibili' },
  { value: 'tencent', label: '腾讯视频' },
  { value: 'iqiyi', label: '爱奇艺' },
  { value: 'youku', label: '优酷' },
  { value: 'mgtv', label: '芒果TV' },
  { value: 'renren', label: '人人视频' },
];

// 从文件名识别来源标签
const detectSourceLabelFromFilename = (filename) => {
  const lowerFilename = filename.toLowerCase();
  if (lowerFilename.includes('bilibili') || lowerFilename.includes('哔哩')) {
    return 'bilibili';
  }
  if (lowerFilename.includes('iqiyi') || lowerFilename.includes('爱奇艺')) {
    return 'iqiyi';
  }
  if (lowerFilename.includes('tencent') || lowerFilename.includes('腾讯')) {
    return 'tencent';
  }
  if (lowerFilename.includes('youku') || lowerFilename.includes('优酷')) {
    return 'youku';
  }
  if (lowerFilename.includes('mgtv') || lowerFilename.includes('芒果')) {
    return 'mgtv';
  }
  if (lowerFilename.includes('renren') || lowerFilename.includes('人人')) {
    return 'renren';
  }
  return 'unknown';
};

// 生成mediaId: custom_{sourceLabel}
const generateMediaId = (sourceLabel) => {
  return `custom_${sourceLabel}`;
};

const LocalMovieFilesModal = ({ visible, movie, onClose, onRefresh }) => {
  const { t } = useTranslation();
  const SOURCE_LABELS = useMemo(() => getSourceLabels(t), [t]);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 100,
    total: 0,
  });
  const [editorVisible, setEditorVisible] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [selectedFileId, setSelectedFileId] = useState(null);
  // 文件来源配置: { fileId: { sourceLabel: 'bilibili', mediaId: 'custom_bilibili' } }
  const [fileSourceConfig, setFileSourceConfig] = useState({});

  useEffect(() => {
    if (visible && movie) {
      loadFiles(pagination.current, pagination.pageSize);
    }
  }, [visible, movie]);

  const loadFiles = async (page, pageSize) => {
    if (!movie) return;

    setLoading(true);
    try {
      const res = await getLocalMovieFiles(movie.title, movie.year, page, pageSize);
      const data = res.data;
      setFiles(data.list || []);
      setPagination({
        current: page,
        pageSize: pageSize,
        total: data.total || 0,
      });

      // 初始化文件来源配置
      const sourceConfig = {};
      if (data.list && data.list.length > 0) {
        data.list.forEach((file) => {
          const filename = file.filePath.split(/[/\\]/).pop();
          const detectedLabel = detectSourceLabelFromFilename(filename);
          sourceConfig[file.id] = {
            sourceLabel: detectedLabel,
            mediaId: generateMediaId(detectedLabel),
          };
        });
        setFileSourceConfig(sourceConfig);

        // 自动选择第一个未导入的文件,如果没有则选择第一个
        const firstNotImported = data.list.find(f => !f.isImported);
        setSelectedFileId(firstNotImported ? firstNotImported.id : data.list[0].id);
      }
    } catch (error) {
      message.error(t('mediaFetch.localMovieFiles.loadFilesFailed') + (error.message || t('mediaFetch.localMovieFiles.unknownError')));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteLocalItem(id);
      message.success(t('mediaFetch.localMovieFiles.deleteSuccess'));
      loadFiles(pagination.current, pagination.pageSize);
      onRefresh?.();
    } catch (error) {
      message.error(t('mediaFetch.localMovieFiles.deleteFailed') + (error.message || t('mediaFetch.localMovieFiles.unknownError')));
    }
  };

  const handleEdit = (record) => {
    setEditingItem(record);
    setEditorVisible(true);
  };

  const handleImport = async () => {
    if (!selectedFileId) {
      message.warning(t('mediaFetch.localMovieFiles.selectFileToImport'));
      return;
    }

    const config = fileSourceConfig[selectedFileId];
    if (!config) {
      message.error(t('mediaFetch.localMovieFiles.configLost'));
      return;
    }

    try {
      // 使用高级导入API,provider固定为custom,mediaId为custom_{sourceLabel}
      const res = await importLocalItems({
        items: [{
          itemId: selectedFileId,
          provider: 'custom',
          mediaId: config.mediaId,
        }]
      });
      message.success(res.data.message || t('mediaFetch.localMovieFiles.importSubmitted'));
      onClose();
      onRefresh?.();
    } catch (error) {
      message.error(t('mediaFetch.localMovieFiles.importFailed') + (error.message || t('mediaFetch.localMovieFiles.unknownError')));
    }
  };

  // 更新文件的来源标签
  const handleSourceLabelChange = (fileId, sourceLabel) => {
    setFileSourceConfig(prev => ({
      ...prev,
      [fileId]: {
        sourceLabel,
        mediaId: generateMediaId(sourceLabel),
      }
    }));
  };

  const columns = [
    {
      title: t('mediaFetch.localMovieFiles.colSelect'),
      key: 'select',
      width: '6%',
      render: (_, record) => (
        <Radio
          checked={selectedFileId === record.id}
          onChange={() => setSelectedFileId(record.id)}
        />
      ),
    },
    {
      title: t('mediaFetch.localMovieFiles.colFilePath'),
      dataIndex: 'filePath',
      key: 'filePath',
      width: '35%',
      ellipsis: true,
    },
    {
      title: t('mediaFetch.localMovieFiles.colSourceLabel'),
      key: 'sourceLabel',
      width: '15%',
      render: (_, record) => {
        const config = fileSourceConfig[record.id];
        return (
          <Select
            value={config?.sourceLabel || 'unknown'}
            onChange={(value) => handleSourceLabelChange(record.id, value)}
            options={SOURCE_LABELS}
            style={{ width: '100%' }}
            size="small"
          />
        );
      },
    },
    {
      title: t('mediaFetch.localMovieFiles.colNfoPath'),
      dataIndex: 'nfoPath',
      key: 'nfoPath',
      width: '25%',
      ellipsis: true,
      render: (path) => path || '-',
    },
    {
      title: t('mediaFetch.localMovieFiles.colStatus'),
      dataIndex: 'isImported',
      key: 'isImported',
      width: '10%',
      render: (imported) => (imported ? t('mediaFetch.localMovieFiles.imported') : t('mediaFetch.localMovieFiles.notImported')),
    },
  ];

  return (
    <>
      <Modal
        title={movie ? t('mediaFetch.localMovieFiles.selectFileTitle', { title: `${movie.title}${movie.year ? ` (${movie.year})` : ''}` }) : t('mediaFetch.localMovieFiles.defaultTitle')}
        open={visible}
        onCancel={onClose}
        width={1000}
        footer={[
          <Button key="cancel" onClick={onClose}>
            {t('mediaFetch.localMovieFiles.cancel')}
          </Button>,
          <Button
            key="import"
            type="primary"
            icon={<ImportOutlined />}
            onClick={handleImport}
            disabled={!selectedFileId}
          >
            {t('mediaFetch.localMovieFiles.importSelected')}
          </Button>,
        ]}
      >
        <Table
          columns={columns}
          dataSource={files}
          loading={loading}
          rowKey="id"
          pagination={{
            ...pagination,
            showSizeChanger: true,
            showTotal: (total) => t('mediaFetch.localMovieFiles.totalFiles', { total }),
            onChange: (page, pageSize) => loadFiles(page, pageSize),
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
          loadFiles(pagination.current, pagination.pageSize);
          onRefresh?.();
        }}
      />
    </>
  );
};

export default LocalMovieFilesModal;


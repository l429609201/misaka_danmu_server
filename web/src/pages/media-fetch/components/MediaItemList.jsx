import React, { useState, useEffect } from 'react';
import { Card, Table, Button, Space, Input, message, Checkbox, Popconfirm, Tag, List, Row, Col, Dropdown, Segmented, Pagination, Popover } from 'antd';
import { SearchOutlined, DeleteOutlined, EditOutlined, ImportOutlined, FolderOpenOutlined, AppstoreOutlined, TableOutlined, MoreOutlined, VideoCameraOutlined, PlaySquareOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { getMediaWorks, deleteMediaItem, batchDeleteMediaItems, importMediaItems } from '../../../apis';
import MediaItemEditor from './MediaItemEditor';
import EpisodeListModal from './EpisodeListModal';
import { useDefaultPageSize } from '../../../hooks/useDefaultPageSize';

const MediaItemList = ({ serverId, refreshTrigger, selectedItems = [], onSelectionChange, mediaTypeFilter: externalMediaTypeFilter, yearFrom, yearTo }) => {
  const { t } = useTranslation();
  // 从后端配置获取默认分页大小
  const defaultPageSize = useDefaultPageSize('mediaItems');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [searchText, setSearchText] = useState('');
  const [searchInput, setSearchInput] = useState(''); // 临时搜索输入
  const [pagination, setPagination] = useState({ current: 1, pageSize: defaultPageSize, total: 0 });
  const [editorVisible, setEditorVisible] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [episodeModalVisible, setEpisodeModalVisible] = useState(false);
  const [selectedShow, setSelectedShow] = useState(null);
  const [viewMode, setViewMode] = useState('table'); // 'table' or 'card'

  // 使用外部传入的 mediaTypeFilter,如果没有则使用默认值
  const mediaTypeFilter = externalMediaTypeFilter || 'all';

  // 加载作品列表
  const loadItems = async (page = 1, pageSize = 100) => {
    setLoading(true);
    try {
      const params = {
        server_id: serverId,
        page,
        page_size: pageSize,
      };

      // 添加类型过滤
      if (mediaTypeFilter !== 'all') {
        params.media_type = mediaTypeFilter;
      }

      // 添加搜索过滤
      if (searchText) {
        params.search = searchText;
      }
      if (yearFrom !== undefined && yearFrom !== null && yearFrom !== '') {
        params.year_from = yearFrom;
      }
      if (yearTo !== undefined && yearTo !== null && yearTo !== '') {
        params.year_to = yearTo;
      }

      const res = await getMediaWorks(params);
      const data = res.data;

      // 构建树形结构(只包含作品和季度,不包含集)
      // 【优化】buildTreeData 现在是同步函数，不再需要 await
      const treeData = buildTreeData(data.list);
      setItems(treeData);
      setPagination({
        current: page,
        pageSize,
        total: data.total,
      });
    } catch (error) {
      message.error(t('mediaFetch.mediaItemList.loadFailed'));
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  // 构建树形数据结构(作品 > 季度)
  // 【优化】直接使用后端返回的 seasons 字段，避免 N+1 查询
  const buildTreeData = (worksList) => {
    const result = [];

    for (const work of worksList) {
      if (work.type === 'movie') {
        // 电影节点 - 使用纯数字id作为key
        result.push({
          ...work,
          key: work.id,
          isGroup: false,
        });
      } else if (work.type === 'tv_show') {
        // 电视节目组节点
        // 【优化】直接使用后端返回的 seasons，不再额外请求
        const seasons = work.seasons || [];

        result.push({
          key: `show-${work.title}`,
          title: work.title,
          mediaType: 'tv_show',
          year: work.year,
          tmdbId: work.tmdbId,
          tvdbId: work.tvdbId,
          imdbId: work.imdbId,
          posterUrl: work.posterUrl,
          serverId: work.serverId,
          isGroup: true,
          seasonCount: work.seasonCount,
          episodeCount: work.episodeCount,
          importedCount: work.importedCount,
          children: seasons.map(s => ({
            key: `season-${work.title}-S${s.season}`,
            title: t('mediaFetch.mediaItemList.seasonLabel', { season: s.season, count: s.episodeCount }),
            season: s.season,
            episodeCount: s.episodeCount,
            importedCount: s.importedCount,
            year: s.year,
            posterUrl: s.posterUrl,
            mediaType: 'tv_season',
            serverId: work.serverId,
            showTitle: work.title,
            isGroup: true,
          })),
        });
      }
    }

    return result;
  };

  // 当默认分页大小加载完成后，更新 pagination
  useEffect(() => {
    if (defaultPageSize) {
      setPagination(prev => ({
        ...prev,
        pageSize: defaultPageSize
      }));
    }
  }, [defaultPageSize]);

  useEffect(() => {
    if (serverId) {
      loadItems(1, pagination.pageSize);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId, refreshTrigger, externalMediaTypeFilter, searchText, yearFrom, yearTo]);

  // 同步外部选中的项目
  useEffect(() => {
    setSelectedRowKeys(selectedItems);
  }, [selectedItems]);

  // 当选中状态改变时，通知外部组件
  const handleSelectionChange = (keys) => {
    setSelectedRowKeys(keys);
    if (onSelectionChange) {
      onSelectionChange(keys);
    }
  };

  // 处理表格变化
  const handleTableChange = (newPagination) => {
    loadItems(newPagination.current, newPagination.pageSize);
  };

  // 处理删除
  const handleDelete = async (record) => {
    if (record.isGroup) {
      message.warning(t('mediaFetch.mediaItemList.cantDeleteGroup'));
      return;
    }

    try {
      await deleteMediaItem(record.id);
      message.success(t('mediaFetch.mediaItemList.deleteSuccess'));
      loadItems(pagination.current, pagination.pageSize);
    } catch (error) {
      message.error(t('mediaFetch.mediaItemList.deleteFailed'));
      console.error(error);
    }
  };

  // 批量删除
  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning(t('mediaFetch.mediaItemList.selectDeleteWarning'));
      return;
    }

    // 分类收集要删除的项目
    const itemIds = [];
    const shows = [];
    const seasons = [];

    selectedRowKeys.forEach(key => {
      // 如果key是数字,说明是电影的id
      if (typeof key === 'number') {
        itemIds.push(key);
        return;
      }

      // 如果key是字符串且以episode-开头,提取id
      if (typeof key === 'string' && key.startsWith('episode-')) {
        itemIds.push(parseInt(key.split('-')[1]));
        return;
      }

      // 其他情况,查找对应的item
      const item = findItemByKey(items, key);
      if (!item) return;

      if (item.mediaType === 'tv_show') {
        // 整个剧集组
        shows.push({
          serverId: serverId,
          title: item.title
        });
      } else if (item.mediaType === 'tv_season') {
        // 某一季：季度节点自带 showTitle(父剧名)/serverId，直接使用，不从 key 反推父节点
        seasons.push({
          serverId: item.serverId,
          title: item.showTitle,
          season: item.season
        });
      }
    });

    if (itemIds.length === 0 && shows.length === 0 && seasons.length === 0) {
      message.warning(t('mediaFetch.mediaItemList.noDeletableItems'));
      return;
    }

    try {
      const payload = {};
      if (itemIds.length > 0) payload.itemIds = itemIds;
      if (shows.length > 0) payload.shows = shows;
      if (seasons.length > 0) payload.seasons = seasons;

      await batchDeleteMediaItems(payload);
      message.success(t('mediaFetch.mediaItemList.deleteSuccess'));
      setSelectedRowKeys([]);
      loadItems(pagination.current, pagination.pageSize);
    } catch (error) {
      message.error(t('mediaFetch.mediaItemList.batchDeleteFailed'));
      console.error(error);
    }
  };

  // 辅助函数:根据key查找item
  const findItemByKey = (list, key) => {
    for (const item of list) {
      if (item.key === key) return item;
      if (item.children) {
        const found = findItemByKey(item.children, key);
        if (found) return found;
      }
    }
    return null;
  };

  // 处理编辑
  const handleEdit = (record) => {
    if (record.isGroup) {
      message.warning(t('mediaFetch.mediaItemList.cantEditGroup'));
      return;
    }
    setEditingItem(record);
    setEditorVisible(true);
  };

  const handleEditorSaved = () => {
    setEditorVisible(false);
    loadItems(pagination.current, pagination.pageSize);
  };

  // 处理导入
  const handleImport = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning(t('mediaFetch.mediaItemList.selectImportWarning'));
      return;
    }

    // 分类收集要导入的项目
    const itemIds = [];
    const shows = [];
    const seasons = [];

    selectedRowKeys.forEach(key => {
      if (key.startsWith('movie-') || key.startsWith('episode-')) {
        // 直接导入的电影或剧集
        itemIds.push(parseInt(key.split('-')[1]));
      } else {
        // 查找对应的item
        const item = findItemByKey(items, key);
        if (!item) return;

        if (item.mediaType === 'tv_show') {
          // 整个剧集组
          shows.push({
            serverId: serverId,
            title: item.title
          });
        } else if (item.mediaType === 'tv_season') {
          // 某一季：季度节点自带 showTitle(父剧名)/serverId，直接使用，不从 key 反推父节点
          seasons.push({
            serverId: item.serverId,
            title: item.showTitle,
            season: item.season
          });
        }
      }
    });

    if (itemIds.length === 0 && shows.length === 0 && seasons.length === 0) {
      message.warning(t('mediaFetch.mediaItemList.noImportableItems'));
      return;
    }

    try {
      const payload = {};
      if (itemIds.length > 0) payload.itemIds = itemIds;
      if (shows.length > 0) payload.shows = shows;
      if (seasons.length > 0) payload.seasons = seasons;

      const res = await importMediaItems(payload);
      const result = res.data;
      message.success(result.message || t('mediaFetch.mediaItemList.importSubmitted'));
      setSelectedRowKeys([]);
      loadItems(pagination.current, pagination.pageSize);
    } catch (error) {
      message.error(t('mediaFetch.mediaItemList.importFailedWith') + (error.message || t('mediaFetch.mediaItemList.unknownError')));
      console.error(error);
    }
  };

  // 打开分集列表弹窗
  const handleOpenEpisodes = (record) => {
    setSelectedShow({
      serverId: record.serverId,
      title: record.showTitle,
      season: record.season,
    });
    setEpisodeModalVisible(true);
  };

  // 表格列定义
  const columns = [
    {
      title: t('mediaFetch.mediaItemList.colTitle'),
      dataIndex: 'title',
      key: 'title',
      width: '30%',
      render: (title, record) => {
        // 季度节点显示为可点击链接
        if (record.mediaType === 'tv_season') {
          return (
            <Button
              type="link"
              icon={<FolderOpenOutlined />}
              onClick={() => handleOpenEpisodes(record)}
              style={{ padding: 0 }}
            >
              {title}
            </Button>
          );
        }
        return title;
      },
    },
    {
      title: t('mediaFetch.mediaItemList.colType'),
      dataIndex: 'mediaType',
      key: 'mediaType',
      width: '10%',
      render: (type) => {
        const typeMap = {
          movie: t('mediaFetch.mediaItemList.typeMovie'),
          tv_series: t('mediaFetch.mediaItemList.typeTvSeries'),
          tv_show: t('mediaFetch.mediaItemList.typeTvSeries'),
          tv_season: '-',
        };
        return typeMap[type] || type;
      },
    },
    {
      title: t('mediaFetch.mediaItemList.colYear'),
      dataIndex: 'year',
      key: 'year',
      width: '10%',
      render: (year) => year || '-',
    },
    {
      title: t('mediaFetch.mediaItemList.colStatus'),
      dataIndex: 'isImported',
      key: 'isImported',
      width: '10%',
      render: (isImported, record) => {
        if (record.isGroup) {
          const imported = record.importedCount;
          const total = record.episodeCount;
          if (imported !== undefined && total !== undefined) {
            if (imported === total && total > 0) {
              return <Tag color="success">{t('mediaFetch.mediaItemList.allImported')}</Tag>;
            } else if (imported > 0) {
              return <Tag color="processing">{imported}/{total}</Tag>;
            } else {
              return <Tag>{t('mediaFetch.mediaItemList.notImported')}</Tag>;
            }
          }
          return '-';
        }
        return isImported ? (
          <Tag color="success">{t('mediaFetch.mediaItemList.imported')}</Tag>
        ) : (
          <Tag>{t('mediaFetch.mediaItemList.notImported')}</Tag>
        );
      },
    },
    {
      title: t('mediaFetch.mediaItemList.colAction'),
      key: 'action',
      width: '20%',
      render: (_, record) => {
        // 剧集组显示删除和导入整部按钮
        if (record.isGroup && record.mediaType === 'tv_show') {
          return (
            <Space size="small">
              <Popconfirm
                title={t('mediaFetch.mediaItemList.confirmDeleteShow', { title: record.title })}
                onConfirm={() => {
                  // 删除整部剧集
                  batchDeleteMediaItems({
                    shows: [{
                      serverId: serverId,
                      title: record.title
                    }]
                  })
                    .then(() => {
                      message.success(t('mediaFetch.mediaItemList.deleteShowSuccess', { title: record.title }));
                      loadItems(pagination.current, pagination.pageSize);
                    })
                    .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
                }}
                okText={t('mediaFetch.mediaItemList.confirm')}
                cancelText={t('mediaFetch.mediaItemList.cancel')}
              >
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                  {t('mediaFetch.mediaItemList.deleteWhole')}
                </Button>
              </Popconfirm>
              <Button
                type="link"
                size="small"
                icon={<ImportOutlined />}
                onClick={() => {
                  // 导入整部剧集
                  importMediaItems({
                    shows: [{
                      serverId: serverId,
                      title: record.title
                    }]
                  })
                    .then((res) => {
                      message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                      loadItems(pagination.current, pagination.pageSize);
                    })
                    .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
                }}
              >
                {t('mediaFetch.mediaItemList.importWhole')}
              </Button>
            </Space>
          );
        }

        // 季度显示删除和导入按钮
        if (record.mediaType === 'tv_season') {
          return (
            <Space size="small">
              <Popconfirm
                title={t('mediaFetch.mediaItemList.confirmDeleteSeason', { season: record.season })}
                onConfirm={() => {
                  // 删除该季度：季度节点自带 showTitle(父剧名)/serverId，直接使用，
                  // 不再从 key 反推父节点（反推 season-{标题}-S{n} => season-{标题} 与父 show-{标题} 不符，
                  // 会导致 findItemByKey 返回 null 而整个操作静默失效）。
                  batchDeleteMediaItems({
                    seasons: [{
                      serverId: record.serverId,
                      title: record.showTitle,
                      season: record.season
                    }]
                  })
                    .then(() => {
                      message.success(t('mediaFetch.mediaItemList.deleteSeasonSuccess', { season: record.season }));
                      loadItems(pagination.current, pagination.pageSize);
                    })
                    .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
                }}
                okText={t('mediaFetch.mediaItemList.confirm')}
                cancelText={t('mediaFetch.mediaItemList.cancel')}
              >
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                  {t('mediaFetch.mediaItemList.deleteSeasonAct')}
                </Button>
              </Popconfirm>
              <Button
                type="link"
                size="small"
                icon={<ImportOutlined />}
                onClick={() => {
                  // 导入该季度：直接用季度节点自带的 showTitle/serverId（同上，不反推父节点）
                  importMediaItems({
                    seasons: [{
                      serverId: record.serverId,
                      title: record.showTitle,
                      season: record.season
                    }]
                  })
                    .then((res) => {
                      message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                      loadItems(pagination.current, pagination.pageSize);
                    })
                    .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
                }}
              >
                {t('mediaFetch.mediaItemList.importSeasonAct')}
              </Button>
            </Space>
          );
        }

        // 电影显示导入、编辑和删除按钮
        if (record.mediaType === 'movie') {
          return (
            <Space size="small">
              <Button
                type="link"
                size="small"
                icon={<ImportOutlined />}
                onClick={() => {
                  // 导入电影
                  importMediaItems({
                    itemIds: [record.id]
                  })
                    .then((res) => {
                      message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                      loadItems(pagination.current, pagination.pageSize);
                    })
                    .catch((error) => message.error(t('mediaFetch.mediaItemList.importFailedWith') + (error.message || t('mediaFetch.mediaItemList.unknownError'))));
                }}
              >
                {t('mediaFetch.mediaItemList.import')}
              </Button>
              <Button
                type="link"
                size="small"
                icon={<EditOutlined />}
                onClick={() => handleEdit(record)}
              >
                {t('mediaFetch.mediaItemList.edit')}
              </Button>
              <Popconfirm
                title={t('mediaFetch.mediaItemList.confirmDelete')}
                onConfirm={() => handleDelete(record)}
                okText={t('mediaFetch.mediaItemList.confirm')}
                cancelText={t('mediaFetch.mediaItemList.cancel')}
              >
                <Button
                  type="link"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                >
                  {t('mediaFetch.mediaItemList.delete')}
                </Button>
              </Popconfirm>
            </Space>
          );
        }

        // 单集显示编辑和删除按钮
        return (
          <Space size="small">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => handleEdit(record)}
            >
              {t('mediaFetch.mediaItemList.edit')}
            </Button>
            <Popconfirm
              title={t('mediaFetch.mediaItemList.confirmDelete')}
              onConfirm={() => handleDelete(record)}
              okText={t('mediaFetch.mediaItemList.confirm')}
              cancelText={t('mediaFetch.mediaItemList.cancel')}
            >
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
              >
                {t('mediaFetch.mediaItemList.delete')}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: handleSelectionChange,
    columnWidth: 48, // 设置复选框列宽度
    // 所有项都可以选择
  };

  // 渲染项目操作按钮 - 桌面端
  const renderItemActions = (record) => {
    // 剧集组显示导入整部和删除整部按钮
    if (record.isGroup && record.mediaType === 'tv_show') {
      return [
        <Button
          key="import-show"
          type="link"
          size="small"
          icon={<ImportOutlined />}
          onClick={() => {
            // 导入整部剧集
            importMediaItems({
              shows: [{
                serverId: serverId,
                title: record.title
              }]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
          }}
        >
          {t('mediaFetch.mediaItemList.importWhole')}
        </Button>,
        <Popconfirm
          key="delete-show"
          title={t('mediaFetch.mediaItemList.confirmDeleteShow', { title: record.title })}
          onConfirm={() => {
            // 删除整部剧集
            batchDeleteMediaItems({
              shows: [{
                serverId: serverId,
                title: record.title
              }]
            })
              .then(() => {
                message.success(t('mediaFetch.mediaItemList.deleteShowSuccess', { title: record.title }));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
          }}
          okText={t('mediaFetch.mediaItemList.confirm')}
          cancelText={t('mediaFetch.mediaItemList.cancel')}
        >
          <Button type="link" size="small" danger icon={<DeleteOutlined />}>
            {t('mediaFetch.mediaItemList.deleteWhole')}
          </Button>
        </Popconfirm>
      ];
    }

    // 季度显示导入、编辑和删除按钮
    if (record.mediaType === 'tv_season') {
      return [
        <Button
          key="import-season"
          type="link"
          size="small"
          icon={<ImportOutlined />}
          onClick={() => {
            // 导入该季度：直接用季度节点自带的 showTitle/serverId，不从 key 反推父节点
            importMediaItems({
              seasons: [{
                serverId: record.serverId,
                title: record.showTitle,
                season: record.season
              }]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
          }}
        >
          {t('mediaFetch.mediaItemList.importSeasonAct')}
        </Button>,
        <Button
          key="edit-season"
          type="link"
          size="small"
          icon={<EditOutlined />}
          onClick={() => handleEdit(record)}
        >
          {t('mediaFetch.mediaItemList.edit')}
        </Button>,
        <Popconfirm
          key="delete-season"
          title={t('mediaFetch.mediaItemList.confirmDeleteSeason', { season: record.season })}
          onConfirm={() => {
            // 删除该季度：直接用季度节点自带的 showTitle/serverId，不从 key 反推父节点
            batchDeleteMediaItems({
              seasons: [{
                serverId: record.serverId,
                title: record.showTitle,
                season: record.season
              }]
            })
              .then(() => {
                message.success(t('mediaFetch.mediaItemList.deleteSeasonSuccess', { season: record.season }));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
          }}
          okText={t('mediaFetch.mediaItemList.confirm')}
          cancelText={t('mediaFetch.mediaItemList.cancel')}
        >
          <Button type="link" size="small" danger icon={<DeleteOutlined />}>
            {t('mediaFetch.mediaItemList.deleteSeasonAct')}
          </Button>
        </Popconfirm>
      ];
    }

    // 电影显示导入、编辑和删除按钮
    if (record.mediaType === 'movie') {
      return [
        <Button
          key="import-movie"
          type="link"
          size="small"
          icon={<ImportOutlined />}
          onClick={() => {
            // 导入电影
            importMediaItems({
              itemIds: [record.id]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch((error) => message.error(t('mediaFetch.mediaItemList.importFailedWith') + (error.message || t('mediaFetch.mediaItemList.unknownError'))));
          }}
        >
          {t('mediaFetch.mediaItemList.import')}
        </Button>,
        <Button
          key="edit-movie"
          type="link"
          size="small"
          icon={<EditOutlined />}
          onClick={() => handleEdit(record)}
        >
          {t('mediaFetch.mediaItemList.edit')}
        </Button>,
        <Popconfirm
          key="delete-movie"
          title={t('mediaFetch.mediaItemList.confirmDelete')}
          onConfirm={() => handleDelete(record)}
          okText={t('mediaFetch.mediaItemList.confirm')}
          cancelText={t('mediaFetch.mediaItemList.cancel')}
        >
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
          >
            {t('mediaFetch.mediaItemList.delete')}
          </Button>
        </Popconfirm>
      ];
    }

    // 单集显示编辑和删除按钮
    return [
      <Button
        key="edit-episode"
        type="link"
        size="small"
        icon={<EditOutlined />}
        onClick={() => handleEdit(record)}
      >
        {t('mediaFetch.mediaItemList.edit')}
      </Button>,
      <Popconfirm
        key="delete-episode"
        title={t('mediaFetch.mediaItemList.confirmDelete')}
        onConfirm={() => handleDelete(record)}
        okText={t('mediaFetch.mediaItemList.confirm')}
        cancelText={t('mediaFetch.mediaItemList.cancel')}
      >
        <Button
          type="link"
          size="small"
          danger
          icon={<DeleteOutlined />}
        >
          {t('mediaFetch.mediaItemList.delete')}
        </Button>
      </Popconfirm>
    ];
  };

  // 渲染移动端操作菜单
  const renderMobileActions = (record) => {
    const items = [];

    if (record.isGroup && record.mediaType === 'tv_show') {
      items.push(
        {
          key: 'import-show',
          icon: <ImportOutlined />,
          label: t('mediaFetch.mediaItemList.importWhole'),
          onClick: () => {
            importMediaItems({
              shows: [{
                serverId: serverId,
                title: record.title
              }]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
          }
        },
        {
          key: 'delete-show',
          icon: <DeleteOutlined />,
          label: t('mediaFetch.mediaItemList.deleteWhole'),
          danger: true,
          onClick: () => {
            // 这里会触发Popconfirm，但为了简化，我们直接执行
            batchDeleteMediaItems({
              shows: [{
                serverId: serverId,
                title: record.title
              }]
            })
              .then(() => {
                message.success(t('mediaFetch.mediaItemList.deleteShowSuccess', { title: record.title }));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
          }
        }
      );
    } else if (record.mediaType === 'tv_season') {
      items.push(
        {
          key: 'import-season',
          icon: <ImportOutlined />,
          label: t('mediaFetch.mediaItemList.importSeasonAct'),
          onClick: () => {
            // 直接用季度节点自带的 showTitle/serverId，不从 key 反推父节点
            importMediaItems({
              seasons: [{
                serverId: record.serverId,
                title: record.showTitle,
                season: record.season
              }]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.importFailed')));
          }
        },
        {
          key: 'edit-season',
          icon: <EditOutlined />,
          label: t('mediaFetch.mediaItemList.edit'),
          onClick: () => handleEdit(record)
        },
        {
          key: 'delete-season',
          icon: <DeleteOutlined />,
          label: t('mediaFetch.mediaItemList.deleteSeasonAct'),
          danger: true,
          onClick: () => {
            // 直接用季度节点自带的 showTitle/serverId，不从 key 反推父节点
            batchDeleteMediaItems({
              seasons: [{
                serverId: record.serverId,
                title: record.showTitle,
                season: record.season
              }]
            })
              .then(() => {
                message.success(t('mediaFetch.mediaItemList.deleteSeasonSuccess', { season: record.season }));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch(() => message.error(t('mediaFetch.mediaItemList.deleteFailed')));
          }
        }
      );
    } else if (record.mediaType === 'movie') {
      items.push(
        {
          key: 'import-movie',
          icon: <ImportOutlined />,
          label: t('mediaFetch.mediaItemList.import'),
          onClick: () => {
            importMediaItems({
              itemIds: [record.id]
            })
              .then((res) => {
                message.success(res.data.message || t('mediaFetch.mediaItemList.importSubmitted'));
                loadItems(pagination.current, pagination.pageSize);
              })
              .catch((error) => message.error(t('mediaFetch.mediaItemList.importFailedWith') + (error.message || t('mediaFetch.mediaItemList.unknownError'))));
          }
        },
        {
          key: 'edit-movie',
          icon: <EditOutlined />,
          label: t('mediaFetch.mediaItemList.edit'),
          onClick: () => handleEdit(record)
        },
        {
          key: 'delete-movie',
          icon: <DeleteOutlined />,
          label: t('mediaFetch.mediaItemList.delete'),
          danger: true,
          onClick: () => handleDelete(record)
        }
      );
    } else {
      // 单集
      items.push(
        {
          key: 'edit-episode',
          icon: <EditOutlined />,
          label: t('mediaFetch.mediaItemList.edit'),
          onClick: () => handleEdit(record)
        },
        {
          key: 'delete-episode',
          icon: <DeleteOutlined />,
          label: t('mediaFetch.mediaItemList.delete'),
          danger: true,
          onClick: () => handleDelete(record)
        }
      );
    }

    return (
      <Dropdown
        menu={{ items }}
        trigger={['click']}
        placement="bottomRight"
      >
        <Button
          type="text"
          icon={<MoreOutlined />}
          size="middle"
          style={{ fontSize: '16px', width: '32px', height: '32px' }}
        />
      </Dropdown>
    );
  };

  return (
    <>
      <Card
        title={
          <div>
            <span className="desktop-only">{t('mediaFetch.mediaItemList.titleDesktop')}</span>
            <span className="mobile-only">{t('mediaFetch.mediaItemList.titleMobile')}</span>
          </div>
        }
        extra={
          <div className="desktop-only">
            <Space wrap>
              <Button
                icon={<TableOutlined />}
                type={viewMode === 'table' ? 'primary' : 'default'}
                onClick={() => setViewMode('table')}
                size="small"
              >
                {t('mediaFetch.mediaItemList.table')}
              </Button>
              <Button
                icon={<AppstoreOutlined />}
                type={viewMode === 'card' ? 'primary' : 'default'}
                onClick={() => setViewMode('card')}
                size="small"
              >
                {t('mediaFetch.mediaItemList.card')}
              </Button>
              <Popover
                trigger="click"
                placement="bottom"
                onOpenChange={(open) => {
                  if (open) {
                    setSearchInput(searchText);
                  }
                }}
                content={(
                  <div style={{ width: 250 }}>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input
                        placeholder={t('mediaFetch.mediaItemList.searchTitlePlaceholder')}
                        value={searchInput}
                        onChange={(e) => setSearchInput(e.target.value)}
                        onPressEnter={() => {
                          setSearchText(searchInput);
                        }}
                        prefix={<SearchOutlined />}
                        allowClear
                      />
                      <div className="flex gap-2 justify-end">
                        <Button
                          size="small"
                          onClick={() => {
                            setSearchInput('');
                            setSearchText('');
                          }}
                        >
                          {t('mediaFetch.mediaItemList.clear')}
                        </Button>
                        <Button
                          type="primary"
                          size="small"
                          icon={<SearchOutlined />}
                          onClick={() => {
                            setSearchText(searchInput);
                          }}
                        >
                          {t('mediaFetch.mediaItemList.search')}
                        </Button>
                      </div>
                    </Space>
                  </div>
                )}
              >
                <Button icon={<SearchOutlined />} size="small">
                  {t('mediaFetch.mediaItemList.search')}{searchText && <span className="ml-1 text-blue-500">({searchText})</span>}
                </Button>
              </Popover>
            </Space>
          </div>
        }
      >
        {/* 移动端头部布局 */}
        <div className="mobile-only" style={{ marginBottom: 20 }}>
          <Row gutter={[12, 12]} align="middle">
            <Col span={12}>
              <Button
                icon={<TableOutlined />}
                type={viewMode === 'table' ? 'primary' : 'default'}
                onClick={() => setViewMode('table')}
                size="large"
                block
                style={{ height: '44px', fontSize: '16px' }}
              >
                {t('mediaFetch.mediaItemList.table')}
              </Button>
            </Col>
            <Col span={12}>
              <Button
                icon={<AppstoreOutlined />}
                type={viewMode === 'card' ? 'primary' : 'default'}
                onClick={() => setViewMode('card')}
                size="large"
                block
                style={{ height: '44px', fontSize: '16px' }}
              >
                {t('mediaFetch.mediaItemList.card')}
              </Button>
            </Col>
          </Row>
          <div style={{ marginTop: 16 }}>
            <Popover
              trigger="click"
              placement="bottom"
              onOpenChange={(open) => {
                if (open) {
                  setSearchInput(searchText);
                }
              }}
              content={(
                <div style={{ width: 250 }}>
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Input
                      placeholder={t('mediaFetch.mediaItemList.searchTitlePlaceholder')}
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      onPressEnter={() => {
                        setSearchText(searchInput);
                      }}
                      prefix={<SearchOutlined />}
                      allowClear
                    />
                    <div className="flex gap-2 justify-end">
                      <Button
                        size="small"
                        onClick={() => {
                          setSearchInput('');
                          setSearchText('');
                        }}
                      >
                        {t('mediaFetch.mediaItemList.clear')}
                      </Button>
                      <Button
                        type="primary"
                        size="small"
                        icon={<SearchOutlined />}
                        onClick={() => {
                          setSearchText(searchInput);
                        }}
                      >
                        {t('mediaFetch.mediaItemList.search')}
                      </Button>
                    </div>
                  </Space>
                </div>
              )}
            >
              <Button icon={<SearchOutlined />} size="large" block style={{ height: '44px', fontSize: '16px' }}>
                {t('mediaFetch.mediaItemList.search')}{searchText && <span className="ml-1 text-blue-500">({searchText})</span>}
              </Button>
            </Popover>
          </div>
        </div>

        <Space style={{ marginBottom: 20, width: '100%' }} direction="vertical" size="middle">
        </Space>

        {viewMode === 'table' ? (
          <div>
            <Table
              columns={columns}
              dataSource={items}
              loading={loading}
              rowSelection={rowSelection}
              pagination={false}
              onChange={handleTableChange}
              expandable={{
                defaultExpandAllRows: false,
              }}
              scroll={{ x: 800 }}
              size="small"
              className="desktop-only"
            />
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: '16px' }}>
              <Pagination
                {...pagination}
                showSizeChanger={true}
                showQuickJumper={true}
                position={['bottomCenter']}
                hideOnSinglePage={false}
                size="small"
                pageSizeOptions={['10', '20', '50', '100', '200']}
                onChange={(page, pageSize) => loadItems(page, pageSize)}
              />
            </div>
          </div>
        ) : (
          <div>
            <List
              loading={loading}
              dataSource={items}
              pagination={false}
              renderItem={(item) => (
                <List.Item key={item.key} style={{ padding: '12px 0' }} actions={[
                  <div key="mobile-actions" className="mobile-only">{renderMobileActions(item)}</div>,
                  <div key="desktop-actions" className="desktop-only">{renderItemActions(item)}</div>
                ]}>
                  <List.Item.Meta
                    title={
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <Checkbox checked={selectedRowKeys.includes(item.key)} onChange={(e) => {
                          if (e.target.checked) setSelectedRowKeys([...selectedRowKeys, item.key]);
                          else setSelectedRowKeys(selectedRowKeys.filter(k => k !== item.key));
                        }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 16, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8 }}>
                            {item.title}
                            {item.isGroup ? (
                              item.importedCount !== undefined && item.episodeCount !== undefined ? (
                                item.importedCount === item.episodeCount && item.episodeCount > 0 ?
                                  <Tag color="success" style={{ marginLeft: 4 }}>{t('mediaFetch.mediaItemList.allImported')}</Tag> :
                                item.importedCount > 0 ?
                                  <Tag color="processing" style={{ marginLeft: 4 }}>{item.importedCount}/{item.episodeCount}</Tag> :
                                  <Tag style={{ marginLeft: 4 }}>{t('mediaFetch.mediaItemList.notImported')}</Tag>
                              ) : null
                            ) : (
                              item.isImported ?
                                <Tag color="success" style={{ marginLeft: 4 }}>{t('mediaFetch.mediaItemList.imported')}</Tag> :
                                <Tag style={{ marginLeft: 4 }}>{t('mediaFetch.mediaItemList.notImported')}</Tag>
                            )}
                          </div>
                          {item.year && <div style={{ color: 'var(--color-text-secondary)' }}>{item.year}</div>}
                        </div>
                      </div>
                    }
                    description={null}
                  />
                </List.Item>
              )}
            />
            <div style={{ display: 'flex', justifyContent: 'center', marginTop: 16 }}>
              <Pagination
                {...pagination}
                showSizeChanger={true}
                showQuickJumper={true}
                hideOnSinglePage={false}
                size="small"
                pageSizeOptions={['10', '20', '50', '100', '200']}
                onChange={(page, pageSize) => loadItems(page, pageSize)}
              />
            </div>
          </div>
        )}</Card>

      <MediaItemEditor
        visible={editorVisible}
        item={editingItem}
        onClose={() => setEditorVisible(false)}
        onSaved={handleEditorSaved}
      />

      <EpisodeListModal
        visible={episodeModalVisible}
        onClose={() => {
          setEpisodeModalVisible(false);
          setSelectedShow(null);
        }}
        serverId={selectedShow?.serverId}
        title={selectedShow?.title}
        season={selectedShow?.season}
        onRefresh={() => loadItems(pagination.current, pagination.pageSize)}
      />
    </>
  );
};

export default MediaItemList;


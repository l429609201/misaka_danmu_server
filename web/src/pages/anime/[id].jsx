import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  addSourceToAnime,
  checkReassociationConflicts,
  deleteAnimeSource,
  deleteAnimeSourceSingle,
  fillMissingEpisodes,
  fullSourceUpdate,
  getAnimeDetail,
  getAnimeLibrary,
  getAnimeSource,
  incrementalUpdate,
  reassociateWithResolution,
  setAnimeSource,
  toggleSourceFavorite,
  toggleSourceIncremental,
  toggleSourceFinished,
} from '../../apis'
import {
  Breadcrumb,
  Button,
  Card,
  Col,
  Dropdown,
  Empty,
  Input,
  List,
  message,
  Modal,
  Row,
  Space,
  Switch,
  Table,
  Tooltip,
  Tag,
} from 'antd'
import { DANDAN_TYPE_DESC_MAPPING } from '../../configs'
import { RoutePaths } from '../../general/RoutePaths'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon'
import classNames from 'classnames'
import { padStart } from 'lodash'
import { EditOutlined, HomeOutlined, MenuOutlined } from '@ant-design/icons'
import { useModal } from '../../ModalContext'
import { useMessage } from '../../MessageContext'
import { AddSourceModal } from '../../components/AddSourceModal'
import { SplitSourceModal } from '../../components/SplitSourceModal'
import { useDebounce } from '../../hooks/useDebounce'
import ReassociationConflictModal from './components/ReassociationConflictModal'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store'
import { ResponsiveTable } from '@/components/ResponsiveTable'

export const AnimeDetail = () => {
  const { id } = useParams()
  const [loading, setLoading] = useState(true)
  const [sourceList, setSourceList] = useState([])
  const [animeDetail, setAnimeDetail] = useState({})
  const [libraryList, setLibraryList] = useState([])
  const [editOpen, setEditOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [conflictModalOpen, setConflictModalOpen] = useState(false)
  const [conflictData, setConflictData] = useState(null)
  const [targetAnimeId, setTargetAnimeId] = useState(null)
  const [targetAnimeTitle, setTargetAnimeTitle] = useState('')
  const [selectedRows, setSelectedRows] = useState([])
  const [isAddSourceModalOpen, setIsAddSourceModalOpen] = useState(false)
  const [isSplitSourceModalOpen, setIsSplitSourceModalOpen] = useState(false)
  const isMobile = useAtomValue(isMobileAtom)

  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 10,
    total: 0,
  })

  const navigate = useNavigate()
  const modalApi = useModal()
  const messageApi = useMessage()
  const deleteFilesRef = useRef(true) // 删除时是否同时删除弹幕文件，默认为 true

  const totalEpisodeCount = useMemo(() => {
    return sourceList.reduce((total, item) => {
      return total + item.episodeCount
    }, 0)
  }, [sourceList])

  const getDetail = async () => {
    setLoading(true)
    try {
      // 如果 animeId 为 0 或无效，直接返回到库页面
      if (!id || Number(id) === 0) {
        messageApi.error('无效的作品ID')
        navigate('/library')
        return
      }

      const [detailRes, sourceRes] = await Promise.all([
        getAnimeDetail({
          animeId: Number(id),
        }),
        getAnimeSource({
          animeId: Number(id),
        }),
      ])
      setAnimeDetail(detailRes.data)
      setSourceList(sourceRes.data)
      setLoading(false)
    } catch (error) {
      messageApi.error('获取作品详情失败')
      navigate('/library')
    }
  }

  const handleAddSourceSuccess = () => {
    setIsAddSourceModalOpen(false)
    getDetail() // 添加成功后刷新数据源列表
  }

  const handleEditSource = async (init = true) => {
    try {
      const res = await getAnimeLibrary({
        keyword: keyword,
        page: pagination.current,
        pageSize: pagination.pageSize,
      })
      setLibraryList(res.data?.list || [])
      setPagination(prev => ({
        ...prev,
        total: res.data?.total || 0,
      }))
      if (init) {
        setEditOpen(true)
      }
    } catch (error) {
      messageApi.error('获取数据源失败')
    }
  }

  const handleKeywordChange = useDebounce(e => {
    setKeyword(e.target.value)
  }, 500)

  useEffect(() => {
    setPagination(n => {
      return {
        ...n,
        current: 1,
      }
    })
  }, [keyword])

  useEffect(() => {
    handleEditSource(false)
  }, [keyword, pagination.pageSize, pagination.current])

  const handleConfirmSource = async item => {
    try {
      // 1. 先检测冲突
      const response = await checkReassociationConflicts({
        sourceAnimeId: animeDetail.animeId,
        targetAnimeId: item.animeId,
      })

      if (response.data.hasConflict) {
        // 2. 有冲突,打开冲突解决对话框
        setConflictData(response.data)
        setTargetAnimeId(item.animeId)
        setTargetAnimeTitle(item.title)
        setConflictModalOpen(true)
        setEditOpen(false)
      } else {
        // 3. 无冲突,直接关联
        modalApi.confirm({
          title: '关联数据源',
          zIndex: 1002,
          content: (
            <div>
              您确定要将当前作品的所有数据源关联到 "{item.title}" (ID:
              {item.animeId}) 吗？
              <br />
              此操作不可撤销！
            </div>
          ),
          okText: '确认',
          cancelText: '取消',
          onOk: async () => {
            try {
              await setAnimeSource({
                sourceAnimeId: animeDetail.animeId,
                targetAnimeId: item.animeId,
              })
              messageApi.success('关联成功')
              setEditOpen(false)
              navigate(RoutePaths.LIBRARY)
            } catch (error) {
              messageApi.error(`关联失败:${error.message}`)
            }
          },
        })
      }
    } catch (error) {
      messageApi.error(`检测冲突失败:${error.message}`)
    }
  }

  // 处理冲突解决
  const handleResolveConflict = async resolutions => {
    try {
      await reassociateWithResolution({
        sourceAnimeId: animeDetail.animeId,
        targetAnimeId: targetAnimeId,
        resolutions: resolutions,
      })
      messageApi.success('关联成功')
      setConflictModalOpen(false)
      navigate(RoutePaths.LIBRARY)
    } catch (error) {
      messageApi.error(`关联失败:${error.message}`)
    }
  }

  const handleBatchDelete = () => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: '删除数据源',
      zIndex: 1002,
      content: (
        <div>
          您确定要删除选中的 {selectedRows.length} 个数据源吗？
          <br />
          此操作将在后台提交一个批量删除任务。
          <div className="flex items-center gap-2 mt-3">
            <span>同时删除弹幕文件：</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeSource({
            sourceIds: selectedRows?.map(it => it.sourceId),
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`提交批量删除任务失败:${error.message}`)
        }
      },
    })
  }

  const handleDeleteSingle = record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: '删除数据源',
      zIndex: 1002,
      content: (
        <div>
          您确定要删除这个数据源吗？
          <br />
          此操作将在后台提交一个删除任务。
          <div className="flex items-center gap-2 mt-3">
            <span>同时删除弹幕文件：</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeSourceSingle({
            sourceId: record.sourceId,
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`提交删除任务失败:${error.message}`)
        }
      },
    })
  }

  const handleIncrementalUpdate = record => {
    modalApi.confirm({
      title: '增量刷新',
      zIndex: 1002,
      content: (
        <div>
          您确定要为 '{animeDetail.title}' 的这个数据源执行增量更新吗？
          <br />
          此操作将尝试获取下一集。
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await incrementalUpdate({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`启动增量更新任务失败: ${error.message}`)
        }
      },
    })
  }

  const handleFullSourceUpdate = record => {
    modalApi.confirm({
      title: '全量刷新',
      zIndex: 1002,
      content: (
        <div>您确定要为 '{animeDetail.title}' 的这个数据源执行全量更新吗？</div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await fullSourceUpdate({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`启动刷新任务失败: ${error.message}`)
        }
      },
    })
  }

  const handleFillMissing = record => {
    modalApi.confirm({
      title: '补全缺失分集',
      zIndex: 1002,
      content: (
        <div>
          将自动检索该源的全部分集，与已有分集对比后只下载缺失的部分。
          <br />
          确定要为 '{animeDetail.title}' 的这个数据源执行分集补全吗？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await fillMissingEpisodes({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`启动补全任务失败: ${error.message}`)
        }
      },
    })
  }

  const goTask = res => {
    modalApi.confirm({
      title: '提示',
      zIndex: 1002,
      content: (
        <div>
          {res.data?.message || '任务已提交'}
          <br />
          是否立即跳转到任务管理器查看进度？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        navigate(`${RoutePaths.TASK}?status=all`)
      },
      onCancel: () => {
        getDetail()
        setSelectedRows([])
      },
    })
  }

  const operateWidth = sourceList?.every(it => it.providerName === 'custom')
    ? 90
    : 180
  const columns = [
    {
      title: '',
      key: 'selection',
      width: 50,
      render: (_, record) => {
        const isSelected = selectedRows.some(row => row.sourceId === record.sourceId)
        return (
          <div
            className="cursor-pointer flex items-center justify-center"
            onClick={() => {
              if (isSelected) {
                setSelectedRows(selectedRows.filter(row => row.sourceId !== record.sourceId))
              } else {
                setSelectedRows([...selectedRows, record])
              }
            }}
          >
            {isSelected ? (
              <div className="w-4 h-4 bg-primary rounded flex items-center justify-center">
                <span className="text-white text-xs">✓</span>
              </div>
            ) : (
              <div className="w-4 h-4 border border-gray-300 dark:border-gray-600 rounded"></div>
            )}
          </div>
        )
      },
    },
    {
      title: '源提供方',
      dataIndex: 'providerName',
      key: 'providerName',
      width: 100,
    },
    {
      title: '媒体库ID',
      dataIndex: 'mediaId',
      key: 'mediaId',
      width: 200,
    },
    {
      title: '状态',
      width: 100,
      dataIndex: 'isFavorited',
      key: 'isFavorited',
      render: (_, record) => {
        return (
          <Space>
            {record.isFavorited && (
              <MyIcon
                icon="favorites-fill"
                size={20}
                className="text-yellow-400"
              />
            )}
            {record.incrementalRefreshEnabled && (
              <MyIcon icon="clock" size={20} className="text-red-400" />
            )}
            {record.isFinished && (
              <MyIcon icon="wanjie1" size={20} className="text-blue-500" />
            )}
          </Space>
        )
      },
    },

    {
      title: '收录时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: '操作',
      width: operateWidth,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title="批量编辑该源的所有分集">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  navigate(`/episode/${record.sourceId}?animeId=${id}&batchEdit=all`)
                }}
              >
                <EditOutlined style={{ fontSize: 18 }} />
              </span>
            </Tooltip>
            {record?.providerName !== 'custom' && (
              <Dropdown
                menu={{
                  items: [
                    {
                      key: 'favorite',
                      label: record.isFavorited ? '取消标记' : '精确标记',
                      icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={classNames({ 'text-yellow-400': record.isFavorited })} />,
                      onClick: async () => {
                        try {
                          await toggleSourceFavorite({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                          ))
                        } catch (error) {
                          alert(`操作失败: ${error.message}`)
                        }
                      },
                    },
                    {
                      key: 'incremental',
                      label: record.incrementalRefreshEnabled ? '关闭定时' : '开启定时',
                      icon: <MyIcon icon="clock" size={16} className={classNames({ 'text-red-400': record.incrementalRefreshEnabled })} />,
                      onClick: async () => {
                        try {
                          await toggleSourceIncremental({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                          ))
                        } catch (error) {
                          alert(`操作失败: ${error.message}`)
                        }
                      },
                    },
                    {
                      key: 'finished',
                      label: record.isFinished ? '取消完结' : '标记完结',
                      icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                      onClick: async () => {
                        try {
                          await toggleSourceFinished({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                          ))
                        } catch (error) {
                          alert(`操作失败: ${error.message}`)
                        }
                      },
                    },
                  ],
                }}
                trigger={['click']}
              >
                <span className="cursor-pointer hover:text-primary">
                  <MenuOutlined style={{ fontSize: 18 }} />
                </span>
              </Dropdown>
            )}
            <Tooltip title="分集列表">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  navigate(`/episode/${record.sourceId}?animeId=${id}`)
                }}
              >
                <MyIcon icon="book" size={20}></MyIcon>
              </span>
            </Tooltip>
            {record?.providerName !== 'custom' && (
              <Dropdown
                menu={{
                  items: [
                    {
                      key: 'incremental',
                      icon: <MyIcon icon="image_134571035400041" size={16} />,
                      label: '增量获取',
                      onClick: () => handleIncrementalUpdate(record),
                    },
                    {
                      key: 'fill_missing',
                      icon: <MyIcon icon="a-image_0583743498849421" size={16} />,
                      label: '补全缺失',
                      onClick: () => handleFillMissing(record),
                    },
                    {
                      key: 'full_update',
                      icon: <MyIcon icon="image_488307257272375" size={16} />,
                      label: '全量更新',
                      onClick: () => handleFullSourceUpdate(record),
                    },
                  ],
                }}
                trigger={['click']}
              >
                <Tooltip title="更新操作">
                  <span className="cursor-pointer hover:text-primary">
                    <MyIcon icon="refresh" size={20} />
                  </span>
                </Tooltip>
              </Dropdown>
            )}

            <Tooltip title="删除数据源">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  handleDeleteSingle(record)
                }}
              >
                <MyIcon icon="delete" size={20}></MyIcon>
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  const rowSelection = {
    onChange: (_, selectedRows) => {
      setSelectedRows(selectedRows)
    },
  }

  useEffect(() => {
    getDetail()
  }, [])

  let imageSrc = animeDetail.localImagePath || animeDetail.imageUrl
  // 兼容旧的、错误的缓存路径
  if (imageSrc && imageSrc.startsWith('/images/')) {
    imageSrc = imageSrc.replace('/images/', '/data/images/')
  }

  return (
    <div className="my-6">
      <Breadcrumb
        className="!mb-4"
        items={[
          {
            title: (
              <Link to="/">
                <HomeOutlined />
              </Link>
            ),
          },
          {
            title: <Link to="/library">弹幕库</Link>,
          },
          {
            title: animeDetail.title,
          },
        ]}
      />
      <Card loading={loading} title={null}>
        <Row gutter={[12, 12]}>
          <Col md={20} xs={24}>
            <div className="flex items-center justify-start gap-4">
              {imageSrc && <img src={imageSrc} className="h-[100px]" />}
              <div>
                <div className="text-xl font-bold mb-3 break-all">
                  {animeDetail.title}（Season{' '}
                  {padStart(animeDetail.season, 2, '0')}）
                </div>
                <div className="flex items-center justify-start gap-2">
                  <span>总集数: {totalEpisodeCount}</span>|
                  <span>已关联 {sourceList.length} 个源</span>
                </div>
              </div>
            </div>
          </Col>
          <Col md={4} xs={24}>
            <div className="h-full flex flex-col items-center justify-center gap-2">
              <Button
                type="primary"
                block
                onClick={() => {
                  handleEditSource()
                }}
              >
                调整关联数据源
              </Button>
              <Button
                block
                onClick={() => {
                  setIsSplitSourceModalOpen(true)
                }}
                disabled={!sourceList?.length}
              >
                拆分数据源
              </Button>
            </div>
          </Col>
        </Row>
        <div className="mt-6">
          <div className="mb-3 text-sm text-gray-600 dark:text-gray-400">
            💡 点击卡片或前面的方框可选中/取消选中数据源，用于批量操作
          </div>
          <div className="flex items-center gap-4 mb-4">
            <Button
              onClick={() => {
                handleBatchDelete()
              }}
              type="primary"
              disabled={!selectedRows.length}
            >
              删除选中
            </Button>
            <Button
              onClick={() => {
                setIsAddSourceModalOpen(true)
              }}
            >
              添加数据源
            </Button>
          </div>
          {!!sourceList?.length ? (
            <ResponsiveTable
              pagination={false}
              size="small"
              dataSource={sourceList}
              columns={columns}
              rowKey={'sourceId'}
              scroll={{ x: '100%' }}
              renderCard={(record) => {
                const isSelected = selectedRows.some(row => row.sourceId === record.sourceId);
                return (
                  <div
                    className={`p-3 rounded-lg transition-all relative cursor-pointer ${isSelected ? 'shadow-lg ring-2 ring-pink-400/50 bg-pink-50/30 dark:bg-pink-900/10' : 'hover:shadow-md hover:bg-gray-50 dark:hover:bg-gray-800/30'}`}
                    onClick={(e) => {
                      // 如果点击的是按钮或链接，不触发选择
                      if (
                        e.target.closest('.ant-btn') ||
                        e.target.closest('a')
                      ) {
                        return
                      }

                      // 切换选中状态
                      if (isSelected) {
                        setSelectedRows(selectedRows.filter(row => row.sourceId !== record.sourceId))
                      } else {
                        setSelectedRows([...selectedRows, record])
                      }
                    }}
                  >
                    <div className="space-y-3 relative">
                      {isSelected && (
                        <div className="absolute -top-1 -right-1 w-3 h-3 bg-pink-400 rounded-full border-2 border-white dark:border-gray-800 z-10"></div>
                      )}
                      <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <Tag color="blue" className="text-xs">
                                {record.providerName}
                              </Tag>
                              <span className="text-sm font-medium text-gray-600 dark:text-gray-400">
                                ID: {record.sourceId}
                              </span>
                            </div>
                            <Button
                              size="small"
                              type="text"
                              danger
                              className="flex-shrink-0"
                              icon={<MyIcon icon="delete" size={16} />}
                              title="删除数据源"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDeleteSingle(record)
                              }}
                            />
                          </div>
                          <div className="font-semibold text-base mb-2 break-words">
                            媒体库ID: {record.mediaId}
                          </div>
                          <div className="space-y-1">
                            <div className="flex items-center gap-4 text-sm">
                              <span className="flex items-center gap-1">
                                <MyIcon icon="clock" size={14} className="text-gray-500" />
                                <span className="text-gray-600 dark:text-gray-400">
                                  {dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}
                                </span>
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <div className="flex gap-1">
                                {record.isFavorited && (
                                  <MyIcon icon="favorites-fill" size={16} className="text-yellow-400" />
                                )}
                                {record.incrementalRefreshEnabled && (
                                  <MyIcon icon="clock" size={16} className="text-red-400" />
                                )}
                                {record.isFinished && (
                                  <MyIcon icon="wanjie1" size={16} className="text-blue-500" />
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div className="pt-1 border-t border-gray-200 dark:border-gray-700">
                        <div className="flex justify-end gap-2 flex-wrap">
                          {isMobile ? (
                            <Tooltip title="分集列表">
                              <Button
                                size="small"
                                type="text"
                                icon={<MyIcon icon="book" size={16} />}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  navigate(`/episode/${record.sourceId}?animeId=${id}`)
                                }}
                              />
                            </Tooltip>
                          ) : (
                            <Button
                              size="small"
                              type="text"
                              icon={<MyIcon icon="book" size={16} />}
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate(`/episode/${record.sourceId}?animeId=${id}`)
                              }}
                            >
                              分集列表
                            </Button>
                          )}
                          {record.providerName !== 'custom' && (
                            <>
                              {isMobile ? (
                                <Dropdown
                                  menu={{
                                    items: [
                                      {
                                        key: 'favorite',
                                        label: record.isFavorited ? '取消标记' : '精确标记',
                                        icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={record.isFavorited ? 'text-yellow-400' : ''} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFavorite({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                      {
                                        key: 'incremental',
                                        label: record.incrementalRefreshEnabled ? '关闭定时' : '开启定时',
                                        icon: <MyIcon icon="clock" size={16} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceIncremental({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                      {
                                        key: 'finished',
                                        label: record.isFinished ? '取消完结' : '标记完结',
                                        icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFinished({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                    ],
                                  }}
                                  trigger={['click']}
                                >
                                  <Button size="small" type="text" icon={<MenuOutlined />} onClick={(e) => e.stopPropagation()} />
                                </Dropdown>
                              ) : (
                                <Dropdown
                                  menu={{
                                    items: [
                                      {
                                        key: 'favorite',
                                        label: record.isFavorited ? '取消标记' : '精确标记',
                                        icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={record.isFavorited ? 'text-yellow-400' : ''} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFavorite({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                      {
                                        key: 'incremental',
                                        label: record.incrementalRefreshEnabled ? '关闭定时' : '开启定时',
                                        icon: <MyIcon icon="clock" size={16} className={classNames({ 'text-red-400': record.incrementalRefreshEnabled })} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceIncremental({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                      {
                                        key: 'finished',
                                        label: record.isFinished ? '取消完结' : '标记完结',
                                        icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFinished({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(`操作失败: ${error.message}`)
                                          }
                                        },
                                      },
                                    ],
                                  }}
                                  trigger={['click']}
                                >
                                  <Button size="small" type="text" icon={<MenuOutlined />} onClick={(e) => e.stopPropagation()} />
                                </Dropdown>
                              )}
                              <Dropdown
                                menu={{
                                  items: [
                                    {
                                      key: 'incremental',
                                      icon: <MyIcon icon="image_134571035400041" size={16} />,
                                      label: '增量获取',
                                      onClick: () => handleIncrementalUpdate(record),
                                    },
                                    {
                                      key: 'fill_missing',
                                      icon: <MyIcon icon="a-image_0583743498849421" size={16} />,
                                      label: '补全缺失',
                                      onClick: () => handleFillMissing(record),
                                    },
                                    {
                                      key: 'full_update',
                                      icon: <MyIcon icon="image_488307257272375" size={16} />,
                                      label: '全量更新',
                                      onClick: () => handleFullSourceUpdate(record),
                                    },
                                  ],
                                }}
                                trigger={['click']}
                              >
                                {isMobile ? (
                                  <Button size="small" type="text" icon={<MyIcon icon="refresh" size={16} />} onClick={(e) => e.stopPropagation()} />
                                ) : (
                                  <Button size="small" type="text" icon={<MyIcon icon="refresh" size={16} />} onClick={(e) => e.stopPropagation()}>
                                    更新
                                  </Button>
                                )}
                              </Dropdown>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              }}
            />
          ) : (
            <Empty />
          )}
        </div>
      </Card>
      <Modal
        title={`为 "${animeDetail.title}"调整关联`}
        open={editOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setEditOpen(false)}
      >
        <div>
          此操作会将 "{animeDetail.title}" (ID: {animeDetail.animeId})
          下的所有数据源移动到您选择的另一个作品条目下，然后删除原条目。
        </div>
        <div className="flex items-center justify-between my-4">
          <div className="text-base font-bold">选择目标作品</div>
          <div>
            <Input
              placeholder="搜索目标作品"
              onChange={e => handleKeywordChange(e)}
            />
          </div>
        </div>
        <List
          itemLayout="vertical"
          size="large"
          dataSource={libraryList}
          pagination={{
            ...pagination,
            align: 'center',
            showLessItems: true,
            onChange: (page, pageSize) => {
              setPagination(n => {
                return {
                  ...n,
                  current: page,
                  pageSize,
                }
              })
            },
            onShowSizeChange: (_, size) => {
              setPagination(n => {
                return {
                  ...n,
                  pageSize: size,
                }
              })
            },
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-base font-bold mb-2">
                        {item.title}（ID: {item.animeId}）
                      </div>
                      <div>
                        <span>季:{item.season}</span>
                        <span className="ml-3">
                          类型:{DANDAN_TYPE_DESC_MAPPING[item.type]}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div>
                    <Button
                      disabled={item.animeId === animeDetail.animeId}
                      type="primary"
                      onClick={() => {
                        handleConfirmSource(item)
                      }}
                    >
                      关联
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <AddSourceModal
        open={isAddSourceModalOpen}
        animeId={id}
        onCancel={() => setIsAddSourceModalOpen(false)}
        onSuccess={handleAddSourceSuccess}
      />

      <ReassociationConflictModal
        open={conflictModalOpen}
        onCancel={() => setConflictModalOpen(false)}
        onConfirm={handleResolveConflict}
        conflictData={conflictData}
        targetAnimeTitle={targetAnimeTitle}
      />

      <SplitSourceModal
        open={isSplitSourceModalOpen}
        animeId={Number(id)}
        animeTitle={animeDetail.title}
        sources={sourceList}
        onCancel={() => setIsSplitSourceModalOpen(false)}
        onSuccess={() => {
          setIsSplitSourceModalOpen(false)
          getDetail() // 刷新数据
        }}
      />
    </div>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
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
  Modal,
  Row,
  Space,
  Switch,
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
import { useTranslation } from 'react-i18next'

export const AnimeDetail = () => {
  const { t } = useTranslation()
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
        messageApi.error(t('animePage.invalidAnimeId'))
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
      messageApi.error(t('animePage.fetchDetailFailed'))
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
      messageApi.error(t('animePage.fetchSourceFailed'))
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
          title: t('animePage.associateTitle'),
          zIndex: 1002,
          content: (
            <div>
              {t('animePage.associateConfirm', { title: item.title, id: item.animeId })}
              <br />
              {t('animePage.associateIrreversible')}
            </div>
          ),
          okText: t('common.confirm'),
          cancelText: t('common.cancel'),
          onOk: async () => {
            try {
              await setAnimeSource({
                sourceAnimeId: animeDetail.animeId,
                targetAnimeId: item.animeId,
              })
              messageApi.success(t('animePage.associateSuccess'))
              setEditOpen(false)
              navigate(RoutePaths.LIBRARY)
            } catch (error) {
              messageApi.error(t('animePage.associateFailed', { error: error.message }))
            }
          },
        })
      }
    } catch (error) {
      messageApi.error(t('animePage.conflictDetectFailed', { error: error.message }))
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
      messageApi.success(t('animePage.associateSuccess'))
      setConflictModalOpen(false)
      navigate(RoutePaths.LIBRARY)
    } catch (error) {
      messageApi.error(t('animePage.associateFailed', { error: error.message }))
    }
  }

  const handleBatchDelete = () => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: t('animePage.deleteSourceTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('animePage.deleteSelectedConfirm', { count: selectedRows.length })}
          <br />
          {t('animePage.deleteBatchHint')}
          <div className="flex items-center gap-2 mt-3">
            <span>{t('animePage.deleteAlsoFiles')}</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await deleteAnimeSource({
            sourceIds: selectedRows?.map(it => it.sourceId),
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('animePage.deleteBatchSubmitFailed', { error: error.message }))
        }
      },
    })
  }

  const handleDeleteSingle = record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: t('animePage.deleteSourceTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('animePage.deleteSingleConfirm')}
          <br />
          {t('animePage.deleteSingleHint')}
          <div className="flex items-center gap-2 mt-3">
            <span>{t('animePage.deleteAlsoFiles')}</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await deleteAnimeSourceSingle({
            sourceId: record.sourceId,
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('animePage.deleteSubmitFailed', { error: error.message }))
        }
      },
    })
  }

  const handleIncrementalUpdate = record => {
    modalApi.confirm({
      title: t('animePage.incrementalTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('animePage.incrementalConfirm', { title: animeDetail.title })}
          <br />
          {t('animePage.incrementalHint')}
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await incrementalUpdate({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('animePage.incrementalFailed', { error: error.message }))
        }
      },
    })
  }

  const handleFullSourceUpdate = record => {
    modalApi.confirm({
      title: t('animePage.fullRefreshTitle'),
      zIndex: 1002,
      content: (
        <div>{t('animePage.fullRefreshConfirm', { title: animeDetail.title })}</div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await fullSourceUpdate({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('animePage.refreshFailed', { error: error.message }))
        }
      },
    })
  }

  const handleFillMissing = record => {
    modalApi.confirm({
      title: t('animePage.completeMissingTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('animePage.completeMissingDesc')}
          <br />
          {t('animePage.completeMissingConfirm', { title: animeDetail.title })}
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await fillMissingEpisodes({
            sourceId: record.sourceId,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('animePage.completeMissingFailed', { error: error.message }))
        }
      },
    })
  }

  const goTask = res => {
    modalApi.confirm({
      title: t('animePage.taskTipTitle'),
      zIndex: 1002,
      content: (
        <div>
          {res.data?.message || t('animePage.taskSubmitted')}
          <br />
          {t('animePage.goTaskManager')}
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
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
      title: t('animePage.colProvider'),
      dataIndex: 'providerName',
      key: 'providerName',
      width: 100,
    },
    {
      title: t('animePage.colMediaId'),
      dataIndex: 'mediaId',
      key: 'mediaId',
      width: 200,
    },
    {
      title: t('animePage.colStatus'),
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
      title: t('animePage.colCollectedAt'),
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
      title: t('animePage.colAction'),
      width: operateWidth,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title={t('animePage.tipBatchEditEpisodes')}>
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
                      label: record.isFavorited ? t('animePage.menuUnFav') : t('animePage.menuFav'),
                      icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={classNames({ 'text-yellow-400': record.isFavorited })} />,
                      onClick: async () => {
                        try {
                          await toggleSourceFavorite({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                          ))
                        } catch (error) {
                          alert(t('animePage.operationFailed', { error: error.message }))
                        }
                      },
                    },
                    {
                      key: 'incremental',
                      label: record.incrementalRefreshEnabled ? t('animePage.menuTimerOff') : t('animePage.menuTimerOn'),
                      icon: <MyIcon icon="clock" size={16} className={classNames({ 'text-red-400': record.incrementalRefreshEnabled })} />,
                      onClick: async () => {
                        try {
                          await toggleSourceIncremental({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                          ))
                        } catch (error) {
                          alert(t('animePage.operationFailed', { error: error.message }))
                        }
                      },
                    },
                    {
                      key: 'finished',
                      label: record.isFinished ? t('animePage.menuUnFin') : t('animePage.menuMarkFin'),
                      icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                      onClick: async () => {
                        try {
                          await toggleSourceFinished({ sourceId: record.sourceId })
                          setSourceList(list => list.map(it =>
                            it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                          ))
                        } catch (error) {
                          alert(t('animePage.operationFailed', { error: error.message }))
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
            <Tooltip title={t('animePage.tipEpisodeList')}>
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
                      label: t('animePage.menuIncremental'),
                      onClick: () => handleIncrementalUpdate(record),
                    },
                    {
                      key: 'fill_missing',
                      icon: <MyIcon icon="a-image_0583743498849421" size={16} />,
                      label: t('animePage.menuCompleteMissing'),
                      onClick: () => handleFillMissing(record),
                    },
                    {
                      key: 'full_update',
                      icon: <MyIcon icon="image_488307257272375" size={16} />,
                      label: t('animePage.menuFullUpdate'),
                      onClick: () => handleFullSourceUpdate(record),
                    },
                  ],
                }}
                trigger={['click']}
              >
                <Tooltip title={t('animePage.tipUpdateAction')}>
                  <span className="cursor-pointer hover:text-primary">
                    <MyIcon icon="refresh" size={20} />
                  </span>
                </Tooltip>
              </Dropdown>
            )}

            <Tooltip title={t('animePage.tipDeleteSource')}>
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
            title: <Link to="/library">{t('animePage.breadcrumbLibrary')}</Link>,
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
                  <span>{t('animePage.totalEpisodes', { count: totalEpisodeCount })}</span>|
                  <span>{t('animePage.associatedSources', { count: sourceList.length })}</span>
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
                {t('animePage.btnAdjustAssociation')}
              </Button>
              <Button
                block
                onClick={() => {
                  setIsSplitSourceModalOpen(true)
                }}
                disabled={!sourceList?.length}
              >
                {t('animePage.btnSplitSource')}
              </Button>
            </div>
          </Col>
        </Row>
        <div className="mt-6">
          <div className="mb-3 text-sm text-gray-600 dark:text-gray-400">
            💡 {t('animePage.selectTip')}
          </div>
          <div className="flex items-center gap-4 mb-4">
            <Button
              onClick={() => {
                handleBatchDelete()
              }}
              type="primary"
              disabled={!selectedRows.length}
            >
              {t('animePage.btnDeleteSelected')}
            </Button>
            <Button
              onClick={() => {
                setIsAddSourceModalOpen(true)
              }}
            >
              {t('animePage.btnAddSource')}
            </Button>
          </div>
          {sourceList?.length ? (
            <ResponsiveTable
              pagination={false}
              size="small"
              dataSource={sourceList}
              columns={columns}
              rowKey={'sourceId'}
              scroll={{ x: '100%' }}
              tableProps={{ className: 'library-table' }}

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
                              title={t('animePage.tipDeleteSource')}
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDeleteSingle(record)
                              }}
                            />
                          </div>
                          <div className="font-semibold text-base mb-2 break-words">
                            {t('animePage.mediaIdCard', { id: record.mediaId })}
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
                            <Tooltip title={t('animePage.tipEpisodeList')}>
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
                              {t('animePage.btnEpisodeList')}
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
                                        label: record.isFavorited ? t('animePage.menuUnFav') : t('animePage.menuFav'),
                                        icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={record.isFavorited ? 'text-yellow-400' : ''} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFavorite({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
                                          }
                                        },
                                      },
                                      {
                                        key: 'incremental',
                                        label: record.incrementalRefreshEnabled ? t('animePage.menuTimerOff') : t('animePage.menuTimerOn'),
                                        icon: <MyIcon icon="clock" size={16} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceIncremental({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
                                          }
                                        },
                                      },
                                      {
                                        key: 'finished',
                                        label: record.isFinished ? t('animePage.menuUnFin') : t('animePage.menuMarkFin'),
                                        icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFinished({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
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
                                        label: record.isFavorited ? t('animePage.menuUnFav') : t('animePage.menuFav'),
                                        icon: <MyIcon icon={record.isFavorited ? 'favorites-fill' : 'favorites'} size={16} className={record.isFavorited ? 'text-yellow-400' : ''} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFavorite({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFavorited: !it.isFavorited } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
                                          }
                                        },
                                      },
                                      {
                                        key: 'incremental',
                                        label: record.incrementalRefreshEnabled ? t('animePage.menuTimerOff') : t('animePage.menuTimerOn'),
                                        icon: <MyIcon icon="clock" size={16} className={classNames({ 'text-red-400': record.incrementalRefreshEnabled })} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceIncremental({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, incrementalRefreshEnabled: !it.incrementalRefreshEnabled } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
                                          }
                                        },
                                      },
                                      {
                                        key: 'finished',
                                        label: record.isFinished ? t('animePage.menuUnFin') : t('animePage.menuMarkFin'),
                                        icon: <MyIcon icon={record.isFinished ? 'wanjie1' : 'wanjie'} size={16} className={record.isFinished ? 'text-blue-500' : 'text-gray-400'} />,
                                        onClick: async () => {
                                          try {
                                            await toggleSourceFinished({ sourceId: record.sourceId })
                                            setSourceList(list => list.map(it =>
                                              it.sourceId === record.sourceId ? { ...it, isFinished: !it.isFinished } : it
                                            ))
                                          } catch (error) {
                                            messageApi.error(t('animePage.operationFailed', { error: error.message }))
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
                                      label: t('animePage.menuIncremental'),
                                      onClick: () => handleIncrementalUpdate(record),
                                    },
                                    {
                                      key: 'fill_missing',
                                      icon: <MyIcon icon="a-image_0583743498849421" size={16} />,
                                      label: t('animePage.menuCompleteMissing'),
                                      onClick: () => handleFillMissing(record),
                                    },
                                    {
                                      key: 'full_update',
                                      icon: <MyIcon icon="image_488307257272375" size={16} />,
                                      label: t('animePage.menuFullUpdate'),
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
                                    {t('animePage.btnUpdate')}
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
        title={t('animePage.adjustTitle', { title: animeDetail.title })}
        open={editOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setEditOpen(false)}
      >
        <div>
          {t('animePage.adjustDesc', { title: animeDetail.title, id: animeDetail.animeId })}
        </div>
        <div className="flex items-center justify-between my-4">
          <div className="text-base font-bold">{t('animePage.selectTargetTitle')}</div>
          <div>
            <Input
              placeholder={t('animePage.placeholderSearchTarget')}
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
                        <span>{t('animePage.seasonLabel', { season: item.season })}</span>
                        <span className="ml-3">
                          {t('animePage.typeLabel', { type: DANDAN_TYPE_DESC_MAPPING[item.type] })}
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
                      {t('animePage.btnAssociate')}
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

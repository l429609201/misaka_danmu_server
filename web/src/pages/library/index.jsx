import React, { useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Pagination,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tooltip,
  Tag,
  Collapse,
  Typography,
  Dropdown,
  Image,
} from 'antd'
import { QuestionCircleOutlined, MenuOutlined, FolderOpenOutlined, SearchOutlined, LinkOutlined, EyeOutlined, AppstoreOutlined, UnorderedListOutlined } from '@ant-design/icons'
import {
  createAnimeEntry,
  deleteAnime,
  fetchLocalEpisodeGroupUrl,
  applyLocalEpisodeGroup,
  getAllEpisode,
  getAnimeDetail,
  getAnimeInfoAsSource,
  getAnimeLibrary,
  getBgmSearch,
  getDoubanSearch,
  getEgidSearch,
  getEpisodeGroupDetail,
  getImdbSearch,
  getTmdbSearch,
  getTvdbSearch,
  refreshPoster,
  setAnimeDetail,
  toggleSourceIncremental,
  toggleSourceFinished,
  batchSetFavorite,
  batchUnsetFavorite,
  downloadPosterToLocal,
  getConfig,
  setConfig,
  getAnimeGroups,
  createAnimeGroup,
  renameAnimeGroup,
  deleteAnimeGroup,
  setAnimeGroupMembership,
} from '../../apis'
import LibraryGroupView from './LibraryGroupView'
import { MyIcon } from '@/components/MyIcon'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../configs'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { CreateAnimeModal } from '../../components/CreateAnimeModal'
import { ScanDuplicatesModal } from '../../components/ScanDuplicatesModal'
import { RoutePaths } from '../../general/RoutePaths'
import { useModal } from '../../ModalContext'
import { useMessage } from '../../MessageContext'
import DirectoryBrowser from '../media-fetch/components/DirectoryBrowser'
import PosterSearchModal from '../media-fetch/components/PosterSearchModal'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { useDefaultPageSize } from '../../hooks/useDefaultPageSize'

import { useTranslation } from 'react-i18next'

const ApplyField = ({ name, label, fetchedValue, form }) => {
  const { t } = useTranslation()
  const currentValue = Form.useWatch(name, form)

  return (
    <Form.Item label={label}>
      <div className="flex items-center gap-2">
        <Form.Item name={name} noStyle>
          <Input />
        </Form.Item>
        {fetchedValue && currentValue !== fetchedValue && (
          <Button
            size="small"
            onClick={() => form.setFieldsValue({ [name]: fetchedValue })}
          >
            {t('libraryPage.apply')}
          </Button>
        )}
      </div>
    </Form.Item>
  )
}

export const Library = () => {
  const { t } = useTranslation()
  // 从后端配置获取默认分页大小
  const defaultPageSize = useDefaultPageSize('library')

  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  // 从 sessionStorage 恢复上次的搜索关键词
  const [keyword, setKeyword] = useState(() => sessionStorage.getItem('lib_keyword') || '')
  // null 表示未从 DB 初始化，防止初始化前触发 getList
  const [sortBy, setSortBy] = useState(null)
  const [sortOrder, setSortOrder] = useState(null)
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const [pagination, setPagination] = useState({
    // 从 sessionStorage 恢复上次的页码
    current: Number(sessionStorage.getItem('lib_page') || '1'),
    pageSize: defaultPageSize,
    total: 0,
  })

  // 视图模式：'list' | 'card'
  const [viewMode, setViewMode] = useState(() => {
    const saved = localStorage.getItem('libraryViewMode')
    // 兼容旧值 'group'，降级为 'list'
    return saved === 'card' ? 'card' : 'list'
  })

  // 分组数据
  const [groups, setGroups] = useState([])

  const switchViewMode = (mode) => {
    setViewMode(mode)
    localStorage.setItem('libraryViewMode', mode)
  }

  // 加载所有分组
  const loadGroups = async () => {
    try {
      const res = await getAnimeGroups()
      setGroups(res.data || [])
    } catch (e) {
      // 静默失败
    }
  }

  // 分组操作
  const handleCreateGroup = async (name, animeIds) => {
    try {
      const res = await createAnimeGroup({ name })
      const newGroupId = res.data?.id
      if (newGroupId && animeIds?.length > 0) {
        await Promise.all(animeIds.map(id => setAnimeGroupMembership(id, { groupId: newGroupId })))
      }
      await loadGroups()
      getList()
      messageApi.success(t('libraryPage.groupCreated', { name }))
    } catch (e) {
      messageApi.error(t('libraryPage.groupCreateFailed', { error: e?.message || t('common.unknownError') }))
    }
  }

  const handleRenameGroup = async (groupId, name) => {
    try {
      await renameAnimeGroup(groupId, { name })
      await loadGroups()
      messageApi.success(t('libraryPage.groupRenamed'))
    } catch (e) {
      messageApi.error(t('libraryPage.groupRenameFailed', { error: e?.message || t('common.unknownError') }))
    }
  }

  const handleDeleteGroup = (group) => {
    modalApi.confirm({
      title: t('libraryPage.groupDissolveTitle', { name: group.name }),
      content: t('libraryPage.groupDissolveContent'),
      okText: t('libraryPage.groupDissolveOk'),
      okType: 'danger',
      onOk: async () => {
        try {
          await deleteAnimeGroup(group.id)
          await loadGroups()
          getList()
          messageApi.success(t('libraryPage.groupDissolved'))
        } catch (e) {
          messageApi.error(t('libraryPage.groupDissolveFailed', { error: e?.message || t('common.unknownError') }))
        }
      },
    })
  }

  // 静默删除分组（不弹确认框，用于"拆分最后一个条目"自动清除空分组）
  const handleDeleteGroupSilent = async (group) => {
    try {
      await deleteAnimeGroup(group.id)
      await loadGroups()
    } catch (e) {
      messageApi.error(t('libraryPage.groupDissolveFailed', { error: e?.message || t('common.unknownError') }))
    }
  }

  const handleSetGroup = async (animeId, groupId) => {
    try {
      await setAnimeGroupMembership(animeId, { groupId: groupId ?? null })
      getList()
    } catch (e) {
      messageApi.error(t('libraryPage.groupSetFailed', { error: e?.message || t('common.unknownError') }))
    }
  }

  // 当默认分页大小加载完成后，更新 pagination
  useEffect(() => {
    if (defaultPageSize) {
      setPagination(prev => ({
        ...prev,
        pageSize: defaultPageSize
      }))
    }
  }, [defaultPageSize])
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isScanDuplicatesOpen, setIsScanDuplicatesOpen] = useState(false)

  const [form] = Form.useForm()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const title = Form.useWatch('title', form)
  const tmdbId = Form.useWatch('tmdbId', form)
  const tvdbId = Form.useWatch('tvdbId', form)
  const doubanId = Form.useWatch('doubanId', form)
  const bangumiId = Form.useWatch('bangumiId', form)
  const imdbId = Form.useWatch('imdbId', form)
  const type = Form.useWatch('type', form)
  const animeId = Form.useWatch('animeId', form)
  const imageUrl = Form.useWatch('imageUrl', form)
  const egidValue = Form.useWatch('tmdbEpisodeGroupId', form)
  const [fetchedMetadata, setFetchedMetadata] = useState(null)

  // 海报搜索与本地海报状态
  const [posterSearchVisible, setPosterSearchVisible] = useState(false)
  const [localImagePath, setLocalImagePath] = useState(null)
  const [downloadingLocal, setDownloadingLocal] = useState(false)
  const [previewVisible, setPreviewVisible] = useState(false)

  const modalApi = useModal()
  const messageApi = useMessage()
  const deleteFilesRef = useRef(true) // 删除时是否同时删除弹幕文件，默认为 true

  // 源选择弹窗状态（用于标记和追更操作）
  const [sourceSelectOpen, setSourceSelectOpen] = useState(false)
  const [sourceSelectAction, setSourceSelectAction] = useState(null) // 'favorite' | 'incremental'
  const [sourceSelectSources, setSourceSelectSources] = useState([])
  const [sourceSelectTitle, setSourceSelectTitle] = useState('')
  const [selectedSourceId, setSelectedSourceId] = useState(null)

  const getList = async () => {
    try {
      setLoading(true)
      const res = await getAnimeLibrary({
        keyword: keyword,
        page: pagination.current,
        pageSize: pagination.pageSize,
        sortBy,
        sortOrder,
      })
      setList(res.data?.list || [])
      setPagination(prev => ({
        ...prev,
        total: res.data?.total || 0,
      }))
    } catch (error) {
      setList([])
    } finally {
      setLoading(false)
    }
  }

  const handleCreateSuccess = () => {
    setIsCreateModalOpen(false)
    setPagination(n => {
      return {
        ...n,
        current: 1,
      }
    })
  }

  useEffect(() => {
    setPagination(n => {
      return {
        ...n,
        current: 1,
      }
    })
  }, [keyword])

  useEffect(() => {
    // sortBy/sortOrder 未从 DB 初始化完成时不触发
    if (sortBy === null || sortOrder === null) return
    getList()
  }, [keyword, pagination.current, pagination.pageSize, sortBy, sortOrder])

  // mount 时从 DB 读取上次保存的排序配置，读完后才触发 getList
  // sessionStorage 中若有缓存的排序状态，优先使用（返回弹幕库时恢复）
  useEffect(() => {
    Promise.all([
      getConfig('librarySortBy'),
      getConfig('librarySortOrder'),
    ]).then(([byRes, orderRes]) => {
      const cachedSortBy = sessionStorage.getItem('lib_sortBy')
      const cachedSortOrder = sessionStorage.getItem('lib_sortOrder')
      setSortBy(cachedSortBy || byRes.data?.value || 'anime_created')
      setSortOrder(cachedSortOrder || orderRes.data?.value || 'desc')
    }).catch(() => {
      const cachedSortBy = sessionStorage.getItem('lib_sortBy')
      const cachedSortOrder = sessionStorage.getItem('lib_sortOrder')
      // 读取失败则使用缓存值或默认值
      setSortBy(cachedSortBy || 'anime_created')
      setSortOrder(cachedSortOrder || 'desc')
    })
    // 初始化加载分组数据
    loadGroups()
  }, [])

  useEffect(() => {
    setSearchInputValue(keyword)
    // 同步搜索关键词到 sessionStorage，下次返回弹幕库时可恢复
    sessionStorage.setItem('lib_keyword', keyword)
  }, [keyword])

  // 同步分页状态到 sessionStorage
  useEffect(() => {
    sessionStorage.setItem('lib_page', String(pagination.current))
    sessionStorage.setItem('lib_pageSize', String(pagination.pageSize))
  }, [pagination.current, pagination.pageSize])

  // 同步排序状态到 sessionStorage
  useEffect(() => {
    if (sortBy) sessionStorage.setItem('lib_sortBy', sortBy)
    if (sortOrder) sessionStorage.setItem('lib_sortOrder', sortOrder)
  }, [sortBy, sortOrder])

  useEffect(() => {
    if (!fetchedMetadata) return

    const currentValues = form.getFieldsValue()
    const newValues = {}

    const fieldsToUpdate = {
      nameEn: fetchedMetadata.nameEn,
      nameJp: containsJapanese(fetchedMetadata?.nameJp)
        ? fetchedMetadata.nameJp
        : null,
      nameRomaji: fetchedMetadata.nameRomaji ?? null,
      aliasCn1: fetchedMetadata.aliasesCn?.[0] ?? null,
      aliasCn2: fetchedMetadata.aliasesCn?.[1] ?? null,
      aliasCn3: fetchedMetadata.aliasesCn?.[2] ?? null,
      tvdbId: fetchedMetadata.tvdbId,
      imdbId: fetchedMetadata.imdbId,
      doubanId: fetchedMetadata.doubanId,
      bangumiId: fetchedMetadata.bangumiId,
    }

    for (const [key, value] of Object.entries(fieldsToUpdate)) {
      if (!currentValues[key] && value) {
        newValues[key] = value
      }
    }

    if (Object.keys(newValues).length > 0) {
      form.setFieldsValue(newValues)
    }
    // 没有封面时填充url
    if (!imageUrl && !!fetchedMetadata?.imageUrl) {
      form.setFieldsValue({
        imageUrl: fetchedMetadata.imageUrl,
      })
    }
  }, [fetchedMetadata, form])


  // antd Table columns 定义（传给 LibraryGroupView 用于拖拽表格）
  const columns = [
    {
      title: t('libraryPage.colPoster'),
      dataIndex: 'imageUrl',
      key: 'imageUrl',
      width: 96,
      render: (_, record) => {
        let imageSrc = record.localImagePath || record.imageUrl
        if (imageSrc?.startsWith('/images/')) imageSrc = imageSrc.replace('/images/', '/data/images/')
        const hasFav = record.sources?.some(s => s.isFavorited)
        const hasInc = record.sources?.some(s => s.incrementalRefreshEnabled)
        const allFin = record.sources?.length > 0 && record.sources.every(s => s.isFinished)
        return (
          <div className="inline-flex flex-col items-center gap-0.5" style={{ width: 56 }}>
            {imageSrc ? (
              <img src={imageSrc} style={{ width: 56, height: 80, objectFit: 'cover', borderRadius: 4, cursor: 'pointer', display: 'block' }}
                onClick={() => navigate(`/anime/${record.animeId}`)} alt={record.title} />
            ) : (
              <div style={{ width: 56, height: 80, borderRadius: 4, background: '#f0f0f0', cursor: 'pointer' }}
                onClick={() => navigate(`/anime/${record.animeId}`)} />
            )}
            {/* 海报下方状态图标 */}
            {(hasFav || hasInc || allFin) && (
              <div className="flex items-center justify-center gap-0.5">
                {allFin && <MyIcon icon="wanjie1" size={13} color="#60a5fa" />}
                {hasInc && <MyIcon icon="refresh" size={13} color="#4ade80" />}
                {hasFav && <MyIcon icon="favorites-fill" size={13} color="#facc15" />}
              </div>
            )}
          </div>
        )
      },
    },
    {
      title: t('libraryPage.colName'),
      dataIndex: 'title',
      key: 'title',
    },
    {
      title: t('libraryPage.colType'),
      dataIndex: 'type',
      key: 'type',
      width: 90,
      render: (_, record) => <span>{DANDAN_TYPE_DESC_MAPPING[record.type]}</span>,
    },
    { title: t('libraryPage.colSeason'), dataIndex: 'season', key: 'season', width: 50 },
    { title: t('libraryPage.colYear'), dataIndex: 'year', key: 'year', width: 70 },
    { title: t('libraryPage.colEpisodeCount'), dataIndex: 'episodeCount', key: 'episodeCount', width: 60 },
    { title: t('libraryPage.colSourceCount'), dataIndex: 'sourceCount', key: 'sourceCount', width: 70 },
    {
      title: t('libraryPage.colCollectedAt'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 150,
      render: (_, record) => <span>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm')}</span>,
    },
    {
      title: t('libraryPage.colAction'),
      width: 140,
      fixed: 'right',
      render: (_, record) => {
        const hasFavorited = record.sources?.some(s => s.isFavorited)
        const hasIncremental = record.sources?.some(s => s.incrementalRefreshEnabled)
        const allFinished = record.sources?.length > 0 && record.sources.every(s => s.isFinished)
        return (
          <Space>
            <Tooltip title={t('libraryPage.tipEdit')}>
              <span className="cursor-pointer hover:text-primary"
                onClick={async (e) => {
                  e.stopPropagation()
                  const res = await getAnimeDetail({ animeId: record.animeId })
                  form.resetFields()
                  form.setFieldsValue({ ...(res.data || {}), animeId: record.animeId })
                  setLocalImagePath(res.data?.localImagePath || null)
                  setEditOpen(true)
                }}>
                <MyIcon icon="edit" size={20} />
              </span>
            </Tooltip>
            <Dropdown
              menu={{
                items: [
                  { key: 'fav', label: hasFavorited ? t('libraryPage.menuUnFav') : t('libraryPage.menuFav'), icon: <MyIcon icon={hasFavorited ? 'favorites-fill' : 'favorites'} size={16} className={hasFavorited ? 'text-yellow-400' : ''} />, onClick: (e) => { e.domEvent?.stopPropagation?.(); handleFavorite(record) } },
                  { key: 'inc', label: hasIncremental ? t('libraryPage.menuUnInc') : t('libraryPage.menuInc'), icon: <MyIcon icon={hasIncremental ? 'refresh' : 'clock'} size={16} className={hasIncremental ? 'text-green-500' : ''} />, onClick: (e) => { e.domEvent?.stopPropagation?.(); handleIncremental(record) } },
                  { key: 'fin', label: allFinished ? t('libraryPage.menuUnFin') : t('libraryPage.menuFin'), icon: <MyIcon icon={allFinished ? 'wanjie1' : 'wanjie'} size={16} className={allFinished ? 'text-blue-500' : 'text-gray-400'} />, onClick: (e) => { e.domEvent?.stopPropagation?.(); handleFinished(record) } },
                ],
              }}
              trigger={['click']}
            >
              <span className="cursor-pointer hover:text-primary" onClick={e => e.stopPropagation()}>
                <MenuOutlined style={{ fontSize: 18 }} />
              </span>
            </Dropdown>
            <Tooltip title={t('libraryPage.tipDetail')}>
              <span className="cursor-pointer hover:text-primary"
                onClick={(e) => { e.stopPropagation(); if (record.animeId) navigate(`/anime/${record.animeId}`) }}>
                <MyIcon icon="book" size={20} />
              </span>
            </Tooltip>
            <Tooltip title={t('libraryPage.tipDelete')}>
              <span className="cursor-pointer hover:text-primary"
                onClick={(e) => { e.stopPropagation(); handleDelete(record) }}>
                <MyIcon icon="delete" size={20} />
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  // 处理标记操作
  const handleFavorite = async (record) => {
    const sources = record.sources || []
    if (sources.length === 0) {
      messageApi.warning(t('libraryPage.noSource'))
      return
    }
    const hasFav = sources.some(s => s.isFavorited)
    if (hasFav) {
      // 当前有标记 → 取消该作品所有源的标记
      try {
        await batchUnsetFavorite({ sourceIds: sources.map(s => s.sourceId) })
        messageApi.success(t('libraryPage.favCancelled'))
        getList()
      } catch (error) {
        messageApi.error(t('libraryPage.operationFailed'))
      }
    } else {
      // 当前无标记 → 需要选择一个源来标记
      if (sources.length === 1) {
        // 只有一个源，直接设为标记
        try {
          await batchSetFavorite({ sourceIds: [sources[0].sourceId] })
          messageApi.success(t('libraryPage.favUpdated'))
          getList()
        } catch (error) {
          messageApi.error(t('libraryPage.operationFailed'))
        }
      } else {
        // 多个源，弹窗选择
        setSourceSelectAction('favorite')
        setSourceSelectSources(sources)
        setSourceSelectTitle(record.title)
        setSelectedSourceId(sources[0].sourceId)
        setSourceSelectOpen(true)
      }
    }
  }

  // 处理追更操作
  const handleIncremental = async (record) => {
    const sources = record.sources || []
    if (sources.length === 0) {
      messageApi.warning(t('libraryPage.noSource'))
      return
    }
    if (sources.length === 1) {
      // 只有一个源，直接切换
      try {
        await toggleSourceIncremental({ sourceId: sources[0].sourceId })
        messageApi.success(t('libraryPage.incUpdated'))
        getList()
      } catch (error) {
        messageApi.error(t('libraryPage.operationFailed'))
      }
    } else {
      // 多个源，弹窗选择
      setSourceSelectAction('incremental')
      setSourceSelectSources(sources)
      setSourceSelectTitle(record.title)
      setSelectedSourceId(sources.find(s => s.incrementalRefreshEnabled)?.sourceId || sources[0].sourceId)
      setSourceSelectOpen(true)
    }
  }

  // 处理完结操作
  const handleFinished = async (record) => {
    const sources = record.sources || []
    if (sources.length === 0) {
      messageApi.warning(t('libraryPage.noSource'))
      return
    }
    if (sources.length === 1) {
      try {
        await toggleSourceFinished({ sourceId: sources[0].sourceId })
        messageApi.success(t('libraryPage.finUpdated'))
        getList()
      } catch (error) {
        messageApi.error(t('libraryPage.operationFailed'))
      }
    } else {
      setSourceSelectAction('finished')
      setSourceSelectSources(sources)
      setSourceSelectTitle(record.title)
      setSelectedSourceId(sources.find(s => s.isFinished)?.sourceId || sources[0].sourceId)
      setSourceSelectOpen(true)
    }
  }

  // 确认源选择
  const handleSourceSelectConfirm = async () => {
    if (!selectedSourceId) {
      messageApi.warning(t('libraryPage.selectSourceFirst'))
      return
    }
    try {
      if (sourceSelectAction === 'favorite') {
        // 使用 batchSetFavorite 直接设为标记（而非 toggle，避免误取消）
        await batchSetFavorite({ sourceIds: [selectedSourceId] })
        messageApi.success(t('libraryPage.favUpdated'))
      } else if (sourceSelectAction === 'incremental') {
        await toggleSourceIncremental({ sourceId: selectedSourceId })
        messageApi.success(t('libraryPage.incUpdated'))
      } else if (sourceSelectAction === 'finished') {
        await toggleSourceFinished({ sourceId: selectedSourceId })
        messageApi.success(t('libraryPage.finUpdated'))
      }
      setSourceSelectOpen(false)
      getList()
    } catch (error) {
      messageApi.error(t('libraryPage.operationFailed'))
    }
  }

  const handleDelete = async record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: t('libraryPage.deleteTitle'),
      zIndex: 1002,
      content: (
        <div>
          {t('libraryPage.deleteConfirmMsg', { name: record.name })}
          <br />
          {t('libraryPage.deleteHintBg')}
          <div className="flex items-center gap-2 mt-3">
            <span>{t('libraryPage.deleteAlsoFiles')}</span>
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
          const res = await deleteAnime({ animeId: record.animeId, deleteFiles: deleteFilesRef.current })
          goTask(res)
        } catch (error) {
          messageApi.error(t('libraryPage.deleteSubmitFailed'))
        }
      },
    })
  }

  const goTask = res => {
    modalApi.confirm({
      title: t('libraryPage.deleteResultTitle'),
      zIndex: 1002,
      content: (
        <div>
          {res.message || t('libraryPage.deleteResultMsg')}
          <br />
          {t('libraryPage.deleteGoTask')}
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => {
        navigate(`${RoutePaths.TASK}?status=all`)
      },
      onCancel: () => {
        getList()
      },
    })
  }

  const handleSave = async () => {
    try {
      if (confirmLoading) return
      setConfirmLoading(true)
      const values = await form.validateFields()
      await setAnimeDetail({
        ...values,
        year: values.year ? Number(values.year) : null,
        tmdbId: values.tmdbId ? `${values.tmdbId}` : null,
        tvdbId: values.tvdbId ? `${values.tvdbId}` : null,
      })
      getList()
      messageApi.success(t('libraryPage.editSuccess'))
    } catch (error) {
      messageApi.error(error.detail || t('libraryPage.editFailed'))
    } finally {
      setConfirmLoading(false)
      setEditOpen(false)
    }
  }

  const containsJapanese = str => {
    if (!str) return false
    // 此正则表达式匹配日文假名和常见的CJK统一表意文字
    return /[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]/.test(str)
  }

  /** 搜索相关 */
  /** 精准搜索loading */
  const [searchAsIdLoading, setSearchAsIdLoading] = useState(false)

  const handleSearchAsId = async ({ source, currentId, mediaType }) => {
    try {
      if (searchAsIdLoading || !currentId) return
      setSearchAsIdLoading(true)
      const res = await getAnimeInfoAsSource({ source, currentId, mediaType })
      applySearchSelectionData({
        data: res.data,
        source,
      })
      messageApi.success(
        t('libraryPage.sourceInfoSuccess', { source: source.toUpperCase() })
      )
    } catch (error) {
      messageApi.error(
        t('libraryPage.sourceInfoFailed', { source: source.toUpperCase(), error: error.message })
      )
    } finally {
      setSearchAsIdLoading(false)
    }
  }

  const applySearchSelectionData = ({ data, source }) => {
    if (!data) return
    switch (source) {
      case 'bangumi':
        form.setFieldsValue({
          nameEn: data.nameEn,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameRomaji: data.nameRomaji,
          ...getAliasCn(data.aliasesCn, data.name),
        })
        break
      case 'tmdb':
        form.setFieldsValue({
          imdbId: data.imdbId,
          tvdbId: data.tvdbId,
          nameEn: data.nameEn,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameRomaji: data.nameRomaji,
          ...getAliasCn(data.aliasesCn, data.mainTitleFromSearch),
        })
        break
      case 'imdb':
        form.setFieldsValue({
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          ...getAliasCn(data.aliasesCn, data.nameEn),
        })
        break
      case 'tvdb':
        form.setFieldsValue({
          imdbId: data.imdbId,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameEn: data.nameEn,
          ...getAliasCn(data.aliasesCn, data.nameEn),
        })
        break
      case 'douban':
        form.setFieldsValue({
          imdbId: data.imdbId,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameEn: data.nameEn,
          ...getAliasCn(
            data.aliasesCn,
            // 修正：统一使用驼峰命名的 aliasesCn，并提供更好的备用标题
            data.aliasesCn && data.aliasesCn.length > 0
              ? data.aliasesCn[0]
              : data.name || ''
          ),
        })
        break
    }
  }

  const getAliasCn = (aliasesCn, name) => {
    const filteredAliases = (aliasesCn || []).filter(
      alias => !!alias && alias !== name
    )
    return {
      aliasCn1: filteredAliases?.[0],
      aliasCn2: filteredAliases?.[1],
      aliasCn3: filteredAliases?.[2],
    }
  }

  const [tmdbResult, setTmdbResult] = useState([])
  const [tmdbOpen, setTmdbOpen] = useState(false)
  const [searchTmdbLoading, setSearchTmdbLoading] = useState(false)
  const onTmdbSearch = async () => {
    try {
      if (searchTmdbLoading) return
      setSearchTmdbLoading(true)
      const res = await getTmdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'tv' : 'movie',
      })
      if (!!res?.data?.length) {
        setTmdbResult(res?.data || [])
        setTmdbOpen(true)
      } else {
        messageApi.error(t('libraryPage.noResults'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.tmdbSearchFailed', { error: error.message }))
    } finally {
      setSearchTmdbLoading(false)
    }
  }

  const [tvdbResult, setTvdbResult] = useState([])
  const [tvdbOpen, setTvdbOpen] = useState(false)
  const [searchTvdbLoading, setSearchTvdbLoading] = useState(false)
  const onTvdbSearch = async () => {
    try {
      if (searchTvdbLoading) return
      setSearchTvdbLoading(true)
      const res = await getTvdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'series' : 'movie',
      })
      if (!!res?.data?.length) {
        setTvdbResult(res?.data || [])
        setTvdbOpen(true)
      } else {
        messageApi.error(t('libraryPage.noResults'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.tvdbSearchFailed', { error: error.message }))
    } finally {
      setSearchTvdbLoading(false)
    }
  }

  const [doubanResult, setDoubanResult] = useState([])
  const [doubanOpen, setDoubanOpen] = useState(false)
  const [searchDoubanLoading, setSearchDoubanLoading] = useState(false)
  const onDoubanSearch = async () => {
    try {
      if (searchDoubanLoading) return
      setSearchDoubanLoading(true)
      const res = await getDoubanSearch({
        keyword: title,
      })
      if (!!res?.data?.length) {
        setDoubanResult(res?.data || [])
        setDoubanOpen(true)
      } else {
        messageApi.error(t('libraryPage.noResults'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.doubanSearchFailed', { error: error.message }))
    } finally {
      setSearchDoubanLoading(false)
    }
  }

  const [imdbResult, setImdbResult] = useState([])
  const [imdbOpen, setImdbOpen] = useState(false)
  const [searchImdbLoading, setSearchImdbLoading] = useState(false)
  const onImdbSearch = async () => {
    try {
      if (searchImdbLoading) return
      setSearchImdbLoading(true)
      const res = await getImdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'series' : 'movie',
      })
      if (!!res?.data?.length) {
        setImdbResult(res?.data || [])
        setImdbOpen(true)
      } else {
        messageApi.error(t('libraryPage.noResults'))
      }
    } catch (error) {
      messageApi.error(
        error.detail || t('libraryPage.imdbSearchFailed', { error: error.message || t('common.unknownError') })
      )
    } finally {
      setSearchImdbLoading(false)
    }
  }

  const [egidResult, setEgidResult] = useState([])
  const [egidOpen, setEgidOpen] = useState(false)
  const [searchEgidLoading, setSearchEgidLoading] = useState(false)
  const [searchAllEpisodeLoading, setSearchAllEpisodeLoading] = useState(false)
  const [allEpisode, setAllEpisode] = useState({})
  const [episodeOpen, setEpisodeOpen] = useState(false)

  // ---- 本地剧集组 state ----
  const [localEgOpen, setLocalEgOpen] = useState(false)
  const [localEgParsedData, setLocalEgParsedData] = useState(null)
  const [localEgApplyLoading, setLocalEgApplyLoading] = useState(false)
  const [pasteJsonOpen, setPasteJsonOpen] = useState(false)
  const [pasteJsonValue, setPasteJsonValue] = useState('')
  const [localPathOpen, setLocalPathOpen] = useState(false)
  const [localPathValue, setLocalPathValue] = useState('')
  const [localPathLoading, setLocalPathLoading] = useState(false)
  const [fileBrowserOpen, setFileBrowserOpen] = useState(false)

  // 公共校验：解析并校验剧集组JSON数据
  const validateAndApplyEgJson = (jsonStr) => {
    try {
      const data = JSON.parse(jsonStr)
      if (!data.groups || !Array.isArray(data.groups) || data.groups.length === 0) {
        messageApi.error(t('libraryPage.egJsonInvalid'))
        return false
      }
      for (const g of data.groups) {
        if (!g.episodes || !Array.isArray(g.episodes)) {
          messageApi.error(t('libraryPage.egJsonGroupInvalid'))
          return false
        }
      }
      setLocalEgParsedData(data)
      setLocalEgOpen(true)
      return true
    } catch {
      messageApi.error(t('libraryPage.egJsonParseError'))
      return false
    }
  }

  // 查询服务端本地路径的JSON
  const handleLocalPathConfirm = async () => {
    const pathVal = localPathValue.trim()
    if (!pathVal) {
      messageApi.warning(t('libraryPage.egInputPathRequired'))
      return
    }
    try {
      setLocalPathLoading(true)
      const res = await fetchLocalEpisodeGroupUrl({ url: pathVal })
      if (res?.data?.groups) {
        setLocalEgParsedData(res.data)
        setLocalEgOpen(true)
        setLocalPathOpen(false)
        setLocalPathValue('')
      } else {
        messageApi.error(t('libraryPage.egJsonNoGroups'))
      }
    } catch (e) {
      messageApi.error(t('libraryPage.egFetchFailed', { error: e?.response?.data?.detail || e.message }))
    } finally {
      setLocalPathLoading(false)
    }
  }

  // 粘贴JSON确认
  const handlePasteJsonConfirm = () => {
    if (!pasteJsonValue.trim()) {
      messageApi.warning(t('libraryPage.egPasteRequired'))
      return
    }
    if (validateAndApplyEgJson(pasteJsonValue)) {
      setPasteJsonOpen(false)
      setPasteJsonValue('')
    }
  }

  // ---- 查看/编辑剧集组 ----
  const [editEgOpen, setEditEgOpen] = useState(false)
  const [editEgData, setEditEgData] = useState(null) // { id, name, description, groups: [...] }
  const [editEgLoading, setEditEgLoading] = useState(false)
  const [editEgSaving, setEditEgSaving] = useState(false)

  const handleOpenEditEg = async () => {
    const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()
    if (!egidValue) {
      messageApi.warning(t('libraryPage.egNoGroupId'))
      return
    }
    try {
      setEditEgLoading(true)
      // 从数据库读取已保存的剧集组映射
      const res = await getEpisodeGroupDetail(egidValue)
      if (res?.data?.id) {
        const raw = res.data
        const groups = (raw.groups || []).map((g, gi) => ({
          _key: gi,
          name: g.name || '',
          order: g.order ?? gi,
          episodes: (g.episodes || []).map((ep, ei) => ({
            _key: ei,
            seasonNumber: ep.seasonNumber ?? 0,
            episodeNumber: ep.episodeNumber ?? 0,
            order: ep.order ?? ei,
            name: ep.name || '',
          })),
        }))
        setEditEgData({ id: raw.id, name: raw.name || '', description: raw.description || '', groups })
        setEditEgOpen(true)
      } else {
        messageApi.error(t('libraryPage.egNoMapping'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.egDetailFailed', { error: error?.response?.data?.detail || error.message }))
    } finally {
      setEditEgLoading(false)
    }
  }

  // 编辑剧集组内部操作
  const updateEditEgField = (path, value) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      let target = next
      for (let i = 0; i < path.length - 1; i++) target = target[path[i]]
      target[path[path.length - 1]] = value
      return next
    })
  }

  const addEgGroup = () => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups.push({ _key: Date.now(), name: '', order: next.groups.length, episodes: [] })
      return next
    })
  }

  const removeEgGroup = (gi) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups.splice(gi, 1)
      return next
    })
  }

  const addEgEpisode = (gi) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      const eps = next.groups[gi].episodes
      eps.push({ _key: Date.now(), seasonNumber: 0, episodeNumber: 0, order: eps.length, name: '' })
      return next
    })
  }

  const removeEgEpisode = (gi, ei) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups[gi].episodes.splice(ei, 1)
      return next
    })
  }

  const handleSaveEditEg = async () => {
    if (!editEgData) return
    if (!tmdbId) { messageApi.warning(t('libraryPage.egTmdbIdRequired')); return }
    try {
      setEditEgSaving(true)
      // 转换为 applyLocalEpisodeGroup 需要的格式
      const localEpisodeGroup = {
        description: editEgData.name || t('libraryPage.egLocalName'),
        groups: editEgData.groups.map(g => ({
          name: g.name,
          order: g.order,
          episodes: g.episodes.map(ep => ({
            season_number: ep.seasonNumber,
            episode_number: ep.episodeNumber,
            order: ep.order,
          })),
        })),
      }
      const res = await applyLocalEpisodeGroup({
        tmdbId: Number(tmdbId),
        localEpisodeGroup,
      })
      if (res?.data?.groupId) {
        form.setFieldsValue({ tmdbEpisodeGroupId: res.data.groupId })
        messageApi.success(t('libraryPage.egSaved', { count: res.data.episodeCount }))
        setEditEgOpen(false)
        setEditEgData(null)
      }
    } catch (e) {
      messageApi.error(t('libraryPage.egSaveFailed', { error: e?.response?.data?.detail || e.message }))
    } finally {
      setEditEgSaving(false)
    }
  }

  const handleLocalEgApply = async () => {
    if (!localEgParsedData) { messageApi.warning(t('libraryPage.egParseFirst')); return }
    if (!tmdbId) { messageApi.warning(t('libraryPage.egTmdbIdRequired')); return }
    try {
      setLocalEgApplyLoading(true)
      const res = await applyLocalEpisodeGroup({
        tmdbId: Number(tmdbId),
        localEpisodeGroup: localEgParsedData,
      })
      if (res?.data?.groupId) {
        form.setFieldsValue({ tmdbEpisodeGroupId: res.data.groupId })
        messageApi.success(t('libraryPage.egApplied', { count: res.data.episodeCount }))
        setLocalEgOpen(false)
        setLocalEgParsedData(null)
      }
    } catch (e) {
      messageApi.error(t('libraryPage.egApplyFailed', { error: e?.response?.data?.detail || e.message }))
    } finally {
      setLocalEgApplyLoading(false)
    }
  }

  // 判断输入是否为 URL 或本地路径
  const isEgidInputPath = (val) => {
    if (!val) return false
    const v = val.trim()
    return v.startsWith('http://') || v.startsWith('https://') || v.endsWith('.json')
  }

  const onEgidSearch = async () => {
    try {
      if (searchEgidLoading) return
      setSearchEgidLoading(true)

      const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()

      if (isEgidInputPath(egidValue)) {
        // URL 或本地路径 → 获取JSON → 弹预览窗
        const res = await fetchLocalEpisodeGroupUrl({ url: egidValue })
        if (res?.data?.groups) {
          setLocalEgParsedData(res.data)
          setLocalEgOpen(true)
        } else {
          messageApi.error(t('libraryPage.egJsonFormatError'))
        }
      } else if (egidValue) {
        // 有内容但不是路径 → 当作剧集组ID直接查详情
        const res = await getAllEpisode({ tmdbId: tmdbId, egid: egidValue })
        if (res?.data?.id) {
          setAllEpisode(res.data)
          setEpisodeOpen(true)
        } else {
          messageApi.error(t('libraryPage.egNoEpisodes'))
        }
      } else {
        // 空 → 按TMDB ID搜索剧集组列表
        if (!tmdbId) {
          messageApi.warning(t('libraryPage.egTmdbOrPathRequired'))
          return
        }
        const res = await getEgidSearch({ tmdbId: tmdbId, keyword: title })
        if (res?.data?.length) {
          setEgidResult(res.data)
          setEgidOpen(true)
        } else {
          messageApi.error(t('libraryPage.noResults'))
        }
      }
    } catch (error) {
      messageApi.error(t('libraryPage.egSearchFailed', { error: error?.response?.data?.detail || error.message }))
    } finally {
      setSearchEgidLoading(false)
    }
  }

  const handleAllEpisode = async item => {
    try {
      if (searchAllEpisodeLoading) return
      setSearchAllEpisodeLoading(true)
      const res = await getAllEpisode({
        tmdbId: tmdbId,
        egid: item.id,
      })
      if (!!res?.data?.id) {
        setAllEpisode(res?.data || {})
        setEpisodeOpen(true)
      } else {
        messageApi.error(t('libraryPage.egNoEpisodesFound'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.egNoEpisodesFound'))
    } finally {
      setSearchAllEpisodeLoading(false)
    }
  }

  // 搜索框受控值（跟随 keyword 同步，初始值也从 sessionStorage 恢复）
  const [searchInputValue, setSearchInputValue] = useState(() => sessionStorage.getItem('lib_keyword') || '')

  const handleSearch = () => {
    setKeyword(searchInputValue)
  }

  const handleReset = () => {
    setKeyword('')
    setSearchInputValue('')
  }

  const [bgmResult, setBgmResult] = useState([])
  const [bgmOpen, setBgmOpen] = useState(false)
  const [searchBgmLoading, setSearchBgmLoading] = useState(false)
  const onBgmSearch = async () => {
    try {
      if (searchBgmLoading) return
      setSearchBgmLoading(true)
      const res = await getBgmSearch({
        keyword: title,
      })
      if (!!res?.data?.length) {
        setBgmResult(res?.data || [])
        setBgmOpen(true)
      } else {
        messageApi.error(t('libraryPage.noResults'))
      }
    } catch (error) {
      messageApi.error(t('libraryPage.bgmSearchFailed', { error: error.message }))
    } finally {
      setSearchBgmLoading(false)
    }
  }

  // 排序选项配置（每个维度只有一项，点击同一项切换升降序）
  const SORT_OPTIONS = [
    { key: 'anime_created',   label: t('libraryPage.sortAnimeCreated') },
    { key: 'episode_fetched', label: t('libraryPage.sortEpisodeFetched') },
  ]
  const currentSortLabel = SORT_OPTIONS.find(o => o.key === sortBy)?.label || t('libraryPage.sortLabel')

  const sortDropdownItems = {
    items: SORT_OPTIONS.map(opt => {
      const isActive = opt.key === sortBy
      // 激活项显示当前实际方向箭头；非激活项显示降序箭头（默认方向预览）
      const arrowIcon = isActive
        ? (sortOrder === 'asc' ? 'arrowTop-fill' : 'xiajiantou-')
        : 'xiajiantou-'
      return {
        key: opt.key,
        label: (
          <span className="flex items-center gap-2">
            <span style={{ fontWeight: isActive ? 600 : 400, color: isActive ? 'var(--ant-color-primary)' : undefined }}>
              {opt.label}
            </span>
            <MyIcon
              icon={arrowIcon}
              size={13}
              style={{ color: isActive ? 'var(--ant-color-primary)' : undefined }}
            />
          </span>
        ),
      }
    }),
    onClick: ({ key }) => {
      if (key === sortBy) {
        // 同一维度：切换升降序
        const newOrder = sortOrder === 'desc' ? 'asc' : 'desc'
        setSortOrder(newOrder)
        setConfig('librarySortOrder', newOrder)
      } else {
        // 切换维度：保持当前升降序方向
        setSortBy(key)
        setConfig('librarySortBy', key)
      }
      setPagination(n => ({ ...n, current: 1 }))
    },
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title={t('libraryPage.pageTitle')}
        extra={
          !isMobile && (
            <Space>
              <Input.Search
                placeholder={t('libraryPage.placeholderSearch')}
                value={searchInputValue}
                onChange={(e) => setSearchInputValue(e.target.value)}
                onSearch={handleSearch}
                enterButton={t('libraryPage.btnSearch')}
                allowClear
                style={{ width: 300 }}
              />
              {keyword && (
                <Button onClick={handleReset}>
                  {t('libraryPage.btnReset')}
                </Button>
              )}
              {/* 视图切换 */}
              <Space.Compact>
                <Tooltip title={t('libraryPage.tipListView')}>
                  <Button
                    type={viewMode === 'list' ? 'primary' : 'default'}
                    icon={<UnorderedListOutlined />}
                    onClick={() => switchViewMode('list')}
                  />
                </Tooltip>
                <Tooltip title={t('libraryPage.tipCardView')}>
                  <Button
                    type={viewMode === 'card' ? 'primary' : 'default'}
                    icon={<AppstoreOutlined />}
                    onClick={() => switchViewMode('card')}
                  />
                </Tooltip>
              </Space.Compact>
              <Dropdown menu={sortDropdownItems}>
                <Button>
                  <span className="flex items-center gap-1">
                    {currentSortLabel}
                    <MyIcon icon={sortOrder === 'asc' ? 'arrowTop-fill' : 'xiajiantou-'} size={14} />
                  </span>
                </Button>
              </Dropdown>
              <Button onClick={() => setIsScanDuplicatesOpen(true)}>
                {t('libraryPage.btnScanDuplicates')}
              </Button>

              <Button type="primary" onClick={() => setIsCreateModalOpen(true)}>
                {t('libraryPage.btnCustomEntry')}
              </Button>
            </Space>
          )
        }
      >
        {isMobile && (
          <div className="mb-4">
            <div className="flex gap-2 mb-3 items-center">
              <div className="flex flex-1" style={{ height: 40 }}>
                <Input
                  placeholder={t('libraryPage.placeholderSearch')}
                  value={searchInputValue}
                  onChange={(e) => setSearchInputValue(e.target.value)}
                  onPressEnter={handleSearch}
                  allowClear
                  style={{
                    height: 40,
                    borderTopRightRadius: 0,
                    borderBottomRightRadius: 0,
                  }}
                />
                <Button
                  type="primary"
                  onClick={handleSearch}
                  style={{
                    height: 40,
                    flexShrink: 0,
                    borderTopLeftRadius: 0,
                    borderBottomLeftRadius: 0,
                    borderTopRightRadius: 8,
                    borderBottomRightRadius: 8,
                  }}
                >
                  {t('libraryPage.btnSearch')}
                </Button>
              </div>
              {keyword && (
                <Button onClick={handleReset} style={{ height: 40, flexShrink: 0 }}>
                  {t('libraryPage.btnReset')}
                </Button>
              )}
            </div>
            <div className="space-y-2">
              <div className="flex gap-2">
                <Button
                  block
                  size="large"
                  onClick={() => setIsScanDuplicatesOpen(true)}
                >
                  {t('libraryPage.btnScanDuplicates')}
                </Button>

              </div>
              <div className="flex gap-2">
                <Dropdown menu={sortDropdownItems}>
                  <Button block size="large">
                    <span className="flex items-center justify-center gap-1">
                      {currentSortLabel}
                      <MyIcon icon={sortOrder === 'asc' ? 'arrowTop-fill' : 'xiajiantou-'} size={15} />
                    </span>
                  </Button>
                </Dropdown>
                <Button
                  type="primary"
                  block
                  size="large"
                  onClick={() => setIsCreateModalOpen(true)}
                >
                  {t('libraryPage.btnCustomEntry')}
                </Button>
              </div>
              <div className="flex gap-2">
                <Button
                  block
                  size="large"
                  type={viewMode === 'list' ? 'primary' : 'default'}
                  icon={<UnorderedListOutlined />}
                  onClick={() => switchViewMode('list')}
                >
                  {t('libraryPage.labelListView')}
                </Button>
                <Button
                  block
                  size="large"
                  type={viewMode === 'card' ? 'primary' : 'default'}
                  icon={<AppstoreOutlined />}
                  onClick={() => switchViewMode('card')}
                >
                  {t('libraryPage.labelCardView')}
                </Button>
              </div>
            </div>
          </div>
        )}
        {/* ===== 统一渲染区：LibraryGroupView 包裹列表/卡片，行/卡可拖拽分组 ===== */}
        <LibraryGroupView
          list={list}
          groups={groups}
          loading={loading}
          viewMode={viewMode}
          columns={columns}
          onEdit={async (record) => {
            const res = await getAnimeDetail({ animeId: record.animeId })
            form.resetFields()
            form.setFieldsValue({ ...(res.data || {}), animeId: record.animeId })
            setLocalImagePath(res.data?.localImagePath || null)
            setEditOpen(true)
          }}
          onDelete={handleDelete}
          onNavigate={(record) => navigate(`/anime/${record.animeId}`)}
          onFavorite={(record) => handleFavorite(record)}
          onIncremental={(record) => handleIncremental(record)}
          onFinished={(record) => handleFinished(record)}
          onSetGroup={handleSetGroup}
          onCreateGroup={handleCreateGroup}
          onRenameGroup={handleRenameGroup}
          onDeleteGroup={handleDeleteGroup}
          onDeleteGroupSilent={handleDeleteGroupSilent}
        />

        {/* 分页器：在 LibraryGroupView 外部统一显示 */}
        {pagination.total > 0 && (
          <div className="mt-4 flex justify-end">
            <Pagination
              current={pagination.current}
              pageSize={pagination.pageSize}
              total={pagination.total}
              showTotal={total => t('libraryPage.totalItems', { total })}
              showSizeChanger
              hideOnSinglePage
              onChange={(page, pageSize) => {
                setPagination(n => ({ ...n, current: page, pageSize }))
              }}
              onShowSizeChange={(_, size) => {
                setPagination(n => ({ ...n, pageSize: size }))
              }}
            />
          </div>
        )}
      </Card>
      <CreateAnimeModal
        open={isCreateModalOpen}
        onCancel={() => setIsCreateModalOpen(false)}
        onSuccess={handleCreateSuccess}
      />
      <ScanDuplicatesModal
        open={isScanDuplicatesOpen}
        onCancel={() => setIsScanDuplicatesOpen(false)}
        onSuccess={() => { setIsScanDuplicatesOpen(false); fetchList() }}
      />
      <Modal
        title={t('libraryPage.editTitle')}
        open={editOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText={t('common.cancel')}
        okText={t('common.confirm')}
        onCancel={() => {
          setEditOpen(false)
          setFetchedMetadata(null)
          setLocalImagePath(null)
        }}
        zIndex={100}
      >
        <Form form={form} layout="horizontal">
          <Form.Item
            name="title"
            label={t('libraryPage.labelName')}
            rules={[{ required: true, message: t('libraryPage.ruleName') }]}
          >
            <Input placeholder={t('libraryPage.placeholderName')} />
          </Form.Item>
          <Form.Item
            name="type"
            label={t('libraryPage.labelType')}
            rules={[{ required: true, message: t('libraryPage.ruleType') }]}
          >
            <Select
              options={[
                {
                  value: 'tv_series',
                  label: DANDAN_TYPE_DESC_MAPPING['tv_series'],
                },
                {
                  value: 'movie',
                  label: DANDAN_TYPE_DESC_MAPPING['movie'],
                },
              ]}
            />
          </Form.Item>
          <Form.Item name="season" label={t('libraryPage.labelSeason')}>
            <InputNumber style={{ width: '100%' }} placeholder={t('libraryPage.placeholderSeason')} />
          </Form.Item>
          <Form.Item name="episodeCount" label={t('libraryPage.labelEpisodeCount')}>
            <InputNumber
              style={{ width: '100%' }}
              placeholder={t('libraryPage.placeholderEpisodeAuto')}
            />
          </Form.Item>
          <Form.Item name="year" label={t('libraryPage.labelYear')}>
            <InputNumber
              style={{ width: '100%' }}
              placeholder={t('libraryPage.placeholderYear')}
            />
          </Form.Item>
          <Form.Item label={t('libraryPage.labelPosterUrl')}>
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="imageUrl" noStyle>
                <Input placeholder="https://..." style={{ flex: 1 }} />
              </Form.Item>
              <Tooltip title={t('libraryPage.tipSearchPoster')}>
                <Button
                  size="small"
                  icon={<SearchOutlined />}
                  onClick={() => setPosterSearchVisible(true)}
                />
              </Tooltip>
              <Tooltip title={t('libraryPage.tipUrlDirectSearch')}>
                <Button
                  size="small"
                  icon={<LinkOutlined />}
                  loading={downloadingLocal}
                  onClick={async () => {
                    if (!imageUrl) {
                      messageApi.warning(t('libraryPage.posterUrlRequired'))
                      return
                    }
                    setDownloadingLocal(true)
                    try {
                      const res = await downloadPosterToLocal({
                        imageUrl,
                        title: title || '',
                        season: form.getFieldValue('season') || 1,
                        year: form.getFieldValue('year') || undefined,
                      })
                      if (res?.data?.localImagePath) {
                        setLocalImagePath(res.data.localImagePath)
                        messageApi.success(t('libraryPage.posterDownloaded'))
                      } else {
                        messageApi.error(t('libraryPage.posterDownloadFailed'))
                      }
                    } catch (error) {
                      messageApi.error(t('libraryPage.egFetchFailed', { error: error?.response?.data?.detail || error.message }))
                    } finally {
                      setDownloadingLocal(false)
                    }
                  }}
                />
              </Tooltip>
              <Tooltip title={t('libraryPage.tipRefreshPoster')}>
                <Button
                  size="small"
                  icon={<MyIcon icon="refresh" size={14} />}
                  onClick={async () => {
                    try {
                      await refreshPoster({ animeId, imageUrl })
                      messageApi.success(t('libraryPage.posterRefreshed'))
                    } catch (error) {
                      messageApi.error(t('libraryPage.posterRefreshFailed', { error: error.message }))
                    }
                  }}
                />
              </Tooltip>
            </Space.Compact>
          </Form.Item>
          {!!fetchedMetadata?.imageUrl &&
            fetchedMetadata?.imageUrl !== imageUrl && (
              <Form.Item className="text-right">
                <Button
                  className="cursor-pointer"
                  onClick={() => {
                    form.setFieldsValue({
                      imageUrl: fetchedMetadata.imageUrl,
                    })
                  }}
                >
                  {t('libraryPage.btnApplyUrl')}
                </Button>
              </Form.Item>
            )}

          {/* 本地海报行 */}
          <Form.Item label={t('libraryPage.labelLocalPoster')}>
            <Space style={{ width: '100%' }}>
              <Input
                value={localImagePath || t('libraryPage.localPosterNone')}
                readOnly
                style={{ flex: 1, minWidth: 300, color: localImagePath ? undefined : 'var(--text-tertiary, #999)' }}
              />
              <Tooltip title={t('libraryPage.tipPreviewPoster')}>
                <Button
                  icon={<EyeOutlined />}
                  disabled={!localImagePath}
                  onClick={() => setPreviewVisible(true)}
                />
              </Tooltip>
            </Space>
          </Form.Item>

          <Form.Item name="tmdbId" label="TMDB ID">
            <Input.Search
              placeholder={t('libraryPage.placeholderTmdbId')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              suffix={
                <Tooltip title={t('libraryPage.tipIdDirectSearch')}>
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'tmdb',
                        currentId: tmdbId,
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'tv'
                            : 'movie',
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchTmdbLoading}
              onSearch={() => {
                onTmdbSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="tmdbEpisodeGroupId" label={t('libraryPage.labelEgId')}>
            <Input.Search
              placeholder={t('libraryPage.placeholderEgid')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              loading={searchEgidLoading}
              prefix={egidValue?.startsWith?.('local-') ? <Tag color="green" className="mr-0">{t('libraryPage.tagLocal')}</Tag> : null}
              onSearch={() => {
                onEgidSearch()
              }}
              suffix={
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: 'id-search',
                        label: t('libraryPage.menuEgidSearch'),
                        onClick: async () => {
                          const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()
                          if (!egidValue) {
                            messageApi.warning(t('libraryPage.egIdRequired'))
                            return
                          }
                          try {
                            setSearchEgidLoading(true)
                            const res = await getAllEpisode({ tmdbId: tmdbId, egid: egidValue })
                            if (res?.data?.id) {
                              setAllEpisode(res.data)
                              setEpisodeOpen(true)
                            } else {
                              messageApi.error(t('libraryPage.egNoEpisodes'))
                            }
                          } catch (error) {
                            messageApi.error(t('libraryPage.egIdSearchFailed', { error: error?.response?.data?.detail || error.message }))
                          } finally {
                            setSearchEgidLoading(false)
                          }
                        },
                      },
                      {
                        key: 'local-json',
                        label: t('libraryPage.menuQueryLocalJson'),
                        onClick: () => {
                          setLocalPathOpen(true)
                        },
                      },
                      {
                        key: 'paste-json',
                        label: t('libraryPage.menuPasteJson'),
                        onClick: () => {
                          setPasteJsonOpen(true)
                        },
                      },
                      { type: 'divider' },
                      {
                        key: 'edit-eg',
                        label: t('libraryPage.menuViewEditEg'),
                        onClick: () => {
                          handleOpenEditEg()
                        },
                      },
                    ],
                  }}
                  trigger={['click']}
                  placement="bottomRight"
                >
                  <MenuOutlined
                    className="cursor-pointer opacity-60 transition-all hover:opacity-100"
                    style={{ fontSize: 14 }}
                  />
                </Dropdown>
              }
            />
          </Form.Item>
          <Form.Item name="bangumiId" label="BGM ID">
            <Input.Search
              placeholder={t('libraryPage.placeholderBgmId')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              suffix={
                <Tooltip title={t('libraryPage.tipIdDirectSearch')}>
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'bangumi',
                        currentId: bangumiId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchBgmLoading}
              onSearch={() => {
                onBgmSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="tvdbId" label="TVDB ID">
            <Input.Search
              placeholder={t('libraryPage.placeholderTvdbId')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              suffix={
                <Tooltip title={t('libraryPage.tipIdDirectSearch')}>
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'tvdb',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: tvdbId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchTvdbLoading}
              onSearch={() => {
                onTvdbSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="doubanId" label={t('libraryPage.labelDoubanId')}>
            <Input.Search
              placeholder={t('libraryPage.placeholderDoubanId')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              suffix={
                <Tooltip title={t('libraryPage.tipIdDirectSearch')}>
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'douban',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: doubanId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchDoubanLoading}
              onSearch={() => {
                onDoubanSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="imdbId" label="IMDB ID">
            <Input.Search
              placeholder={t('libraryPage.placeholderImdbId')}
              allowClear
              enterButton={t('libraryPage.btnSearch')}
              suffix={
                <Tooltip title={t('libraryPage.tipIdDirectSearch')}>
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'imdb',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: imdbId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchImdbLoading}
              onSearch={() => {
                onImdbSearch()
              }}
            />
          </Form.Item>
          <ApplyField
            name="nameEn"
            label={t('libraryPage.labelEnName')}
            fetchedValue={fetchedMetadata?.nameEn}
            form={form}
          />
          <ApplyField
            name="nameJp"
            label={t('libraryPage.labelJpName')}
            fetchedValue={
              containsJapanese(fetchedMetadata?.nameJp)
                ? fetchedMetadata.nameJp
                : null
            }
            form={form}
          />
          <ApplyField
            name="nameRomaji"
            label={t('libraryPage.labelRomaji')}
            fetchedValue={fetchedMetadata?.nameRomaji}
            form={form}
          />
          <ApplyField
            name="aliasCn1"
            label={t('libraryPage.labelAlias1')}
            fetchedValue={fetchedMetadata?.aliasesCn?.[0]}
            form={form}
          />
          <ApplyField
            name="aliasCn2"
            label={t('libraryPage.labelAlias2')}
            fetchedValue={fetchedMetadata?.aliasesCn?.[1]}
            form={form}
          />
          <ApplyField
            name="aliasCn3"
            label={t('libraryPage.labelAlias3')}
            fetchedValue={fetchedMetadata?.aliasesCn?.[2]}
            form={form}
          />
          <Form.Item
            name="aliasLocked"
            label={
              <Space>
                <span>{t('libraryPage.labelLockAlias')}</span>
                <Tooltip title={t('libraryPage.tipLockAlias')}>
                  <QuestionCircleOutlined />
                </Tooltip>
              </Space>
            }
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="animeId" hidden>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t('libraryPage.searchTmdbTitle', { title })}
        open={tmdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setTmdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tmdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="text-sm mt-2 line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'tmdb',
                          mediaType: type === 'tv_series' ? 'tv' : type,
                          currentId: item.id,
                        })
                        form.setFieldsValue({ tmdbId: res.data.id })

                        setFetchedMetadata(res.data)
                        setTmdbOpen(false)
                      }}
                    >
                      {t('libraryPage.btnSelect')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.searchImdbTitle', { title })}
        open={imdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setImdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={imdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'imdb',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ imdbId: res.data.id })

                        setFetchedMetadata(res.data)
                        setImdbOpen(false)
                      }}
                    >
                      {t('libraryPage.btnSelect')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.searchTvdbTitle', { title })}
        open={tvdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setTvdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tvdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'tvdb',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ tvdbId: res.data.id })
                        setFetchedMetadata(res.data)
                        setTvdbOpen(false)
                      }}
                    >
                      {t('libraryPage.btnSelect')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.searchEgidTitle', { title })}
        open={egidOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setEgidOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={egidResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div>
                    <div className="text-xl font-bold mb-3">
                      {t('libraryPage.egGroupInfo', { name: item.name, groups: item.groupCount, episodes: item.episodeCount })}
                    </div>
                    <div>{item.description || t('libraryPage.egNoDesc')}</div>
                  </div>
                  <div className="flex item-center justify-center gap-2">
                    <Button
                      type="primary"
                      size="small"
                      onClick={() => {
                        form.setFieldsValue({
                          tmdbEpisodeGroupId: item.id,
                        })
                        setEgidOpen(false)
                      }}
                    >
                      {t('libraryPage.btnApplyGroup')}
                    </Button>
                    <Button
                      type="default"
                      size="small"
                      loading={searchAllEpisodeLoading}
                      onClick={() => handleAllEpisode(item)}
                    >
                      {t('libraryPage.btnViewEpisodes')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.episodeDetailTitle', { name: allEpisode.name })}
        open={episodeOpen}
        footer={null}
        zIndex={120}
        onCancel={() => setEpisodeOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={allEpisode?.groups || []}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="text-base font-bold mb-2">
                  {item.name} (Order: {item.order})
                </div>
                {item.episodes?.map((ep, i) => {
                  // 计算绝对集数显示格式
                  // 特别季(season_number=0): S00EXX
                  // 正片(season_number=1): S01EXX (使用episode_number作为绝对序号)
                  const seasonNum = ep.season_number || ep.seasonNumber || 0
                  const episodeNum = ep.episode_number || ep.episodeNumber || (ep.order + 1)
                  const absoluteDisplay = `S${String(seasonNum).padStart(2, '0')}E${String(episodeNum).padStart(2, '0')}`

                  return (
                    <div key={i}>
                      {t('libraryPage.episodeInfo', { ep: ep.order + 1, abs: absoluteDisplay })}
                      {ep.name || t('libraryPage.episodeNoTitle')}
                    </div>
                  )
                })}
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.searchBgmTitle', { title })}
        open={bgmOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setBgmOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={bgmResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="text-sm mt-2 line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'bangumi',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ bangumiId: res.data.id })
                        setFetchedMetadata(res.data)
                        setBgmOpen(false)
                      }}
                    >
                      {t('libraryPage.btnSelect')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={t('libraryPage.searchDoubanTitle', { title })}
        open={doubanOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setDoubanOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={doubanResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'douban',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ doubanId: res.data.id })
                        setFetchedMetadata(res.data)
                        setDoubanOpen(false)
                      }}
                    >
                      {t('libraryPage.btnSelect')}
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      {/* 源选择弹窗 */}
      <Modal
        title={`${sourceSelectAction === 'favorite' ? t('libraryPage.sourceSelectTitleFav') : t('libraryPage.sourceSelectTitleInc')} - ${sourceSelectTitle}`}
        open={sourceSelectOpen}
        onOk={handleSourceSelectConfirm}
        onCancel={() => setSourceSelectOpen(false)}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        zIndex={110}
      >
        <div className="py-4">
          <Radio.Group
            value={selectedSourceId}
            onChange={(e) => setSelectedSourceId(e.target.value)}
            className="w-full"
          >
            <Space direction="vertical" className="w-full">
              {sourceSelectSources.map((source) => (
                <Radio key={source.sourceId} value={source.sourceId} className="w-full">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{source.providerName}</span>
                    {source.isFavorited && (
                      <Tag color="gold" className="ml-2">{t('libraryPage.tagMarked')}</Tag>
                    )}
                    {source.incrementalRefreshEnabled && (
                      <Tag color="green" className="ml-2">{t('libraryPage.tagIncremental')}</Tag>
                    )}
                  </div>
                </Radio>
              ))}
            </Space>
          </Radio.Group>
          <div className="mt-4 text-gray-500 text-sm">
            {sourceSelectAction === 'favorite'
              ? t('libraryPage.hintFavSingle')
              : t('libraryPage.hintIncSingle')
            }
          </div>
        </div>
      </Modal>
      {/* 本地剧集组预览 Modal */}
      <Modal
        title={t('libraryPage.localEgPreviewTitle')}
        open={localEgOpen}
        footer={null}
        zIndex={110}
        onCancel={() => {
          setLocalEgOpen(false)
          setLocalEgParsedData(null)
        }}
        width={600}
      >
        {localEgParsedData && (
          <div>
            <Card size="small" title={
              <span>
                {t('libraryPage.localEgSummary', { groups: localEgParsedData.groups?.length || 0, episodes: localEgParsedData.groups?.reduce((sum, g) => sum + (g.episodes?.length || 0), 0) })}
              </span>
            }>
              {localEgParsedData.description && (
                <Typography.Paragraph type="secondary" ellipsis={{ rows: 2, expandable: true }}>
                  {localEgParsedData.description}
                </Typography.Paragraph>
              )}
              <Collapse
                size="small"
                items={localEgParsedData.groups?.map((g, i) => ({
                  key: i,
                  label: t('libraryPage.localEgGroupLabel', { name: g.name || t('libraryPage.egGroupName', { index: i + 1 }), count: g.episodes?.length || 0 }),
                  children: (
                    <div className="max-h-40 overflow-y-auto text-sm">
                      {g.episodes?.map((ep, j) => (
                        <div key={j}>
                          S{String(ep.season_number ?? 0).padStart(2, '0')}
                          E{String(ep.episode_number ?? 0).padStart(2, '0')}
                          {' → '}Order: {ep.order}
                        </div>
                      ))}
                    </div>
                  ),
                }))}
              />
            </Card>
            <Button
              type="primary"
              className="mt-3 w-full"
              loading={localEgApplyLoading}
              onClick={handleLocalEgApply}
            >
              {t('libraryPage.btnApplyEg')}
            </Button>
          </div>
        )}
      </Modal>
      {/* 查询本地JSON路径 Modal */}
      <Modal
        title={t('libraryPage.fetchLocalEgTitle')}
        open={localPathOpen}
        onOk={handleLocalPathConfirm}
        confirmLoading={localPathLoading}
        okText={t('libraryPage.fetchLocalEgOk')}
        cancelText={t('common.cancel')}
        zIndex={110}
        onCancel={() => {
          setLocalPathOpen(false)
          setLocalPathValue('')
        }}
      >
        <div className="text-gray-500 text-sm mb-3">
          {t('libraryPage.fetchLocalEgDesc')}
        </div>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder={t('libraryPage.fetchLocalEgPlaceholder')}
            value={localPathValue}
            onChange={(e) => setLocalPathValue(e.target.value)}
            onPressEnter={handleLocalPathConfirm}
          />
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => setFileBrowserOpen(true)}
            title={t('libraryPage.tipBrowseServerFile')}
          />
        </Space.Compact>
      </Modal>
      {/* 服务端文件浏览器（选择JSON文件） */}
      <DirectoryBrowser
        visible={fileBrowserOpen}
        onClose={() => setFileBrowserOpen(false)}
        onSelect={(path) => {
          setLocalPathValue(path)
          setFileBrowserOpen(false)
        }}
        selectMode="file"
        fileFilter=".json"
      />
      {/* 粘贴JSON Modal */}
      <Modal
        title={t('libraryPage.pasteJsonTitle')}
        open={pasteJsonOpen}
        onOk={handlePasteJsonConfirm}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        zIndex={110}
        onCancel={() => {
          setPasteJsonOpen(false)
          setPasteJsonValue('')
        }}
      >
        <Input.TextArea
          rows={12}
          placeholder={t('libraryPage.pasteJsonPlaceholder')}
          value={pasteJsonValue}
          onChange={(e) => setPasteJsonValue(e.target.value)}
        />
      </Modal>
      {/* 查看/编辑剧集组 Modal */}
      <Modal
        title={t('libraryPage.editEgTitle', { id: editEgData?.id || '' })}
        open={editEgOpen}
        width={700}
        zIndex={110}
        onCancel={() => { setEditEgOpen(false); setEditEgData(null) }}
        footer={
          <div className="flex justify-end gap-2">
            <Button onClick={() => { setEditEgOpen(false); setEditEgData(null) }}>{t('common.cancel')}</Button>
            <Button type="primary" loading={editEgSaving} onClick={handleSaveEditEg}>
              {t('libraryPage.btnSaveChanges')}
            </Button>
          </div>
        }
      >
        {editEgData && (
          <div className="max-h-[60vh] overflow-y-auto">
            <div className="flex gap-3 mb-4">
              <div className="flex-1">
                <div className="text-xs text-gray-500 mb-1">{t('libraryPage.labelEgName')}</div>
                <Input
                  value={editEgData.name}
                  onChange={e => updateEditEgField(['name'], e.target.value)}
                  placeholder={t('libraryPage.placeholderEgName')}
                />
              </div>
              <div className="flex-1">
                <div className="text-xs text-gray-500 mb-1">{t('libraryPage.labelEgDesc')}</div>
                <Input
                  value={editEgData.description}
                  onChange={e => updateEditEgField(['description'], e.target.value)}
                  placeholder={t('libraryPage.placeholderEgDesc')}
                />
              </div>
            </div>
            <Collapse
              size="small"
              defaultActiveKey={editEgData.groups.map((_, i) => i)}
              items={editEgData.groups.map((group, gi) => ({
                key: gi,
                label: (
                  <div className="flex items-center gap-2 w-full">
                    <span className="font-bold">{group.name || t('libraryPage.egGroupName', { index: gi + 1 })}</span>
                    <Tag>{t('libraryPage.tagEpCount', { count: group.episodes.length })}</Tag>
                    <span className="text-xs text-gray-400">Order: {group.order}</span>
                  </div>
                ),
                extra: (
                  <Button
                    type="text" danger size="small"
                    onClick={e => { e.stopPropagation(); removeEgGroup(gi) }}
                  >
                    {t('libraryPage.btnDeleteGroup')}
                  </Button>
                ),
                children: (
                  <div>
                    <div className="flex gap-2 mb-2">
                      <Input
                        size="small" placeholder={t('libraryPage.placeholderGroupName')}
                        value={group.name}
                        onChange={e => updateEditEgField(['groups', gi, 'name'], e.target.value)}
                        style={{ width: 150 }}
                      />
                      <InputNumber
                        size="small" placeholder="Order"
                        value={group.order} min={0}
                        onChange={v => updateEditEgField(['groups', gi, 'order'], v ?? 0)}
                        style={{ width: 90 }}
                      />
                    </div>
                    <Table
                      size="small" pagination={false}
                      dataSource={group.episodes}
                      rowKey={(_, i) => i}
                      columns={[
                        {
                          title: t('libraryPage.egColSeason'), dataIndex: 'seasonNumber', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'seasonNumber'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: t('libraryPage.egColEpisode'), dataIndex: 'episodeNumber', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'episodeNumber'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: t('libraryPage.egColOrder'), dataIndex: 'order', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'order'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: t('libraryPage.egColTitle'), dataIndex: 'name',
                          render: (v, _, ei) => (
                            <Input size="small" value={v}
                              onChange={e => updateEditEgField(['groups', gi, 'episodes', ei, 'name'], e.target.value)}
                            />
                          ),
                        },
                        {
                          title: '', width: 50,
                          render: (_, __, ei) => (
                            <Button type="text" danger size="small"
                              onClick={() => removeEgEpisode(gi, ei)}
                            >
                              {t('libraryPage.btnDeleteEp')}
                            </Button>
                          ),
                        },
                      ]}
                    />
                    <Button size="small" type="dashed" className="mt-2 w-full"
                      onClick={() => addEgEpisode(gi)}
                    >
                      {t('libraryPage.btnAddEpisode')}
                    </Button>
                  </div>
                ),
              }))}
            />
            <Button type="dashed" className="mt-3 w-full" onClick={addEgGroup}>
              {t('libraryPage.btnAddGroup')}
            </Button>
          </div>
        )}
      </Modal>

      {/* 海报搜索弹窗 */}
      <PosterSearchModal
        visible={posterSearchVisible}
        onClose={() => setPosterSearchVisible(false)}
        onSelect={(posterUrl) => {
          form.setFieldsValue({ imageUrl: posterUrl })
          messageApi.success(t('libraryPage.posterUrlFilled'))
        }}
        defaultKeyword={title || ''}
        tmdbId={tmdbId}
        tvdbId={tvdbId}
        mediaType={type === 'movie' ? 'movie' : 'tv'}
      />

      {/* 本地海报预览 */}
      {previewVisible && localImagePath && (
        <Image
          style={{ display: 'none' }}
          preview={{
            visible: previewVisible,
            src: localImagePath,
            onVisibleChange: (vis) => setPreviewVisible(vis),
          }}
        />
      )}
    </div>
  )
}

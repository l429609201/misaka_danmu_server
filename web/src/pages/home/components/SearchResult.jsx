import {
  getEditEpisodes,
  getInLibraryEpisodes,
  getTmdbSearch,
  importDanmu,
  importEdit,
  getSearchResult,
  getAnimeLibrary,
} from '../../../apis'
import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import {
  Button,
  Card,
  Col,
  List,
  Row,
  Tag,
  Input,
  Modal,
  Radio,
  Form,
  Empty,
  InputNumber,
  Dropdown,
  Space,
  Checkbox,
  Popover,
  Select,
  Pagination,
  Spin,
  Segmented,
} from 'antd'
import { useAtom } from 'jotai'
import {
  isMobileAtom,
  lastSearchResultAtom,
  searchLoadingAtom,
} from '../../../../store'
import {
  CloseCircleOutlined,
  CalendarOutlined,
  CloudServerOutlined,
  DownOutlined,
  LinkOutlined,
  ReloadOutlined,
  SearchOutlined,
  ClearOutlined,
} from '@ant-design/icons'
import { DANDAN_TYPE_MAPPING } from '../../../configs'
import { useWatch } from 'antd/es/form/Form'

import { MyIcon } from '@/components/MyIcon'
import {
  closestCorners,
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

const IMPORT_MODE = [
  {
    key: 'separate',
    label: '作为多个独立条目导入',
  },
  {
    key: 'merge',
    label: '统一导入为单个条目',
  },
]

export const SearchResult = () => {
  const [form] = Form.useForm()
  const title = useWatch('title', form)
  const tmdbid = useWatch('tmdbid', form)
  const [tmdbList, setTmdbResult] = useState([])
  const [searchTmdbLoading, setSearchTmdbLoading] = useState(false)
  const [tmdbOpen, setTmdbOpen] = useState(false)

  const [isMobile] = useAtom(isMobileAtom)

  const [searchLoading] = useAtom(searchLoadingAtom)
  const [lastSearchResultData, setLastSearchResultData] = useAtom(lastSearchResultAtom)

  const [selectList, setSelectList] = useState([])

  const modalApi = useModal()
  const messageApi = useMessage()

  /** 编辑导入相关 */
  const [editImportOpen, setEditImportOpen] = useState(false)
  const [editEpisodeList, setEditEpisodeList] = useState([])
  const [editLoading, setEditLoading] = useState(false)
  const [editItem, setEditItem] = useState({})
  const [editAnimeTitle, setEditAnimeTitle] = useState('')
  const [activeItem, setActiveItem] = useState(null)
  const dragOverlayRef = useRef(null)
  const [editConfirmLoading, setEditConfirmLoading] = useState(false)
  const [range, setRange] = useState([1, 1])
  const [episodePageSize, setEpisodePageSize] = useState(10)
  const [episodePage, setEpisodePage] = useState(1)
  const [episodeOrder, setEpisodeOrder] = useState('asc') // 新增：排序状态
  const [editMediaType, setEditMediaType] = useState('tv_series') // 编辑导入：媒体类型
  const [editSeason, setEditSeason] = useState(1) // 编辑导入：季度

  // 重整分集导入子弹窗状态
  const [reshuffleOpen, setReshuffleOpen] = useState(false)
  const [reshuffleKeyword, setReshuffleKeyword] = useState('')
  const [reshuffleResults, setReshuffleResults] = useState([])
  const [reshuffleLoading, setReshuffleLoading] = useState(false)
  const [selectedReshuffleItem, setSelectedReshuffleItem] = useState(null)
  const [reshuffleConfirmLoading, setReshuffleConfirmLoading] = useState(false)

  // 补充源状态管理
  const [supplementMap, setSupplementMap] = useState({})
  // { 'bilibili_ss12345': { provider: '360', mediaId: 'xxx', title: 'xxx', enabled: true } }

  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 5,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        distance: 8,
        delay: 100,
      },
    })
  )

  const searchSeason = lastSearchResultData?.search_season
  const searchEpisode = lastSearchResultData?.search_episode
  const supplementalResults = lastSearchResultData?.supplemental_results || []

  const [loading, setLoading] = useState(false)

  const [batchOpen, setBatchOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)

  /** 导入模式 */
  const [importMode, setImportMode] = useState(IMPORT_MODE[0].key)

  /** 筛选条件 */
  const [typeFilter, setTypeFilter] = useState('all')
  const [yearFilter, setYearFilter] = useState('all')
  const [providerFilter, setProviderFilter] = useState('all')

  const [keyword, setKeyword] = useState('')

  /** 保存原始的年份和来源列表（不随过滤变化） */
  const [availableYears, setAvailableYears] = useState([])
  const [availableProviders, setAvailableProviders] = useState([])

  /** 自动加载模式 */
  const [autoLoadMode, setAutoLoadMode] = useState(false)
  const [accumulatedResults, setAccumulatedResults] = useState([])
  const scrollContainerRef = useRef(null)

  /** 渲染使用的数据 - 自动加载模式使用累积数据，否则使用后端返回的数据 */
  const renderData = autoLoadMode ? accumulatedResults : (lastSearchResultData.results || [])

  /** 分页相关 - 从后端数据获取 */
  const [pageSize, setPageSize] = useState(lastSearchResultData.pageSize || 10)
  const [currentPage, setCurrentPage] = useState(lastSearchResultData.page || 1)
  const [paginationLoading, setPaginationLoading] = useState(false)

  // 总数从后端获取
  const total = lastSearchResultData.total || 0

  // 是否还有更多数据可加载
  const hasMore = autoLoadMode && accumulatedResults.length < total

  // 后端过滤请求函数
  const fetchWithFilters = useCallback(async (page, size, filters = {}, isLoadMore = false) => {
    if (!lastSearchResultData.keyword) return
    setPaginationLoading(true)
    try {
      const res = await getSearchResult({
        keyword: lastSearchResultData.keyword,
        page,
        pageSize: size,
        typeFilter: filters.typeFilter || typeFilter,
        yearFilter: filters.yearFilter || yearFilter,
        providerFilter: filters.providerFilter || providerFilter,
        titleFilter: filters.titleFilter !== undefined ? filters.titleFilter : keyword,
      })

      const newData = res?.data || {}

      // 自动加载模式：累积数据
      if (isLoadMore && autoLoadMode) {
        setAccumulatedResults(prev => [...prev, ...(newData.results || [])])
      } else if (autoLoadMode) {
        // 自动加载模式首次加载
        setAccumulatedResults(newData.results || [])
      }

      setLastSearchResultData({
        ...newData,
        keyword: lastSearchResultData.keyword,
      })
      setCurrentPage(page)
      if (!autoLoadMode) {
        setPageSize(size)
      }
    } catch (error) {
      console.error(`请求失败: ${error.message || error}`)
    } finally {
      setPaginationLoading(false)
    }
  }, [lastSearchResultData.keyword, setLastSearchResultData, typeFilter, yearFilter, providerFilter, keyword, autoLoadMode])

  // 加载更多（自动加载模式）
  const loadMore = useCallback(() => {
    if (paginationLoading || !hasMore) return
    const nextPage = currentPage + 1
    fetchWithFilters(nextPage, 20, {}, true)
  }, [paginationLoading, hasMore, currentPage, fetchWithFilters])

  // 滚动监听（自动加载模式）
  useEffect(() => {
    if (!autoLoadMode || !scrollContainerRef.current) return

    const container = scrollContainerRef.current
    const handleScroll = () => {
      if (paginationLoading || !hasMore) return
      const { scrollTop, scrollHeight, clientHeight } = container
      // 距离底部 100px 时触发加载
      if (scrollHeight - scrollTop - clientHeight < 100) {
        loadMore()
      }
    }

    container.addEventListener('scroll', handleScroll)
    return () => container.removeEventListener('scroll', handleScroll)
  }, [autoLoadMode, paginationLoading, hasMore, loadMore])

  // 页码变化
  const handlePageChange = (page, size) => {
    if (size !== pageSize) {
      // pageSize 变化时，重置到第一页
      fetchWithFilters(1, size)
    } else {
      fetchWithFilters(page, size)
    }
  }

  // 切换分页模式
  const handleModeChange = (value) => {
    if (value === 'auto') {
      setAutoLoadMode(true)
      setAccumulatedResults(lastSearchResultData.results || [])
      // 自动加载模式使用固定的 pageSize
    } else {
      setAutoLoadMode(false)
      setAccumulatedResults([])
      setPageSize(value)
      fetchWithFilters(1, value)
    }
  }

  // 过滤条件变化时，重新请求后端（重置到第一页）
  const handleFilterChange = (filterType, value) => {
    const newFilters = {
      typeFilter,
      yearFilter,
      providerFilter,
      titleFilter: keyword,
    }
    newFilters[filterType] = value

    // 更新本地状态
    if (filterType === 'typeFilter') setTypeFilter(value)
    if (filterType === 'yearFilter') setYearFilter(value)
    if (filterType === 'providerFilter') setProviderFilter(value)
    if (filterType === 'titleFilter') setKeyword(value)

    // 重置自动加载累积数据
    if (autoLoadMode) {
      setAccumulatedResults([])
    }

    // 请求后端
    fetchWithFilters(1, autoLoadMode ? 20 : pageSize, newFilters)
  }

  useEffect(() => {
    setSelectList([])
  }, [renderData])

  useEffect(() => {
    if (searchLoading) {
      setYearFilter('all')
      setProviderFilter('all')
      // 新搜索开始时，清空可用列表（等待新数据）
      setAvailableYears([])
      setAvailableProviders([])
    }
  }, [searchLoading])

  const importModeText = useMemo(() => {
    const uniqueTitles = new Set(selectList.map(item => item.title))
    if (uniqueTitles.size === 1) {
      setImportMode('merge')
      return `您选择了 ${selectList.length} 个标题相同的条目。请确认导入模式。`
    } else {
      setImportMode('separate')
      return `检测到您选择的媒体标题不一致。请指定导入模式。`
    }
  }, [selectList])

  useEffect(() => {
    form.setFieldsValue({
      title: selectList?.[0]?.title?.split?.(' ')?.[0],
      tmdbid: null,
    })
  }, [selectList])

  // 注意：过滤现在由后端处理，不再需要前端过滤 useEffect

  // 当没有过滤条件且有新数据时，更新可用的年份和来源列表
  useEffect(() => {
    // 优先使用后端返回的全量过滤元数据
    if (lastSearchResultData.available_years?.length) {
      setAvailableYears(lastSearchResultData.available_years)
    }
    if (lastSearchResultData.available_providers?.length) {
      setAvailableProviders(lastSearchResultData.available_providers)
    }
  }, [lastSearchResultData.available_years, lastSearchResultData.available_providers])

  // 使用保存的可用列表，而不是从当前过滤后的数据中提取
  const years = availableYears
  const providers = availableProviders

  const handleImportDanmu = async item => {
    try {
      if (loading) return
      setLoading(true)

      // 检查是否有补充源 - 查找所有以主源key开头的补充源
      const mainKey = `${item.provider}_${item.mediaId}`
      const supplement = Object.entries(supplementMap).find(([key, value]) =>
        key.startsWith(mainKey + '_') && value?.enabled
      )?.[1]

      const res = await importDanmu({
        provider: item.provider,
        mediaId: item.mediaId,
        animeTitle: item.title,
        type: item.type,
        // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
        // 否则，使用从单个结果中解析出的季度
        season: searchSeason ?? item.season,
        year: item.year, // 新增年份
        imageUrl: item.imageUrl,
        doubanId: item.doubanId,
        currentEpisodeIndex: item.currentEpisodeIndex,
        // 新增: 补充源信息
        supplementProvider: supplement?.enabled ? supplement.provider : undefined,
        supplementMediaId: supplement?.enabled ? supplement.mediaId : undefined,
      })
      messageApi.success(res.data.message || '导入成功')
    } catch (error) {
      messageApi.error(`提交导入任务失败: ${error.detail || error}`)
    } finally {
      setLoading(false)
    }
  }

  const handleImportEdit = async () => {
    try {
      if (editConfirmLoading) return
      setEditConfirmLoading(true)
      const finalTitle = editAnimeTitle || editItem.title
      const finalMediaType = editMediaType
      const finalSeason = editMediaType === 'movie' ? 1 : editSeason
      const { animeTitle: _a, mediaType: _m, season: _s, episodes: _e, ...restEditItem } = editItem
      const res = await importEdit(
        JSON.stringify({
          ...restEditItem,
          animeTitle: finalTitle,
          mediaType: finalMediaType,
          season: finalSeason,
          episodes: editEpisodeList ?? [],
        })
      )
      messageApi.success(res.data?.message || '编辑导入任务已提交。')
    } catch (error) {
      messageApi.error(`提交导入任务失败: ${error.message}`)
    } finally {
      setEditConfirmLoading(false)
      setEditImportOpen(false)
      setEditEpisodeList([])
      setEditItem({})
      setEditAnimeTitle('')
      setEditMediaType('tv_series')
      setEditSeason(1)
    }
  }

  const handleBatchImport = () => {
    let tmdbparams = {}
    if (importMode === 'merge') {
      if (!title) {
        messageApi.error('最终导入名称不能为空。')
        return
      }
      tmdbparams = {
        tmdbId: `${tmdbid}`,
      }
    }
    modalApi.confirm({
      title: '批量导入',
      zIndex: 1002,
      content: (
        <div>
          确定要将 {selectList.length} 个条目
          {importMode === 'merge' ? '合并' : '分开'}导入吗？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          setConfirmLoading(true)
          const results = await Promise.allSettled(
            selectList.map(item => {
              console.log(item, '1')
              return importDanmu(
                JSON.stringify({
                  provider: item.provider,
                  mediaId: item.mediaId,
                  type: item.type,
                  season: item.season,
                  year: item.year,
                  imageUrl: item.imageUrl,
                  doubanId: item.doubanId,
                  currentEpisodeIndex: item.currentEpisodeIndex,
                  animeTitle: title ?? item.title,
                  ...tmdbparams,
                })
              )
            })
          )

          // 统计成功和失败的任务
          const successCount = results.filter(r => r.status === 'fulfilled').length
          const failedCount = results.filter(r => r.status === 'rejected').length

          if (successCount > 0) {
            if (failedCount > 0) {
              messageApi.warning(`已提交 ${successCount} 个任务，${failedCount} 个任务提交失败，请在任务管理器中查看进度。`)
            } else {
              messageApi.success('批量导入任务已提交，请在任务管理器中查看进度。')
            }
          } else {
            messageApi.error('所有任务提交失败')
          }

          setSelectList([])
          setConfirmLoading(false)
          setBatchOpen(false)
        } catch (err) {
          messageApi.error('批量导入失败')
        } finally {
          setConfirmLoading(false)
          setBatchOpen(false)
        }
      },
    })
  }

  const onTmdbSearch = async () => {
    try {
      if (searchTmdbLoading) return
      setSearchTmdbLoading(true)
      const res = await getTmdbSearch({
        keyword: title,
        mediaType:
          selectList?.[0]?.type === DANDAN_TYPE_MAPPING.tvseries
            ? 'tv'
            : 'movie',
      })
      if (!!res?.data?.length) {
        setTmdbResult(res?.data || [])
        setTmdbOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error('TMDB搜索失败')
    } finally {
      setSearchTmdbLoading(false)
    }
  }

  const handleDragEnd = event => {
    const { active, over } = event
    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    // 找到原位置和新位置

    setEditEpisodeList(list => {
      const activeIndex = list.findIndex(
        item => item.episodeId === active.data.current.item.episodeId
      )
      const overIndex = list.findIndex(
        item => item.episodeId === over.data.current.item.episodeId
      )

      if (activeIndex !== -1 && overIndex !== -1) {
        // 1. 重新排列数组
        const newList = [...editEpisodeList]
        const [movedItem] = newList.splice(activeIndex, 1)
        newList.splice(overIndex, 0, movedItem)

        // // 2. 重新计算所有项的display_order（从1开始连续编号）
        // const updatedList = newList.map((item, index) => ({
        //   ...item,
        //   episodeIndex: index + 1, // 排序值从1开始
        // }))

        return newList
      }
      return list
    })

    setActiveItem(null)
  }

  // 类型筛选菜单
  const typeMenu = {
    items: [
      {
        key: 'all',
        label: (
          <>
            <MyIcon icon="tvlibrary" size={16} className="mr-2" />
            所有类型
          </>
        ),
      },
      {
        key: DANDAN_TYPE_MAPPING.movie,
        label: (
          <>
            <MyIcon icon="movie" size={16} className="mr-2" />
            电影/剧场版
          </>
        ),
      },
      {
        key: DANDAN_TYPE_MAPPING.tvseries,
        label: (
          <>
            <MyIcon icon="tv" size={16} className="mr-2" />
            电视节目
          </>
        ),
      },
    ],
    onClick: ({ key }) => handleFilterChange('typeFilter', key),
  }

  // 年份筛选菜单
  const yearMenu = {
    items: [
      { key: 'all', label: '所有年份' },
      ...years.map(year => ({ key: year, label: `${year}年` })),
    ],
    onClick: ({ key }) => handleFilterChange('yearFilter', key === 'all' ? 'all' : Number(key)),
  }

  // 来源筛选菜单
  const providerMenu = {
    items: [
      { key: 'all', label: '所有来源' },
      ...providers.map(p => ({
        key: p,
        label: p.charAt(0).toUpperCase() + p.slice(1),
      })),
    ],
    onClick: ({ key }) => handleFilterChange('providerFilter', key),
  }

  // 处理拖拽开始
  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = editEpisodeList.find(item => item.episodeId === active.id)
    setActiveItem(item)
  }

  const handleDelete = item => {
    // 3. 更新状态
    setEditEpisodeList(list => {
      const activeIndex = list.findIndex(o => o.episodeId === item.episodeId)
      const newList = [...list]
      newList.splice(activeIndex, 1)

      // const updatedList = newList.map((item, index) => ({
      //   ...item,
      //   episodeIndex: index + 1, // 排序值从1开始
      // }))
      return newList
    })
  }

  const handleEditTitle = (item, value) => {
    setEditEpisodeList(list => {
      return list.map(it => {
        if (it.episodeId === item.episodeId) {
          return {
            ...it,
            title: value,
          }
        } else {
          return it
        }
      })
    })
  }

  const handleEditIndex = (item, value) => {
    setEditEpisodeList(list => {
      return list.map(it => {
        if (it.episodeId === item.episodeId) {
          return {
            ...it,
            episodeIndex: value,
          }
        } else {
          return it
        }
      })
    })
  }

  const renderDragOverlay = () => {
    if (!activeItem) return null

    return (
      <div ref={dragOverlayRef} style={{ width: '100%', maxWidth: '100%' }}>
        <List.Item
          style={{
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
            opacity: 0.9,
          }}
        >
          <div className="w-full flex items-center justify-between">
            <div>
              <MyIcon icon="drag" size={24} />
            </div>
            <div className="w-full flex items-center justify-between gap-3">
              <div>{activeItem.episodeIndex}</div>
              <Input
                style={{
                  width: '100%',
                }}
                value={activeItem.title}
                onChange={e => {}}
              />
              <div>
                <CloseCircleOutlined />
              </div>
            </div>
          </div>
        </List.Item>
      </div>
    )
  }

  // 新增：切换排序的处理函数
  const handleToggleOrder = () => {
    const newOrder = episodeOrder === 'asc' ? 'desc' : 'asc'
    setEpisodeOrder(newOrder)

    setEditEpisodeList(list => {
      const sortedList = [...list].sort((a, b) => {
        if (newOrder === 'asc') {
          return a.episodeIndex - b.episodeIndex
        } else {
          return b.episodeIndex - a.episodeIndex
        }
      })
      return sortedList
    })
  }

  // 补充源复选框处理
  const handleSupplementToggle = (mainItem, supplement, checked, customKey = null) => {
    // 使用自定义key或默认key
    const key = customKey || `${mainItem.provider}_${mainItem.mediaId}`

    if (checked) {
      // 如果勾选了新的补充源,需要取消同一主源的其他补充源
      const mainKey = `${mainItem.provider}_${mainItem.mediaId}`
      const newMap = { ...supplementMap }

      // 清除同一主源的其他补充源
      Object.keys(newMap).forEach(k => {
        if (k.startsWith(mainKey + '_') && k !== key) {
          delete newMap[k]
        }
      })

      // 设置新的补充源
      newMap[key] = {
        provider: supplement.provider,
        mediaId: supplement.mediaId,
        title: supplement.title,
        enabled: true
      }

      setSupplementMap(newMap)
    } else {
      // 取消勾选
      setSupplementMap(prev => {
        const newMap = { ...prev }
        delete newMap[key]
        return newMap
      })
    }
  }

  // 补充搜索
  const supplementDom = item => {
    if (item.episodeCount === 0) {
      const calculateSimilarity = (str1, str2) => {
        if (!str1 || !str2) return 0
        const s1 = str1.toLowerCase().trim()
        const s2 = str2.toLowerCase().trim()
        if (s1 === s2) return 100
        if (s1.includes(s2) || s2.includes(s1)) return 85
        // 简单的词汇匹配
        const words1 = s1.split(/\s+/)
        const words2 = s2.split(/\s+/)
        const commonWords = words1.filter(word => words2.includes(word))
        return (
          (commonWords.length / Math.max(words1.length, words2.length)) * 100
        )
      }

      // 查找所有匹配的补充源(相似度>80且支持分集URL且支持当前主源平台)
      const matching_supplements = supplementalResults.filter(
        sup => {
          // 基本条件: 不是同一个provider, 标题相似度>80, 支持分集URL
          if (sup.provider === item.provider) return false
          if (calculateSimilarity(item.title, sup.title) <= 80) return false
          if (sup.supportsEpisodeUrls !== true) return false

          // 检查补充源是否支持当前主源的平台
          const supportedProviders = sup.extra?.supported_providers || []
          if (supportedProviders.length === 0) {
            // 如果没有supported_providers信息,保持兼容性,允许显示
            return true
          }

          // 只有当补充源支持当前主源平台时才显示
          return supportedProviders.includes(item.provider)
        }
      )

      if (matching_supplements.length > 0) {
        const mainKey = `${item.provider}_${item.mediaId}`

        // 查找当前选中的补充源(不管是否启用)
        const selectedKey = Object.keys(supplementMap).find(k =>
          k.startsWith(mainKey + '_')
        )
        // key格式: provider_mediaId_supplementProvider_supplementMediaId
        // 提取 supplementProvider_supplementMediaId 作为 value
        const selectedProvider = selectedKey ? selectedKey.substring(mainKey.length + 1) : undefined
        const isEnabled = selectedKey ? (supplementMap[selectedKey]?.enabled || false) : false

        return (
          <div className="mt-2 p-2 bg-gray-100 dark:bg-gray-700 rounded-md flex items-center gap-2">
            <span className="text-sm text-gray-500 dark:text-gray-400 shrink-0">
              找到补充源:
            </span>
            <Select
              placeholder="选择补充源"
              value={selectedProvider}
              onChange={value => {
                // 如果选择了补充源
                if (value) {
                  // 使用唯一key来查找补充源
                  const supplement = matching_supplements.find(s => `${s.provider}_${s.mediaId}` === value)
                  if (supplement) {
                    const key = `${item.provider}_${item.mediaId}_${supplement.provider}_${supplement.mediaId}`
                    // 选择补充源时,不自动启用,需要用户勾选checkbox
                    setSupplementMap(prev => {
                      const newMap = { ...prev }
                      // 清除同一主源的其他补充源
                      Object.keys(newMap).forEach(k => {
                        if (k.startsWith(mainKey + '_') && k !== key) {
                          delete newMap[k]
                        }
                      })
                      // 添加新选择的补充源(但不启用)
                      newMap[key] = {
                        provider: supplement.provider,
                        mediaId: supplement.mediaId,
                        title: supplement.title,
                        enabled: false
                      }
                      return newMap
                    })
                  }
                } else {
                  // 如果清空选择,删除所有该主源的补充源
                  setSupplementMap(prev => {
                    const newMap = { ...prev }
                    Object.keys(newMap).forEach(k => {
                      if (k.startsWith(mainKey + '_')) {
                        delete newMap[k]
                      }
                    })
                    return newMap
                  })
                }
              }}
              allowClear
              style={{ minWidth: 200 }}
              options={matching_supplements.map(supplement => ({
                label: `${supplement.provider} - ${supplement.title}`,
                value: `${supplement.provider}_${supplement.mediaId}`
              }))}
            />
            {selectedProvider && (
              <Checkbox
                checked={isEnabled}
                onChange={e => {
                  e.stopPropagation()
                  // 使用唯一key来查找补充源
                  const supplement = matching_supplements.find(s => `${s.provider}_${s.mediaId}` === selectedProvider)
                  if (supplement) {
                    const key = `${item.provider}_${item.mediaId}_${supplement.provider}_${supplement.mediaId}`
                    handleSupplementToggle(item, supplement, e.target.checked, key)
                  }
                }}
              >
                使用补充源分集列表
              </Checkbox>
            )}
          </div>
        )
      }
      return null
    }
    return null
  }

  return (
    <>
      {lastSearchResultData && (
        <div className="border-t border-base-border mt-6 pt-6">
          <div className="text-lg font-semibold mb-4">搜索结果</div>
          <div>
            <div className="mb-6">
              {isMobile ? (
                /* 移动端：两行布局 */
                <div className="flex flex-col gap-2">
                  {/* 第一行：4个筛选按钮 */}
                  <div className="grid grid-cols-4 gap-2">
                    <Button
                      type="primary"
                      onClick={() => {
                        setSelectList(list =>
                          list.length === renderData.length ? [] : renderData
                        )
                      }}
                      disabled={!renderData.length}
                    >
                      {selectList.length === renderData.length && renderData.length
                        ? '取消全选'
                        : '全选'}
                    </Button>
                    <Dropdown menu={typeMenu}>
                      <Button className="w-full">
                        {typeFilter === 'all' ? (
                          <>
                            <MyIcon icon="tvlibrary" size={16} className="mr-1" />
                            类型
                          </>
                        ) : typeFilter === DANDAN_TYPE_MAPPING.movie ? (
                          <>
                            <MyIcon icon="movie" size={16} className="mr-1" />
                            电影
                          </>
                        ) : (
                          <>
                            <MyIcon icon="tv" size={16} className="mr-1" />
                            TV
                          </>
                        )}
                      </Button>
                    </Dropdown>
                    <Dropdown menu={yearMenu} disabled={!years.length}>
                      <Button icon={<CalendarOutlined />} className="w-full">
                        {yearFilter === 'all' ? '年份' : `${yearFilter}年`}
                      </Button>
                    </Dropdown>
                    <Dropdown menu={providerMenu} disabled={!providers.length}>
                      <Button icon={<CloudServerOutlined />} className="w-full">
                        {providerFilter === 'all'
                          ? '来源'
                          : providerFilter.charAt(0).toUpperCase() +
                            providerFilter.slice(1)}
                      </Button>
                    </Dropdown>
                  </div>
                  {/* 第二行：3个操作按钮均等分布 */}
                  <div className="grid grid-cols-3 gap-2">
                    <Popover
                      content={
                        <div style={{ width: 250 }}>
                          <Input.Search
                            placeholder="输入标题关键词过滤"
                            allowClear
                            value={keyword}
                            onChange={e => setKeyword(e.target.value)}
                            onSearch={value => handleFilterChange('titleFilter', value)}
                            enterButton="过滤"
                            autoFocus
                          />
                        </div>
                      }
                      title="过滤结果"
                      trigger="click"
                      placement="bottom"
                    >
                      <Button icon={<SearchOutlined />} className="w-full">
                        {keyword ? `过滤: ${keyword.length > 5 ? keyword.slice(0, 5) + '...' : keyword}` : '过滤'}
                      </Button>
                    </Popover>
                    <Button
                      icon={<ClearOutlined />}
                      className="w-full"
                      disabled={!renderData.length}
                      onClick={() => {
                        setLastSearchResultData({
                          results: [],
                          searchSeason: null,
                          keyword: '',
                        })
                        setSelectList([])
                        setKeyword('')
                        setYearFilter('all')
                        setProviderFilter('all')
                        setTypeFilter('all')
                      }}
                    >
                      清除
                    </Button>
                    <Button
                      className="w-full"
                      type="primary"
                      onClick={() => {
                        if (selectList.length === 0) {
                          messageApi.error('请选择要导入的媒体')
                          return
                        }
                        setBatchOpen(true)
                      }}
                      disabled={!renderData.length}
                    >
                      批量导入
                    </Button>
                  </div>
                </div>
              ) : (
                /* 桌面端：单行flex布局 */
                <div className="flex items-center gap-2 flex-wrap">
                  <Button
                    type="primary"
                    onClick={() => {
                      setSelectList(list =>
                        list.length === renderData.length ? [] : renderData
                      )
                    }}
                    disabled={!renderData.length}
                  >
                    {selectList.length === renderData.length && renderData.length
                      ? '取消全选'
                      : '全选'}
                  </Button>
                  <Dropdown menu={typeMenu}>
                    <Button>
                      {typeFilter === 'all' ? (
                        <>
                          <MyIcon icon="tvlibrary" size={16} className="mr-1" />
                          按类型
                        </>
                      ) : typeFilter === DANDAN_TYPE_MAPPING.movie ? (
                        <>
                          <MyIcon icon="movie" size={16} className="mr-1" />
                          电影/剧场版
                        </>
                      ) : (
                        <>
                          <MyIcon icon="tv" size={16} className="mr-1" />
                          电视节目
                        </>
                      )}
                    </Button>
                  </Dropdown>
                  <Dropdown menu={yearMenu} disabled={!years.length}>
                    <Button icon={<CalendarOutlined />}>
                      {yearFilter === 'all' ? '按年份' : `${yearFilter}年`}
                    </Button>
                  </Dropdown>
                  <Dropdown menu={providerMenu} disabled={!providers.length}>
                    <Button icon={<CloudServerOutlined />}>
                      {providerFilter === 'all'
                        ? '按来源'
                        : providerFilter.charAt(0).toUpperCase() +
                          providerFilter.slice(1)}
                    </Button>
                  </Dropdown>
                  <Popover
                    content={
                      <div style={{ width: 250 }}>
                        <Input.Search
                          placeholder="输入标题关键词过滤"
                          allowClear
                          value={keyword}
                          onChange={e => setKeyword(e.target.value)}
                          onSearch={value => handleFilterChange('titleFilter', value)}
                          enterButton="过滤"
                          autoFocus
                        />
                      </div>
                    }
                    title="过滤结果"
                    trigger="click"
                    placement="bottomRight"
                  >
                    <Button icon={<SearchOutlined />}>
                      {keyword ? `过滤: ${keyword.length > 5 ? keyword.slice(0, 5) + '...' : keyword}` : '过滤'}
                    </Button>
                  </Popover>
                  <Button
                    icon={<ClearOutlined />}
                    className="ml-auto"
                    disabled={!renderData.length}
                    onClick={() => {
                      setLastSearchResultData({
                        results: [],
                        searchSeason: null,
                        keyword: '',
                      })
                      setSelectList([])
                      setKeyword('')
                      setYearFilter('all')
                      setProviderFilter('all')
                      setTypeFilter('all')
                    }}
                  >
                    清除结果
                  </Button>
                  <Button
                    type="primary"
                    onClick={() => {
                      if (selectList.length === 0) {
                        messageApi.error('请选择要导入的媒体')
                        return
                      }
                      setBatchOpen(true)
                    }}
                    disabled={!renderData.length}
                  >
                    批量导入
                  </Button>
                </div>
              )}
            </div>
          {/* 分页信息和控件 */}
          <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
            <div className="text-sm text-gray-500">
              {autoLoadMode ? (
                <>已加载 {accumulatedResults.length} / {total} 条结果</>
              ) : (
                <>
                  共 {total} 条结果
                  {total > 0 && ` (第 ${(currentPage - 1) * pageSize + 1}-${Math.min(currentPage * pageSize, total)} 条)`}
                </>
              )}
            </div>
            <div className={`flex items-center ${isMobile ? 'gap-1' : 'gap-2'}`}>
              <span className={`text-gray-500 ${isMobile ? 'text-xs' : 'text-sm'}`}>显示</span>
              <Select
                value={autoLoadMode ? 'auto' : pageSize}
                onChange={handleModeChange}
                options={[
                  { label: '10条/页', value: 10 },
                  { label: '20条/页', value: 20 },
                  { label: '50条/页', value: 50 },
                  { label: '100条/页', value: 100 },
                  { label: '自动加载', value: 'auto' },
                ]}
                size="small"
                className={isMobile ? 'mobile-select-compact' : ''}
                style={{ width: isMobile ? 80 : 100 }}
              />
            </div>
          </div>
          {/* 固定高度滚动区域 */}
          <Spin spinning={paginationLoading}>
          <div
            ref={scrollContainerRef}
            className="overflow-y-auto overflow-x-hidden border border-gray-200 rounded-lg"
            style={{ maxHeight: '600px' }}
          >
          {!!renderData?.length ? (
            <List
              itemLayout="vertical"
              size="large"
              dataSource={renderData}
              footer={autoLoadMode && hasMore ? (
                <div className="text-center py-4 text-gray-500">
                  {paginationLoading ? '加载中...' : '滚动加载更多'}
                </div>
              ) : null}
              renderItem={item => {
                const isActive = selectList.includes(item)
                return (
                  <List.Item
                    key={`${item.mediaId}-${item.provider}`}
                    style={{ paddingLeft: isMobile ? 8 : 16, paddingRight: isMobile ? 8 : 16 }}
                  >
                    <Row gutter={[8, 8]}>
                      <Col md={15} xs={24}>
                        <div
                          className="flex items-center justify-start relative cursor-pointer"
                          onClick={() =>
                            setSelectList(list => {
                              return list.includes(item)
                                ? list.filter(i => i !== item)
                                : [...list, item]
                            })
                          }
                        >
                          <Checkbox checked={isActive} />
                          <img
                            width={60}
                            alt="logo"
                            src={item.imageUrl}
                            className="ml-3 aspect-[3/4]"
                          />
                          <div className="ml-4">
                            <div className="text-xl font-bold mb-3">
                              {item.title}
                              {item.type === 'movie' ? (
                                <MyIcon icon="movie" size={20} className="ml-2" />
                              ) : (
                                <MyIcon icon="tv" size={20} className="ml-2" />
                              )}
                              {item.url && (
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => e.stopPropagation()}
                                  className="ml-2 text-blue-500 hover:text-blue-700 inline-flex items-center"
                                  title="在平台打开"
                                >
                                  <LinkOutlined style={{ fontSize: '18px' }} />
                                </a>
                              )}
                            </div>
                            <div className="flex items-center flex-wrap gap-2">
                              <Tag color="magenta">
                                源：{item.provider ?? '未知'}
                              </Tag>
                              <Tag color="volcano">
                                年份：{item.year ?? '未知'}
                              </Tag>
                              <Tag color="orange">
                                季度：{item.season ?? '未知'}
                              </Tag>
                              <Tag color="gold">
                                总集数：{item.episodeCount ?? 0}
                              </Tag>
                              {searchEpisode && (
                                <Tag color="cyan">
                                  单集获取：{searchEpisode}
                                </Tag>
                              )}
                            </div>
                            {!isMobile && <>{supplementDom(item)}</>}
                          </div>
                        </div>
                        {isMobile && (
                          <div className="mt-3">{supplementDom(item)}</div>
                        )}
                      </Col>
                      <Col md={4} xs={{ span: 11, offset: 1 }}>
                        <Button
                          block
                          type="default"
                          className="mt-3"
                          loading={editLoading}
                          onClick={async () => {
                            try {
                              if (editLoading) return
                              setEditLoading(true)

                              // 检查是否有补充源 - 查找所有以主源key开头的补充源
                              const mainKey = `${item.provider}_${item.mediaId}`
                              const supplement = Object.entries(supplementMap).find(([key, value]) =>
                                key.startsWith(mainKey + '_') && value?.enabled
                              )?.[1]

                              // 构建请求参数
                              const params = {
                                provider: item.provider,
                                media_id: item.mediaId,
                                media_type: item.type,
                              }

                              // 如果启用了补充源,添加补充源参数
                              if (supplement?.enabled) {
                                params.supplement_provider = supplement.provider
                                params.supplement_media_id = supplement.mediaId
                              }

                              const res = await getEditEpisodes(params)
                              setEditEpisodeList(res.data)
                              setEditImportOpen(true)
                              setEditItem(item)
                              setEditMediaType(item.type || 'tv_series')
                              setEditSeason(item.season ?? 1)
                              // 修正：设置区间的结束值为总集数，如果总集数为0或不存在则为1
                              const endValue = item.episodeCount > 0 ? item.episodeCount : 1
                              setRange([1, endValue])
                            } catch (error) {
                            } finally {
                              setEditLoading(false)
                            }
                          }}
                        >
                          编辑导入
                        </Button>
                      </Col>
                      <Col md={4} xs={11}>
                        <Button
                          block
                          loading={loading}
                          type="primary"
                          className="mt-3"
                          onClick={() => {
                            handleImportDanmu(item)
                          }}
                        >
                          直接导入
                        </Button>
                      </Col>
                    </Row>
                  </List.Item>
                )
              }}
            />
          ) : (
            <Empty description="暂无搜索结果" />
          )}
          </div>
          </Spin>
          {/* 底部分页控件 - 自动加载模式下隐藏 */}
          {!autoLoadMode && total > pageSize && (
            <div className="flex justify-center mt-4">
              <Pagination
                current={currentPage}
                pageSize={pageSize}
                total={total}
                onChange={(page) => handlePageChange(page, pageSize)}
                showQuickJumper={!isMobile}
                showSizeChanger={false}
                showLessItems={isMobile}
                size={isMobile ? 'small' : 'default'}
              />
            </div>
          )}
        </div>
        </div>
      )}
      <Modal
        title="批量导入确认"
        open={batchOpen}
        onOk={handleBatchImport}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setBatchOpen(false)}
      >
        <div>
          <div className="mb-2">{importModeText}</div>
          <div className="text-base mb-2 font-bold">已选择的条目</div>
          <div className="max-h-[300px] overflow-y-auto">
            {selectList.map((item, index) => {
              return (
                <div
                  key={index}
                  className="my-3 p-2 rounded-xl border-gray-300/45 border"
                >
                  <div className="text-xl font-bold mb-2">
                    {item.title}
                    {item.type === 'movie' ? (
                      <MyIcon icon="movie" size={20} className="ml-2" />
                    ) : (
                      <MyIcon icon="tv" size={20} className="ml-2" />
                    )}
                  </div>
                  <div className="flex items-center flex-wrap gap-2">
                    <Tag color="magenta">源：{item.provider ?? '未知'}</Tag>
                    <Tag color="volcano">年份：{item.year ?? '未知'}</Tag>
                    <Tag color="orange">季度：{item.season ?? '未知'}</Tag>
                    <Tag color="gold">总集数：{item.episodeCount ?? 0}</Tag>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="text-base my-3 font-bold">导入模式</div>
          <Radio.Group
            value={importMode}
            onChange={e => setImportMode(e.target.value)}
            className="!mb-4"
          >
            {IMPORT_MODE.map(item => (
              <Radio key={item.key} value={item.key}>
                {item.label}
              </Radio>
            ))}
          </Radio.Group>
          {importMode === 'merge' && (
            <Form form={form} layout="horizontal">
              <Form.Item
                name="title"
                label="最终导入名称"
                rules={[{ required: true, message: '请输入最终导入名称' }]}
              >
                <Input.Search
                  placeholder="请输入最终导入名称"
                  allowClear
                  enterButton="搜索"
                  loading={searchTmdbLoading}
                  onSearch={onTmdbSearch}
                />
              </Form.Item>
              <Form.Item name="tmdbid" label="最终TMDB ID">
                <Input disabled placeholder="从TMDB搜索选择后自动填充" />
              </Form.Item>
            </Form>
          )}
        </div>
      </Modal>
      <Modal
        title="批量导入搜索 TMDB ID"
        open={tmdbOpen}
        footer={null}
        onCancel={() => setTmdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tmdbList}
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
                      onClick={() => {
                        form.setFieldsValue({
                          tmdbid: item.id,
                        })
                        setTmdbOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`编辑导入: ${editItem.title}`}
        open={editImportOpen}
        onCancel={() => {
          setEditImportOpen(false)
          setEditAnimeTitle('')
          setEditMediaType('tv_series')
          setEditSeason(1)
        }}
        footer={[
          <Button
            key="order"
            type={episodeOrder === 'asc' ? 'default' : 'primary'}
            onClick={handleToggleOrder}
            style={{ float: 'left' }}
          >
            {episodeOrder === 'asc' ? '正序' : '倒序'}
          </Button>,
          <Button key="cancel" onClick={() => {
            setEditImportOpen(false)
            setEditAnimeTitle('')
            setEditMediaType('tv_series')
            setEditSeason(1)
          }}>
            取消
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={editConfirmLoading}
            onClick={() => {
              handleImportEdit()
            }}
          >
            确认导入
          </Button>,
        ]}
        styles={{ body: { overflow: 'hidden', display: 'flex', flexDirection: 'column', maxHeight: isMobile ? '75vh' : '70vh', padding: isMobile ? '12px 16px' : undefined } }}
      >
          {isMobile ? (
            <div className="space-y-3 mb-3 shrink-0">
              <div>
                <div className="font-medium text-sm mb-2">作品标题</div>
                <Input
                  value={editAnimeTitle || editItem.title}
                  placeholder="请输入作品标题"
                  onChange={e => {
                    setEditAnimeTitle(e.target.value)
                  }}
                />
                <Button
                  type="default"
                  block
                  icon={<ReloadOutlined />}
                  onClick={() => setReshuffleOpen(true)}
                  className="mt-2"
                >
                  重整分集导入
                </Button>
              </div>
              <div>
                <div className="font-medium text-sm mb-2">类型 / 季度</div>
                <div className="flex items-center justify-between">
                  <Segmented
                    value={editMediaType}
                    onChange={value => {
                      setEditMediaType(value)
                      if (value === 'movie') setEditSeason(1)
                    }}
                    options={[
                      { label: <span className="inline-flex items-center gap-1"><MyIcon icon="movie" size={14} /> 电影</span>, value: 'movie' },
                      { label: <span className="inline-flex items-center gap-1"><MyIcon icon="tv" size={14} /> 电视节目</span>, value: 'tv_series' },
                    ]}
                  />
                  <div className="flex items-center gap-2">
                    <span className="text-sm">季度:</span>
                    <InputNumber
                      value={editSeason}
                      onChange={value => setEditSeason(value)}
                      min={1}
                      step={1}
                      disabled={editMediaType === 'movie'}
                      style={{ width: 70 }}
                    />
                  </div>
                </div>
              </div>
              <div>
                <div className="font-medium text-sm mb-2">集数区间</div>
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-sm">从</span>
                  <InputNumber
                    className="flex-1"
                    value={range[0]}
                    onChange={value => setRange(r => [value, r[1]])}
                    min={1}
                    max={range[1]}
                    step={1}
                  />
                  <span className="text-sm">到</span>
                  <InputNumber
                    className="flex-1"
                    value={range[1]}
                    onChange={value => setRange(r => [r[0], value])}
                    min={range[0]}
                    step={1}
                  />
                </div>
                <Button
                  type="primary"
                  block
                  onClick={() => {
                    setEditEpisodeList(list => {
                      return list.filter(
                        it =>
                          it.episodeIndex >= range[0] && it.episodeIndex <= range[1]
                      )
                    })
                  }}
                >
                  确认区间
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-wrap md:flex-nowrap justify-between items-center gap-3 my-6 shrink-0">
                <div className="shrink-0">作品标题:</div>
                <div className="w-full">
                  <Input
                    value={editAnimeTitle || editItem.title}
                    placeholder="请输入作品标题"
                    onChange={e => {
                      setEditAnimeTitle(e.target.value)
                    }}
                    style={{ width: '100%' }}
                  />
                </div>
                <Button
                  type="default"
                  onClick={() => setReshuffleOpen(true)}
                  icon={<ReloadOutlined />}
                  className="shrink-0"
                >
                  重整分集导入
                </Button>
              </div>
              <div className="flex items-wrap md:flex-nowrap justify-between items-center gap-3 my-6 shrink-0">
                <div className="flex items-center gap-2">
                  <Segmented
                    value={editMediaType}
                    onChange={value => {
                      setEditMediaType(value)
                      if (value === 'movie') setEditSeason(1)
                    }}
                    options={[
                      { label: <span className="inline-flex items-center gap-1"><MyIcon icon="movie" size={14} /> 电影</span>, value: 'movie' },
                      { label: <span className="inline-flex items-center gap-1"><MyIcon icon="tv" size={14} /> 电视节目</span>, value: 'tv_series' },
                    ]}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <span className="shrink-0">季度:</span>
                  <InputNumber
                    value={editSeason}
                    onChange={value => setEditSeason(value)}
                    min={1}
                    step={1}
                    disabled={editMediaType === 'movie'}
                    style={{ width: 80 }}
                  />
                </div>
              </div>
              <div className="flex items-wrap md:flex-nowrap justify-between items-center gap-3 my-6 shrink-0">
                <div className="shrink-0">集数区间:</div>
                <div className="w-full flex items-center justify-between flex-wrap md:flex-nowrap gap-2">
                  <div className="flex items-center justify-start gap-2">
                    <span>从</span>
                    <InputNumber
                      value={range[0]}
                      onChange={value => setRange(r => [value, r[1]])}
                      min={1}
                      max={range[1]}
                      step={1}
                      style={{
                        width: '100%',
                      }}
                    />
                    <span>到</span>
                    <InputNumber
                      value={range[1]}
                      onChange={value => setRange(r => [r[0], value])}
                      min={range[0]}
                      step={1}
                      style={{
                        width: '100%',
                      }}
                    />
                  </div>
                  <Button
                    type="primary"
                    block
                    onClick={() => {
                      setEditEpisodeList(list => {
                        return list.filter(
                          it =>
                            it.episodeIndex >= range[0] && it.episodeIndex <= range[1]
                        )
                      })
                    }}
                  >
                    确认区间
                  </Button>
                </div>
              </div>
            </>
          )}
          <Card
            size="small"
            className="flex-1 min-h-0"
            style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
            styles={{ body: { padding: '8px 12px', flex: 1, minHeight: 0, overflowY: 'auto' } }}
          >
            <DndContext
              sensors={sensors}
              collisionDetection={closestCorners}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={editEpisodeList.map(item => item.episodeId)}
                strategy={verticalListSortingStrategy}
              >
                <List
                  itemLayout="vertical"
                  size="large"
                  pagination={false}
                  dataSource={editEpisodeList.slice((episodePage - 1) * episodePageSize, episodePage * episodePageSize)}
                  renderItem={(item, index) => (
                    <SortableItem
                      key={item.episodeId}
                      item={item}
                      index={index}
                      handleDelete={() => handleDelete(item)}
                      handleEditTitle={value => handleEditTitle(item, value)}
                      handleEditIndex={value => handleEditIndex(item, value)}
                    />
                  )}
                />
              </SortableContext>

              {/* 拖拽覆盖层 */}
              <DragOverlay>{renderDragOverlay()}</DragOverlay>
            </DndContext>
          </Card>
          {editEpisodeList.length > episodePageSize && (
            <div className="flex justify-center items-center mt-3 shrink-0 gap-3">
              <Pagination
                current={episodePage}
                pageSize={episodePageSize}
                total={editEpisodeList.length}
                onChange={(page) => setEpisodePage(page)}
                showSizeChanger={false}
                showLessItems
                size="small"
              />
              <Dropdown
                menu={{
                  items: [
                    { key: '5', label: '5 条/页' },
                    { key: '10', label: '10 条/页' },
                    { key: '20', label: '20 条/页' },
                    { key: '50', label: '50 条/页' },
                  ],
                  selectedKeys: [String(episodePageSize)],
                  onClick: ({ key }) => {
                    setEpisodePageSize(Number(key))
                    setEpisodePage(1)
                  },
                }}
                trigger={['click']}
              >
                <Button size="small" className="shrink-0">
                  {episodePageSize} 条/页 <DownOutlined />
                </Button>
              </Dropdown>
            </div>
          )}
      </Modal>
      {/* 重整分集导入子弹窗 */}
      <Modal
        title="重整分集导入"
        open={reshuffleOpen}
        onCancel={() => {
          setReshuffleOpen(false)
          setReshuffleKeyword('')
          setReshuffleResults([])
          setSelectedReshuffleItem(null)
        }}
        footer={[
          <Button key="cancel" onClick={() => {
            setReshuffleOpen(false)
            setReshuffleKeyword('')
            setReshuffleResults([])
            setSelectedReshuffleItem(null)
          }}>
            取消
          </Button>,
          <Button
            key="confirm"
            type="primary"
            disabled={!selectedReshuffleItem}
            loading={reshuffleConfirmLoading}
            onClick={async () => {
              if (!selectedReshuffleItem) return
              setReshuffleConfirmLoading(true)
              try {
                const res = await getInLibraryEpisodes({
                  title: selectedReshuffleItem.title,
                  season: selectedReshuffleItem.season ?? 1,
                })
                if (!res.data?.length) {
                  messageApi.error(
                    `所选条目 "${selectedReshuffleItem.title}" 没有任何已存在的分集。`
                  )
                  return
                }
                const existingIndices = new Set(res.data)
                setEditEpisodeList(list =>
                  list.filter(it => !existingIndices.has(it.episodeIndex))
                )
                const removedCount = editEpisodeList.filter(it =>
                  existingIndices.has(it.episodeIndex)
                ).length
                messageApi.success(
                  `重整完成！根据 "${selectedReshuffleItem.title}" 的库内记录，移除了 ${removedCount} 个已存在的分集。`
                )
                setReshuffleOpen(false)
                setReshuffleKeyword('')
                setReshuffleResults([])
                setSelectedReshuffleItem(null)
              } catch (error) {
                messageApi.error(`查询已存在分集失败: ${error.message}`)
              } finally {
                setReshuffleConfirmLoading(false)
              }
            }}
          >
            确认过滤
          </Button>,
        ]}
      >
        <div className="mb-3" style={{ color: 'var(--color-text)' }}>
          💡 选择库内已有条目，将自动移除已存在的分集
        </div>
        <Input.Search
          placeholder="搜索库内条目..."
          allowClear
          enterButton={<SearchOutlined />}
          loading={reshuffleLoading}
          value={reshuffleKeyword}
          onChange={e => setReshuffleKeyword(e.target.value)}
          onSearch={async (value) => {
            if (!value?.trim()) {
              setReshuffleResults([])
              return
            }
            setReshuffleLoading(true)
            try {
              const res = await getAnimeLibrary({ keyword: value.trim(), pageSize: 20 })
              setReshuffleResults(res.data?.list || [])
            } catch (error) {
              messageApi.error('搜索失败')
            } finally {
              setReshuffleLoading(false)
            }
          }}
        />
        <Card
          size="small"
          className="mt-3 h-[40vh] overflow-y-auto"
          styles={{ body: { padding: '8px' } }}
        >
          {reshuffleResults.length > 0 ? (
            <Radio.Group
              value={selectedReshuffleItem?.animeId}
              onChange={e => {
                const item = reshuffleResults.find(r => r.animeId === e.target.value)
                setSelectedReshuffleItem(item)
              }}
              className="w-full"
            >
              <div className="space-y-2">
                {reshuffleResults.map(item => (
                  <div
                    key={item.animeId}
                    className="p-2 rounded-lg border border-gray-300/45 cursor-pointer hover:border-pink-400/60 transition-colors"
                    style={{
                      backgroundColor: selectedReshuffleItem?.animeId === item.animeId
                        ? 'var(--color-hover)' : undefined,
                    }}
                    onClick={() => setSelectedReshuffleItem(item)}
                  >
                    <Radio value={item.animeId}>
                      <span style={{ color: 'var(--color-text)' }}>
                        {item.title}
                        {item.type === 'movie' ? <MyIcon icon="movie" size={14} className="ml-1" /> : <MyIcon icon="tv" size={14} className="ml-1" />}
                        {item.type !== 'movie' && ` (S${String(item.season).padStart(2, '0')})`}
                        <span className="text-gray-400 ml-2 text-sm">
                          {item.year ? `${item.year}年` : ''} · {item.episodeCount}集
                        </span>
                      </span>
                    </Radio>
                  </div>
                ))}
              </div>
            </Radio.Group>
          ) : (
            reshuffleKeyword && !reshuffleLoading && (
              <Empty description="未找到匹配的条目" />
            )
          )}
        </Card>
      </Modal>
    </>
  )
}

const SortableItem = ({
  item,
  index,
  handleDelete,
  handleEditTitle,
  handleEditIndex,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.episodeId,
    data: {
      item,
      index,
    },
  })

  const inputRef = useRef(null)
  const [isFocused, setIsFocused] = useState(false)
  const inputNumberRef = useRef(null)
  const [isNumberFocused, setIsNumberFocused] = useState(false)

  useEffect(() => {
    if (isFocused && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isFocused, item.title])

  useEffect(() => {
    if (isNumberFocused && inputNumberRef.current) {
      inputNumberRef.current.focus()
    }
  }, [isNumberFocused, item.episodeIndex])

  // 拖拽样式
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    ...(isDragging && { cursor: 'grabbing' }),
  }

  return (
    <List.Item ref={setNodeRef} style={style}>
      {/* 保留你原有的列表项渲染逻辑 */}
      <div className="w-full flex items-center justify-between">
        <div {...attributes} {...listeners} style={{ cursor: 'grab' }}>
          <MyIcon icon="drag" size={24} />
        </div>
        <div className="w-full flex items-center justify-start gap-3">
          <InputNumber
            ref={inputNumberRef}
            value={item.episodeIndex}
            onChange={value => {
              handleEditIndex(value)
            }}
            onFocus={() => setIsNumberFocused(true)}
            onBlur={() => setIsNumberFocused(false)}
          />
          <Input
            ref={inputRef}
            style={{
              width: '100%',
            }}
            key={item.title}
            value={item.title}
            onChange={e => {
              console.log(e.target.value, 'e.target.value')
              handleEditTitle(e.target.value)
            }}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
          />
          <div onClick={() => handleDelete(item)}>
            <CloseCircleOutlined />
          </div>
        </div>
      </div>
    </List.Item>
  )
}

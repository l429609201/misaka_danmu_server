import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Input, Button, Card, Checkbox, Tag, Spin, Empty, Space, message, Dropdown, Pagination, Popover, Modal, Tooltip } from 'antd'
import { SyncOutlined, WarningOutlined, CheckCircleOutlined, CloseCircleOutlined, DownOutlined, SearchOutlined, DeleteOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import { MyIcon } from '../../components/MyIcon'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import {
  getIncrementalRefreshSources,
  getIncrementalRefreshTaskStatus,
  batchToggleIncrementalRefresh,
  batchSetFavorite,
  batchUnsetFavorite,
  toggleSourceIncremental,
  toggleSourceFavorite,
  toggleSourceFinished,
  batchSetSourceFinished,
  batchUnsetSourceFinished,
  deleteAnimeSource,
  getWeeklyCalendar,
  syncSchedule,
  clearCalendarCache,
  subscribeCalendarItem,
  batchSubscribeCalendarItems,
  unsubscribeCalendarItem,
} from '../../apis'
import dayjs from 'dayjs'
import { useDefaultPageSize } from '../../hooks/useDefaultPageSize'



export const BatchManagePage = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const defaultPageSize = useDefaultPageSize('refreshModal')

  // ---- State ----
  const [viewMode, setViewMode] = useState('list') // 'list' | 'calendar'
  const [loading, setLoading] = useState(false)
  const [taskStatus, setTaskStatus] = useState(null)
  const [animeGroups, setAnimeGroups] = useState([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selectedSourceIds, setSelectedSourceIds] = useState([])
  const [operationLoading, setOperationLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(defaultPageSize || 20)
  const [favoriteFilter, setFavoriteFilter] = useState('all')
  const [refreshFilter, setRefreshFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [finishedFilter, setFinishedFilter] = useState('all')
  const [sortBy, setSortBy] = useState('created')
  const [sortOrder, setSortOrder] = useState('desc')
  const [stats, setStats] = useState({ total: 0, totalSources: 0, refreshEnabled: 0, favorited: 0, maxFailures: 10 })
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [deleteFiles, setDeleteFiles] = useState(true)
  // Calendar state
  const [calendarData, setCalendarData] = useState({ weekly: {}, unscheduled: [], stats: {} })
  const [calendarLoading, setCalendarLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [calendarFilter, setCalendarFilter] = useState('local') // 'all' | 'local' | 'bangumi' | 'trakt'
  // 状态提升：批量选择的外部番（PC 端页头显示批量操作需要顶层访问）
  const [selectedExtItems, setSelectedExtItems] = useState([])

  useEffect(() => { if (defaultPageSize) setPageSize(defaultPageSize) }, [defaultPageSize])

  // ---- Fetch ----
  const fetchData = useCallback(async (params = {}) => {
    setLoading(true)
    try {
      const [sourcesRes, statusRes] = await Promise.all([
        getIncrementalRefreshSources({
          page: params.page ?? page,
          pageSize: params.pageSize ?? pageSize,
          keyword: params.keyword ?? searchKeyword,
          favoriteFilter: params.favoriteFilter ?? favoriteFilter,
          refreshFilter: params.refreshFilter ?? refreshFilter,
          typeFilter: params.typeFilter ?? typeFilter,
          finishedFilter: params.finishedFilter ?? finishedFilter,
          sortBy: params.sortBy ?? sortBy,
          sortOrder: params.sortOrder ?? sortOrder,
        }),
        getIncrementalRefreshTaskStatus(),
      ])
      const data = sourcesRes?.data || {}
      setAnimeGroups(data.list || [])
      setStats({
        total: data.total || 0,
        totalSources: data.totalSources || 0,
        refreshEnabled: data.refreshEnabled || 0,
        favorited: data.favorited || 0,
        maxFailures: data.maxFailures || 10,
      })
      setTaskStatus(statusRes?.data || null)
    } catch (error) {
      message.error(t('incrementalRefresh.loadFailed') + ': ' + error.message)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, searchKeyword, favoriteFilter, refreshFilter, typeFilter, finishedFilter, sortBy, sortOrder])

  useEffect(() => { fetchData({ page: 1 }) }, [])

  useEffect(() => {
    if (viewMode === 'list') {
      fetchData()
    }
  }, [viewMode])


  // ---- Calendar fetch ----
  const fetchCalendar = useCallback(async () => {
    setCalendarLoading(true)
    try {
      const res = await getWeeklyCalendar()
      setCalendarData(res.data || res)
    } catch { message.error(t('calendar.loadFailed')) }
    finally { setCalendarLoading(false) }
  }, [t])

  const handleSyncSchedule = async () => {
    setSyncing(true)
    try {
      const res = await syncSchedule()
      const d = res.data || res
      message.success(t('calendar.syncSuccess', { count: d.updatedCount }))
      fetchCalendar()
    } catch { message.error(t('calendar.syncFailed')) }
    finally { setSyncing(false) }
  }

  const handleClearCache = async () => {
    try {
      await clearCalendarCache()
      message.success(t('calendar.clearCacheSuccess'))
      fetchCalendar()
    } catch { message.error(t('calendar.clearCacheFailed')) }
  }

  useEffect(() => { if (viewMode === 'calendar') fetchCalendar() }, [viewMode, fetchCalendar])

  // ---- Handlers ----
  const handleSearch = (value) => { setSearchKeyword(value); setPage(1); fetchData({ page: 1, keyword: value }) }
  const handleFilterChange = (key, value) => {
    const setters = { favoriteFilter: setFavoriteFilter, refreshFilter: setRefreshFilter, typeFilter: setTypeFilter, finishedFilter: setFinishedFilter }
    setters[key]?.(value); setPage(1); fetchData({ page: 1, [key]: value })
  }
  const handleSortChange = (key) => {
    if (key === sortBy) { const o = sortOrder === 'desc' ? 'asc' : 'desc'; setSortOrder(o); setPage(1); fetchData({ page: 1, sortOrder: o }) }
    else { setSortBy(key); setPage(1); fetchData({ page: 1, sortBy: key }) }
  }
  const handlePageChange = (p) => { setPage(p); fetchData({ page: p }) }
  const handlePageSizeChange = (s) => { setPageSize(s); setPage(1); fetchData({ page: 1, pageSize: s }) }

  // Toggle single source
  const handleToggleRefresh = async (sourceId) => {
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return
    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.incrementalRefreshEnabled
    setAnimeGroups(prev => prev.map(g => {
      if (g.animeId !== group.animeId) return g
      return { ...g, sources: g.sources.map(s => {
        if (s.sourceId === sourceId) return { ...s, incrementalRefreshEnabled: newState }
        if (newState) return { ...s, incrementalRefreshEnabled: false }
        return s
      }) }
    }))
    try { await toggleSourceIncremental({ sourceId }) }
    catch (e) { message.error(t('incrementalRefresh.operationFailed') + ': ' + e.message); fetchData() }
  }

  const handleToggleFavorite = async (sourceId) => {
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return
    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.isFavorited
    setAnimeGroups(prev => prev.map(g => {
      if (g.animeId !== group.animeId) return g
      return { ...g, sources: g.sources.map(s => {
        if (s.sourceId === sourceId) return { ...s, isFavorited: newState }
        if (newState) return { ...s, isFavorited: false }
        return s
      }) }
    }))
    try { await toggleSourceFavorite({ sourceId }) }
    catch (e) { message.error(t('incrementalRefresh.operationFailed') + ': ' + e.message); fetchData() }
  }

  const handleToggleFinished = async (sourceId) => {
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return
    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.isFinished
    setAnimeGroups(prev => prev.map(g => ({
      ...g, sources: g.sources.map(s => s.sourceId === sourceId ? { ...s, isFinished: newState } : s)
    })))
    try { await toggleSourceFinished({ sourceId }) }
    catch (e) { message.error(t('incrementalRefresh.operationFailed') + ': ' + e.message); fetchData() }
  }

  // Batch operations
  const handleBatchOp = async (fn, successKey) => {
    if (selectedSourceIds.length === 0) { message.warning(t('incrementalRefresh.selectSourceFirst')); return }
    setOperationLoading(true)
    try { await fn({ sourceIds: selectedSourceIds }); message.success(t(successKey)); setSelectedSourceIds([]); fetchData() }
    catch (e) { message.error(t('incrementalRefresh.operationFailed') + ': ' + e.message) }
    finally { setOperationLoading(false) }
  }

  const handleBatchDelete = async () => {
    setOperationLoading(true)
    try {
      const cnt = selectedSourceIds.length
      await deleteAnimeSource({ sourceIds: selectedSourceIds, deleteFiles })
      message.success(t('incrementalRefresh.batchDeleteSubmitted', { count: cnt }))
      setSelectedSourceIds([]); setDeleteModalOpen(false); setDeleteFiles(true)
      const currentCount = animeGroups.flatMap(g => g.sources).length
      if (cnt >= currentCount && page > 1) { setPage(page - 1); fetchData({ page: page - 1 }) }
      else fetchData()
    } catch (e) { message.error(t('incrementalRefresh.operationFailed') + ': ' + e.message) }
    finally { setOperationLoading(false) }
  }

  const handleSelectAll = () => setSelectedSourceIds(animeGroups.flatMap(g => g.sources.map(s => s.sourceId)))
  const handleDeselectAll = () => setSelectedSourceIds([])
  const handleCheckboxChange = (sourceId, checked) => setSelectedSourceIds(prev => checked ? [...prev, sourceId] : prev.filter(id => id !== sourceId))
  const toggleGroupSelection = (group) => {
    const ids = group.sources.map(s => s.sourceId)
    const allSelected = ids.every(id => selectedSourceIds.includes(id))
    if (allSelected) {
      setSelectedSourceIds(prev => prev.filter(id => !ids.includes(id)))
    } else {
      setSelectedSourceIds(prev => [...new Set([...prev, ...ids])])
    }
  }

  // ---- Computed ----
  const finishedCount = animeGroups.reduce((acc, g) => acc + g.sources.filter(s => s.isFinished).length, 0)
  const statIconBgClass = {
    indigo: 'bg-indigo-500/10',
    blue: 'bg-blue-500/10',
    amber: 'bg-amber-500/10',
    green: 'bg-green-500/10',
  }
  const getPoster = (group) => {
    let src = group.localImagePath || group.imageUrl
    if (src?.startsWith('/images/')) src = src.replace('/images/', '/data/images/')
    return src
  }

  // ---- Render ----
  const taskChip = () => {
    if (!taskStatus || !taskStatus.exists) return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border border-gray-300/20 bg-gray-500/8 text-gray-500 dark:text-gray-400">
        <span className="w-1.5 h-1.5 rounded-full bg-current" /> {t('batchManage.taskNotConfigured')}
      </span>
    )
    if (!taskStatus.enabled) return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border border-yellow-500/20 bg-yellow-500/8 text-yellow-500">
        <span className="w-1.5 h-1.5 rounded-full bg-current" /> {t('batchManage.taskDisabled')}
      </span>
    )
    return (
      <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border border-green-500/20 bg-green-500/8 text-green-500">
        <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" /> {t('batchManage.taskEnabled')}
        {taskStatus.nextRunTime && <span className="opacity-60 ml-1">· {t('batchManage.nextRun')} {dayjs(taskStatus.nextRunTime).format('MM-DD HH:mm')}</span>}
      </span>
    )
  }

  return (
    <div className="my-6">
      <Card>


      {/* 页头 */}
      <div className="mb-6">

        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 flex-wrap min-w-0">
            <h1 className="text-2xl font-extrabold tracking-tight truncate">{t('batchManage.title')}</h1>
            {taskChip()}
          </div>
          {/* 视图切换 */}
          <div className="flex rounded-xl border border-gray-200 dark:border-white/6 overflow-hidden flex-shrink-0">
            <button onClick={() => setViewMode('list')}
              className={`px-3 py-1.5 text-xs font-medium transition ${viewMode === 'list' ? 'bg-indigo-500 text-white' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'}`}>
              ☰ {t('batchManage.viewList')}
            </button>
            <button onClick={() => setViewMode('calendar')}
              className={`px-3 py-1.5 text-xs font-medium transition ${viewMode === 'calendar' ? 'bg-indigo-500 text-white' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'}`}>
              📅 {t('batchManage.viewCalendar')}
            </button>
          </div>
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1.5">{t('batchManage.pageDesc')}</p>
      </div>

      {viewMode === 'list' ? (<>

      {/* 统计徽章 — 卡片上方整行 */}
      <div className="flex items-center justify-between gap-2 mb-3">
        {[
          { iconName: 'yuan', iconColor: '#818cf8', label: t('batchManage.statTotal'), value: stats.totalSources },
          { iconName: 'refresh', iconColor: '#4ade80', label: t('batchManage.statRefreshing'), value: stats.refreshEnabled },
          { iconName: 'favorites-fill', iconColor: '#facc15', label: t('batchManage.statFavorited'), value: stats.favorited },
          { iconName: 'wanjie1', iconColor: '#60a5fa', label: t('batchManage.statFinished'), value: finishedCount },
        ].map((s, i) => (
          <div key={i} className="flex-1 flex items-center justify-between px-2.5 py-1.5 rounded-lg border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-xs cursor-default min-w-0">
            <div className="flex items-center gap-1 min-w-0">
              <MyIcon icon={s.iconName} size={13} color={s.iconColor} />
              <span className="text-gray-500 dark:text-gray-400 truncate">{s.label}</span>
            </div>
            <span className="font-bold tabular-nums ml-1">{s.value}</span>
          </div>
        ))}
      </div>

      {/* 主内容区 */}
      <div>
        {/* 表格面板 */}
        <div className={`rounded-2xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] overflow-hidden ${isMobile ? 'flex flex-col max-h-[calc(100vh-300px)]' : ''}`}>
          {/* 工具栏 */}
          <div className="px-4 sm:px-5 py-3 sm:py-4 border-b border-gray-200 dark:border-white/6 flex items-center gap-2 flex-wrap">
            {(() => {
              const allIds = animeGroups.flatMap(g => g.sources.map(s => s.sourceId))
              const allSelected = allIds.length > 0 && allIds.every(id => selectedSourceIds.includes(id))
              return (
                <button
                  className={`px-3.5 py-1 rounded-full text-xs font-medium border transition flex items-center gap-1 ${allSelected ? 'border-transparent shadow-sm' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}
                  style={allSelected ? { backgroundColor: 'color-mix(in srgb, var(--color-primary) 12%, transparent)', color: 'var(--color-primary)', borderColor: 'color-mix(in srgb, var(--color-primary) 30%, transparent)' } : undefined}
                  onClick={() => allSelected ? handleDeselectAll() : handleSelectAll()}
                >
                  {allSelected ? '☑' : '☐'} {allSelected ? t('batchManage.sidebarDeselectAll') : t('batchManage.sidebarSelectAll')}
                </button>
              )
            })()}
            <Dropdown menu={{ items: [
              { key: 'all', label: t('incrementalRefresh.allTypes') },
              { key: 'movie', label: t('batchManage.typeMovie') },
              { key: 'tv_series', label: t('batchManage.typeTV') },
            ], selectedKeys: [typeFilter], onClick: ({ key }) => handleFilterChange('typeFilter', key) }} trigger={['click']}>
              <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition ${typeFilter !== 'all' ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                {t('incrementalRefresh.type')} ▾
              </button>
            </Dropdown>
            <Dropdown menu={{ items: [
              { key: 'all', label: t('incrementalRefresh.all') },
              { key: 'enabled', label: t('incrementalRefresh.refreshed') },
              { key: 'disabled', label: t('incrementalRefresh.notRefreshed') },
            ], selectedKeys: [refreshFilter], onClick: ({ key }) => handleFilterChange('refreshFilter', key) }} trigger={['click']}>
              <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition ${refreshFilter !== 'all' ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                {t('batchManage.colRefresh')} ▾
              </button>
            </Dropdown>
            <Dropdown menu={{ items: [
              { key: 'all', label: t('incrementalRefresh.all') },
              { key: 'favorited', label: t('incrementalRefresh.favoritedFilter') },
              { key: 'unfavorited', label: t('incrementalRefresh.unfavorited') },
            ], selectedKeys: [favoriteFilter], onClick: ({ key }) => handleFilterChange('favoriteFilter', key) }} trigger={['click']}>
              <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition ${favoriteFilter !== 'all' ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                {t('batchManage.colFavorite')} ▾
              </button>
            </Dropdown>
            <Dropdown menu={{ items: [
              { key: 'all', label: t('incrementalRefresh.all') },
              { key: 'finished', label: t('incrementalRefresh.finishedFilter') },
              { key: 'unfinished', label: t('incrementalRefresh.unfinished') },
            ], selectedKeys: [finishedFilter], onClick: ({ key }) => handleFilterChange('finishedFilter', key) }} trigger={['click']}>
              <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition ${finishedFilter !== 'all' ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                {t('batchManage.colFinished')} ▾
              </button>
            </Dropdown>
            <div className={isMobile ? 'flex gap-2 items-center w-full mt-1' : 'ml-auto flex gap-2 items-center'}>
              <Popover content={<div style={{ width: 200 }}><Input placeholder={t('incrementalRefresh.searchPlaceholder')} allowClear value={searchKeyword} onChange={e => setSearchKeyword(e.target.value)} onPressEnter={e => handleSearch(e.target.value)} autoFocus /></div>} trigger="click" placement="bottom">
                <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition ${searchKeyword ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                  🔍 {searchKeyword || t('incrementalRefresh.search')}
                </button>
              </Popover>
              <Dropdown menu={{ items: [
                { key: 'created', label: t('incrementalRefresh.sortCreated') },
                { key: 'title', label: t('incrementalRefresh.sortTitle') },
              ], selectedKeys: [sortBy], onClick: ({ key }) => handleSortChange(key) }} trigger={['click']}>
                <button className="px-3.5 py-1 rounded-full text-xs font-medium border border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition">
                  {sortBy === 'created' ? t('incrementalRefresh.sortCreated') : t('incrementalRefresh.sortTitle')} {sortOrder === 'asc' ? '↑' : '↓'}
                </button>
              </Dropdown>
              <Dropdown menu={{ items: [
                  { key: 'selectAll', label: `☑️ ${t('batchManage.sidebarSelectAll')}`, onClick: handleSelectAll },
                  { key: 'deselectAll', label: `⬜ ${t('batchManage.sidebarDeselectAll')}`, onClick: handleDeselectAll },
                  { type: 'divider' },
                  { key: 'refreshEnable', label: `${t('batchManage.sidebarBatchRefresh')} › ${t('batchManage.enable')}`, onClick: () => handleBatchOp(d => batchToggleIncrementalRefresh({ ...d, enabled: true }), 'incrementalRefresh.batchEnableSuccess') },
                  { key: 'refreshDisable', label: `${t('batchManage.sidebarBatchRefresh')} › ${t('batchManage.disable')}`, onClick: () => handleBatchOp(d => batchToggleIncrementalRefresh({ ...d, enabled: false }), 'incrementalRefresh.batchDisableSuccess') },
                  { type: 'divider' },
                  { key: 'favSet', label: `${t('batchManage.sidebarBatchFavorite')} › ${t('batchManage.enable')}`, onClick: () => handleBatchOp(batchSetFavorite, 'incrementalRefresh.batchFavoriteSuccess') },
                  { key: 'favUnset', label: `${t('batchManage.sidebarBatchFavorite')} › ${t('batchManage.disable')}`, onClick: () => handleBatchOp(batchUnsetFavorite, 'incrementalRefresh.batchUnfavoriteSuccess') },
                  { type: 'divider' },
                  { key: 'finishSet', label: `${t('batchManage.sidebarBatchFinished')} › ${t('batchManage.enable')}`, onClick: () => handleBatchOp(batchSetSourceFinished, 'incrementalRefresh.batchFinishedSuccess') },
                  { key: 'finishUnset', label: `${t('batchManage.sidebarBatchFinished')} › ${t('batchManage.disable')}`, onClick: () => handleBatchOp(batchUnsetSourceFinished, 'incrementalRefresh.batchUnfinishedSuccess') },
                  { type: 'divider' },
                  { key: 'delete', label: `🗑️ ${t('batchManage.sidebarBatchDelete')}`, danger: true, disabled: selectedSourceIds.length === 0, onClick: () => { if (selectedSourceIds.length > 0) setDeleteModalOpen(true) } },
                ] }} trigger={['click']} disabled={operationLoading}>
                  <button className={`px-3.5 py-1 rounded-full text-xs font-medium border transition flex items-center gap-1 ${selectedSourceIds.length > 0 ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
                    {t('batchManage.sidebarBatchOps')} {selectedSourceIds.length > 0 && <span className="bg-indigo-500 text-white text-[10px] px-1.5 py-0.5 rounded-full leading-none">{selectedSourceIds.length}</span>} ▾
                  </button>
                </Dropdown>
            </div>
          </div>

          {/* 表格 / 移动端卡片 */}
          <div className={isMobile ? 'p-3 space-y-3 flex-1 overflow-y-auto' : 'overflow-y-auto overflow-x-hidden'} style={isMobile ? undefined : { maxHeight: 'calc(100vh - 380px)', minHeight: 300 }}>
            {loading ? (
              <div className="flex justify-center py-12"><Spin /></div>
            ) : animeGroups.length === 0 ? (
              <Empty className="py-12" description={t('incrementalRefresh.noData')} />
            ) : isMobile ? (
              /* ===== 移动端卡片视图 ===== */
              animeGroups.map(group => {
                const isMulti = group.sources.length > 1
                const s = group.sources[0]
                const selectedCount = group.sources.filter(x => selectedSourceIds.includes(x.sourceId)).length
                const groupSelected = selectedCount > 0
                const groupAllSelected = selectedCount === group.sources.length
                const latestTime = isMulti
                  ? (() => { const l = group.sources.filter(x => x.lastRefreshLatestEpisodeAt).sort((a, b) => new Date(b.lastRefreshLatestEpisodeAt) - new Date(a.lastRefreshLatestEpisodeAt))[0]; return l ? dayjs(l.lastRefreshLatestEpisodeAt).format('MM-DD HH:mm') : '—' })()
                  : s.lastRefreshLatestEpisodeAt ? dayjs(s.lastRefreshLatestEpisodeAt).format('MM-DD HH:mm') : '—'
                return (
                  <div
                    key={isMulti ? group.animeId : s.sourceId}
                    onClick={() => toggleGroupSelection(group)}
                    className={`relative rounded-xl border p-3 transition cursor-pointer ${groupSelected ? 'ring-2 ring-inset shadow-sm' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] hover:border-indigo-300/60 dark:hover:border-indigo-500/40'}`}
                    style={groupSelected ? {
                      backgroundColor: 'color-mix(in srgb, var(--color-primary) 15%, transparent)',
                      borderColor: 'color-mix(in srgb, var(--color-primary) 40%, transparent)',
                      '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
                    } : undefined}
                  >
                    {groupSelected && (
                      <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10"
                        style={{ backgroundColor: 'var(--color-primary)' }}>
                        <span className="text-white text-[10px] font-bold">{groupAllSelected ? '✓' : selectedCount}</span>
                      </div>
                    )}
                    {/* 顶部：海报 + 信息 */}
                    <div className="flex items-start gap-2.5">
                      {getPoster(group) ? <img src={getPoster(group)} alt={group.animeTitle} className="w-10 h-14 rounded-md object-cover flex-shrink-0" /> : <div className="w-10 h-14 rounded-md bg-gray-200/10 dark:bg-white/6 flex-shrink-0" />}
                      <div className="min-w-0 flex-1">
                        <Tooltip title={group.animeTitle} placement="topLeft"><div className="font-semibold text-sm truncate cursor-default">{group.animeTitle}</div></Tooltip>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${group.animeType === 'movie' ? 'bg-purple-500/10 text-purple-400' : 'bg-indigo-500/10 text-indigo-400'}`}>{group.animeType === 'movie' ? t('batchManage.typeMovie') : t('batchManage.typeTV')}</span>
                          {group.animeType !== 'movie' && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400">{t('libraryGroup.seasonTag', { season: group.season })}</span>}
                          {isMulti && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-indigo-500/8 text-indigo-400">{t('batchManage.multiSource', { count: group.sources.length })}</span>}
                          {!isMulti && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-500/8 text-gray-500 dark:text-gray-400">{s.providerName}</span>}
                        </div>
                      </div>
                    </div>
                    {/* 底部：进度 + toggle + 更新时间 */}
                    <div className="flex items-center justify-between mt-2.5 pt-2 border-t border-gray-100 dark:border-white/3">
                      <span className="text-xs text-gray-500 dark:text-gray-400">{t('splitSource.episodeCountSuffix', { count: isMulti ? Math.max(...group.sources.map(x => x.episodeCount)) : s.episodeCount })}</span>
                      <div className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>
                        {isMulti ? (
                          <>
                            <MultiToggle sources={group.sources} field="incrementalRefreshEnabled" iconName="refresh" color="#4ade80" onToggle={handleToggleRefresh} />
                            <MultiToggle sources={group.sources} field="isFavorited" iconName="favorites-fill" color="#facc15" onToggle={handleToggleFavorite} />
                            <MultiToggle sources={group.sources} field="isFinished" iconName="wanjie1" color="#60a5fa" onToggle={handleToggleFinished} />
                          </>
                        ) : (
                          <>
                            <ToggleBtn on={s.incrementalRefreshEnabled} onClick={() => handleToggleRefresh(s.sourceId)} iconName="refresh" color="#4ade80" />
                            <ToggleBtn on={s.isFavorited} onClick={() => handleToggleFavorite(s.sourceId)} iconName="favorites-fill" color="#facc15" />
                            <ToggleBtn on={s.isFinished} onClick={() => handleToggleFinished(s.sourceId)} iconName="wanjie1" color="#60a5fa" />
                          </>
                        )}
                        <span className="text-[10px] text-gray-500 ml-1">{latestTime}</span>
                      </div>
                    </div>
                  </div>
                )
              })
            ) : (
              /* ===== 桌面端卡片列表视图 ===== */
              <>
                {/* 列表头 */}
                <div className="flex items-center px-5 py-2.5 text-[11px] uppercase tracking-wider text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-white/6 sticky top-0 bg-white dark:bg-[#1a1e2e] z-10">
                  <div className="w-[38%] cursor-pointer hover:text-gray-700 dark:hover:text-gray-200 transition flex items-center gap-1" onClick={() => handleSortChange('title')}>
                    {t('batchManage.colTitle')} <span className={`text-[10px] ${sortBy === 'title' ? 'text-indigo-400' : 'opacity-30'}`}>{sortBy === 'title' ? (sortOrder === 'asc' ? '↑' : '↓') : '⇅'}</span>
                  </div>
                  <div className="w-[10%]">{t('batchManage.colProgress')}</div>
                  <div className="w-[10%]">{t('batchManage.colMissing')}</div>
                  <div className="w-[10%]">{t('batchManage.colRefresh')}</div>
                  <div className="w-[10%]">{t('batchManage.colFavorite')}</div>
                  <div className="w-[10%]">{t('batchManage.colFinished')}</div>
                  <div className="w-[12%] cursor-pointer hover:text-gray-700 dark:hover:text-gray-200 transition" onClick={() => handleSortChange('created')}>
                    <div className="flex items-center gap-1">
                      <div className="flex flex-col leading-tight">
                        <span>{t('batchManage.colUpdatedLine1')}</span>
                        <span>{t('batchManage.colUpdatedLine2')}</span>
                      </div>
                      <div className="flex flex-col items-center text-[10px] leading-none gap-0.5">
                        <span className={sortBy === 'created' && sortOrder === 'asc' ? 'text-indigo-400' : 'opacity-30'}>▲</span>
                        <span className={sortBy === 'created' && sortOrder === 'desc' ? 'text-indigo-400' : 'opacity-30'}>▼</span>
                      </div>
                    </div>
                  </div>
                </div>
                {/* 卡片行列表 */}
                <div className="p-2 space-y-1">
                  {animeGroups.map((group, idx) => {
                    const isMulti = group.sources.length > 1
                    const s = group.sources[0]
                    const isSelected = isMulti
                      ? group.sources.some(x => selectedSourceIds.includes(x.sourceId))
                      : selectedSourceIds.includes(s.sourceId)
                    const latestTime = isMulti
                      ? (() => { const l = group.sources.filter(x => x.lastRefreshLatestEpisodeAt).sort((a, b) => new Date(b.lastRefreshLatestEpisodeAt) - new Date(a.lastRefreshLatestEpisodeAt))[0]; return l ? dayjs(l.lastRefreshLatestEpisodeAt).format('MM-DD HH:mm') : '—' })()
                      : s.lastRefreshLatestEpisodeAt ? dayjs(s.lastRefreshLatestEpisodeAt).format('MM-DD HH:mm') : '—'
                    return (
                      <div
                        key={isMulti ? group.animeId : s.sourceId}
                        onClick={() => toggleGroupSelection(group)}
                        className={`relative flex items-center px-5 py-5 rounded-2xl cursor-pointer transition-all select-none ${
                          isSelected
                            ? 'ring-2 ring-inset shadow-sm'
                            : idx % 2 === 1
                              ? 'bg-gray-50/60 dark:bg-white/[0.02] hover:bg-gray-100/70 dark:hover:bg-white/[0.04]'
                              : 'hover:bg-gray-50 dark:hover:bg-white/3'
                        }`}
                        style={isSelected ? {
                          backgroundColor: 'color-mix(in srgb, var(--color-primary) 15%, transparent)',
                          '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
                        } : undefined}
                      >
                        {/* 选中打钩 */}
                        {isSelected && (
                          <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10"
                            style={{ backgroundColor: 'var(--color-primary)' }}>
                            <span className="text-white text-[10px] font-bold">✓</span>
                          </div>
                        )}
                        {/* 作品 */}
                        <div className="w-[38%] min-w-0 pr-3">
                          <div className="flex items-center gap-3">
                            {getPoster(group)
                              ? <img src={getPoster(group)} alt={group.animeTitle} className="w-11 h-[60px] rounded-lg object-cover flex-shrink-0" />
                              : <div className="w-11 h-[60px] rounded-lg bg-gray-200/10 dark:bg-white/6 flex-shrink-0" />}
                            <div className="min-w-0 flex-1">
                              <Tooltip title={group.animeTitle} placement="topLeft">
                                <div className="font-semibold text-sm truncate">{group.animeTitle}</div>
                              </Tooltip>
                              <div className="flex gap-1 mt-0.5 flex-wrap">
                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${group.animeType === 'movie' ? 'bg-purple-500/10 text-purple-400' : 'bg-indigo-500/10 text-indigo-400'}`}>
                                  {group.animeType === 'movie' ? t('batchManage.typeMovie') : t('batchManage.typeTV')}
                                </span>
                                {group.animeType !== 'movie' && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-cyan-500/10 text-cyan-400">{t('libraryGroup.seasonTag', { season: group.season })}</span>}
                                {isMulti
                                  ? <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-md bg-indigo-500/8 text-indigo-400">{t('batchManage.multiSource', { count: group.sources.length })}</span>
                                  : <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-md bg-gray-500/8 text-gray-500 dark:text-gray-400">{s.providerName}</span>}
                              </div>
                            </div>
                          </div>
                        </div>
                        {/* 进度 */}
                        <div className="w-[10%] text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                          {t('splitSource.episodeCountSuffix', { count: isMulti ? Math.max(...group.sources.map(x => x.episodeCount)) : s.episodeCount })}
                        </div>
                        {/* 缺失 */}
                        <div className="w-[10%] text-xs text-gray-500 dark:text-gray-400">{t('batchManage.noMissing')}</div>
                        {/* 三个 Toggle，阻止冒泡避免触发行选中 */}
                        <div className="w-[10%]" onClick={e => e.stopPropagation()}>
                          {isMulti
                            ? <MultiToggle sources={group.sources} field="incrementalRefreshEnabled" iconName="refresh" color="#4ade80" onToggle={handleToggleRefresh} />
                            : <ToggleBtn on={s.incrementalRefreshEnabled} onClick={() => handleToggleRefresh(s.sourceId)} iconName="refresh" color="#4ade80" />}
                        </div>
                        <div className="w-[10%]" onClick={e => e.stopPropagation()}>
                          {isMulti
                            ? <MultiToggle sources={group.sources} field="isFavorited" iconName="favorites-fill" color="#facc15" onToggle={handleToggleFavorite} />
                            : <ToggleBtn on={s.isFavorited} onClick={() => handleToggleFavorite(s.sourceId)} iconName="favorites-fill" color="#facc15" />}
                        </div>
                        <div className="w-[10%]" onClick={e => e.stopPropagation()}>
                          {isMulti
                            ? <MultiToggle sources={group.sources} field="isFinished" iconName="wanjie1" color="#60a5fa" onToggle={handleToggleFinished} />
                            : <ToggleBtn on={s.isFinished} onClick={() => handleToggleFinished(s.sourceId)} iconName="wanjie1" color="#60a5fa" />}
                        </div>
                        {/* 最近更新 */}
                        <div className="w-[12%] text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                          {latestTime}
                          {!isMulti && s.incrementalRefreshEnabled && s.incrementalRefreshFailures > 0 && (
                            <><br /><span className="text-[10px] px-1.5 py-0.5 rounded-md bg-red-500/10 text-red-400">{t('incrementalRefresh.failureCount', { failures: s.incrementalRefreshFailures, max: stats.maxFailures })}</span></>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </>
            )}
          </div>

          {/* 分页 */}
          {stats.total > pageSize && (
            <div className="px-4 sm:px-5 py-3 border-t border-gray-200 dark:border-white/6 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
              <span>{t('incrementalRefresh.totalSources', { count: stats.totalSources })} · {t('incrementalRefresh.selectedCount', { count: selectedSourceIds.length })}</span>
              <div className="flex items-center gap-2">
                <Pagination current={page} pageSize={pageSize} total={stats.total} onChange={handlePageChange} showSizeChanger={false} size="small" simple={isMobile} />
                {!isMobile && <Dropdown menu={{ items: [10, 20, 50, 100].map(n => ({ key: String(n), label: t('incrementalRefresh.perPage', { size: n }) })), selectedKeys: [String(pageSize)], onClick: ({ key }) => handlePageSizeChange(Number(key)) }} trigger={['click']}>
                  <Button size="small">{t('incrementalRefresh.perPage', { size: pageSize })} <DownOutlined /></Button>
                </Dropdown>}
              </div>
            </div>
          )}
        </div>
      </div>
      </>) : (
        /* ========== 日历视图 ========== */
        <CalendarView
          data={calendarData}
          loading={calendarLoading}
          isMobile={isMobile}
          t={t}
          filter={calendarFilter}
          onFilterChange={setCalendarFilter}
          syncing={syncing}
          onSync={handleSyncSchedule}
          onClearCache={handleClearCache}
          selectedExtItems={selectedExtItems}
          setSelectedExtItems={setSelectedExtItems}
          setCalendarData={setCalendarData}
        />
      )}
      </Card>

      {/* 删除确认弹窗 */}
      <Modal title={t('incrementalRefresh.deleteConfirmTitle')} open={deleteModalOpen}
        onCancel={() => { setDeleteModalOpen(false); setDeleteFiles(true) }} onOk={handleBatchDelete}
        okText={t('incrementalRefresh.confirmDelete')} cancelText={t('common.cancel')}
        okButtonProps={{ danger: true, loading: operationLoading }}>
        <div className="py-4">
          <p className="mb-4" dangerouslySetInnerHTML={{ __html: t('incrementalRefresh.deleteConfirmContent', { count: `<strong>${selectedSourceIds.length}</strong>` }) }} />
          <Checkbox checked={deleteFiles} onChange={e => setDeleteFiles(e.target.checked)}>{t('incrementalRefresh.deleteFilesToo')}</Checkbox>
          <p className="text-gray-500 text-sm mt-2">{deleteFiles ? t('incrementalRefresh.deleteWithFiles') : t('incrementalRefresh.deleteRecordOnly')}</p>
        </div>
      </Modal>
    </div>
  )
}


// ---- Sub-components ----
const ToggleBtn = ({ on, onClick, iconName, color }) => (
  <button
    onClick={onClick}
    className={`h-7 px-2 rounded-full flex items-center justify-center gap-1 transition-all cursor-pointer text-xs font-medium ${
      on
        ? 'shadow-sm'
        : 'bg-gray-100 dark:bg-white/6 text-gray-400 dark:text-gray-500 hover:bg-gray-200 dark:hover:bg-white/10'
    }`}
    style={on ? {
      backgroundColor: `${color}22`,
      color: color,
      boxShadow: `0 0 0 1px ${color}44`,
    } : undefined}
  >
    <MyIcon icon={iconName} size={13} color={on ? color : undefined} />
  </button>
)

const MultiToggle = ({ sources, field, iconName, color, onToggle }) => {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const anyOn = sources.some(s => s[field])

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative inline-block" ref={ref}>
      <button
        className={`h-7 px-2 rounded-full flex items-center gap-1 transition-all cursor-pointer text-xs font-medium ${
          anyOn
            ? 'shadow-sm'
            : 'bg-gray-100 dark:bg-white/6 text-gray-400 dark:text-gray-500 hover:bg-gray-200 dark:hover:bg-white/10'
        }`}
        style={anyOn ? {
          backgroundColor: `${color}22`,
          color: color,
          boxShadow: `0 0 0 1px ${color}44`,
        } : undefined}
        onClick={() => setOpen(!open)}
      ><MyIcon icon={iconName} size={13} color={anyOn ? color : undefined} /> <span className="text-[9px] opacity-50">▾</span></button>
      {open && (
        <div className="absolute top-full mt-1 left-1/2 -translate-x-1/2 bg-white dark:bg-[#1a1e2e] border border-gray-200 dark:border-white/6 rounded-xl p-1.5 shadow-xl z-20 min-w-[140px]">
          {sources.map(s => (
            <div key={s.sourceId} className="flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-white/3 text-xs">
              <span className="font-medium text-gray-600 dark:text-gray-300">{s.providerName}</span>
              <ToggleBtn on={s[field]} onClick={() => onToggle(s.sourceId)} iconName={iconName} color={color} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


// ---- Calendar View ----
const DAYS_KEYS = ['calendar.mon', 'calendar.tue', 'calendar.wed', 'calendar.thu', 'calendar.fri', 'calendar.sat', 'calendar.sun']

// 外部番来源样式（提到组件外，避免 CalCard 重渲染时引用变化）
const ORIGIN_STYLE = {
  local: '',
  bangumi: 'border-dashed border-pink-400/30 bg-pink-500/[0.03] opacity-80',
  trakt: 'border-dashed border-red-400/30 bg-red-500/[0.03] opacity-80',
}

// 纯函数：稳定的 item 唯一 key
const getItemKey = (item) => `${item.origin}-${item.bangumiId || item.traktId || item.animeTitle}`

// ============ CalCard：海报卡片（顶层组件 + React.memo，防止父组件重渲染时 <img> 重新挂载导致海报重复请求 307） ============
const CalCard = React.memo(function CalCard({
  item, isToday, horizontal, day, isMobile, selected, t,
  posterSrc, displayTitle, displayYear, countdown,
  onToggleSelect, onSubscribe, onUnsubscribe, isSubscribing,
}) {
  const isExternal = !item.isLocal
  const selectedStyle = selected ? {
    backgroundColor: 'color-mix(in srgb, var(--color-primary) 15%, transparent)',
    borderColor: 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
    boxShadow: '0 0 0 1px color-mix(in srgb, var(--color-primary) 30%, transparent)',
  } : undefined
  const baseStyle = selected
    ? 'ring-2 ring-inset'
    : isExternal
      ? ORIGIN_STYLE[item.origin] || ''
      : (isToday ? 'border-indigo-200 dark:border-indigo-500/20 bg-indigo-50 dark:bg-indigo-500/4' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/2 hover:bg-gray-50 dark:hover:bg-white/4')

  // 竖版海报卡（行内横滑，仿小幻影视）
  if (horizontal) {
    const cardWidth = isMobile ? 'w-44' : 'w-36'
    return (
      <div
        className={`group/card ${cardWidth} flex-shrink-0 rounded-xl overflow-hidden border transition relative ${baseStyle} cursor-pointer hover:border-indigo-400/50 dark:hover:border-indigo-500/40`}
        style={{
          ...(selected ? { ...selectedStyle, '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)' } : {}),
        }}
        onClick={() => onToggleSelect(item)}
      >
        <div className="relative w-full aspect-[2/3] bg-gray-200 dark:bg-white/6">
          {posterSrc
            ? <img src={posterSrc} loading="lazy" alt="" onError={e => { e.currentTarget.style.visibility = 'hidden' }} className="w-full h-full object-cover" />
            : <div className="w-full h-full flex items-center justify-center text-gray-400 text-2xl">🎬</div>}
          {/* 左上角评分 */}
          {item.rating && <span className="absolute top-1 left-1 text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-black/55 text-yellow-400">★{item.rating}</span>}
          {/* 右上角来源角标：本地卡时按「本地 → 外部源」自上而下竖排 */}
          {item.isLocal && (
            <div className="absolute top-1 right-1 flex flex-col gap-0.5 items-end">
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md bg-emerald-500/85 text-white">{t('calendar.local') || '本地'}</span>
              {item.externalSources?.map((es, i) => (
                <span key={i} className={`text-[9px] font-bold px-1.5 py-0.5 rounded-md text-white flex items-center gap-0.5 ${es.origin === 'bangumi' ? 'bg-pink-500/85' : 'bg-red-500/85'}`}
                      title={[es.animeTitle || es.titleZh, es.platformWatchStatus === 'watching' ? t('calendar.platformWatching') : (es.platformWatchStatus === 'wish' ? t('calendar.platformWishlist') : '')].filter(Boolean).join(' · ')}>
                  {es.origin === 'bangumi' ? 'BGM' : 'Trakt'}
                  {es.platformWatchStatus === 'watching' ? '⭐' : (es.platformWatchStatus === 'wish' ? '📌' : '')}
                </span>
              ))}
            </div>
          )}
          {/* 移动端：左下角倒计时徽章 */}
          {countdown && (
            <span className={`absolute bottom-9 left-1 text-[9px] font-extrabold px-1.5 py-0.5 rounded-md backdrop-blur-sm ${countdown.isNow ? 'bg-emerald-500/85 text-white' : 'bg-indigo-500/85 text-white'}`}>
              {countdown.isNow ? countdown.text : `${countdown.text}${countdown.unit}`}
            </span>
          )}
          {/* 来源角标 */}
          {!item.isLocal && item.origin === 'bangumi' && <span className="absolute top-1 right-1 text-[9px] font-bold px-1.5 py-0.5 rounded-md bg-pink-500/85 text-white">BGM</span>}
          {!item.isLocal && item.origin === 'trakt' && <span className="absolute top-1 right-1 text-[9px] font-bold px-1.5 py-0.5 rounded-md bg-red-500/85 text-white">Trakt</span>}
          {/* 平台「我在追/想看」徽章（OAuth 账号下的私人状态，与本地订阅独立）
              位置：来源角标下方一点，避开右上角选中勾 */}
          {!item.isLocal && (item.platformWatchStatus === 'watching' || item.platformWatchStatus === 'wish') && (
            <span className={`absolute top-7 right-1 text-[9px] font-bold px-1.5 py-0.5 rounded-md ${item.platformWatchStatus === 'watching' ? 'bg-amber-500/90 text-white' : 'bg-sky-500/85 text-white'} flex items-center gap-0.5`}
                  title={item.platformWatchStatus === 'watching' ? t('calendar.platformWatching') : t('calendar.platformWishlist')}>
              {item.platformWatchStatus === 'watching' ? '⭐' : '📌'}
              {item.platformWatchedEpisodes ? ` EP${String(item.platformWatchedEpisodes).padStart(2, '0')}` : ''}
            </span>
          )}
          {/* 选中勾 */}
          {selected && <div className="absolute top-1 right-1 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10" style={{ backgroundColor: 'var(--color-primary)' }}><span className="text-white text-[10px] font-bold">✓</span></div>}
          {/* 底部悬浮：左侧进度条+集数（自适应），右下角订阅/已订阅 */}
          <div className="absolute bottom-0 inset-x-0 px-1.5 pb-1.5 pt-4 bg-gradient-to-t from-black/70 to-transparent flex items-center gap-1.5">
            {(() => {
              const cur = item.latestEpisodeIndex ?? 0
              const total = item.episodeCount || null
              const pct = total ? Math.min(100, Math.round((cur / total) * 100)) : 8
              if (item.latestEpisodeIndex == null && !total) return <div className="flex-1" />
              return (
                <div className="flex-1 min-w-0 flex items-center gap-1">
                  <div className="flex-1 h-1 rounded-full bg-white/25 overflow-hidden">
                    <div className="h-full rounded-full bg-indigo-400" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-[8px] font-semibold text-white/90 whitespace-nowrap">{cur}/{total ?? '∞'}</span>
                </div>
              )
            })()}
            {/* 本地订阅状态：isLocal（已在追更）或 isSubscribed（外部条目对应本地有匹配）→ 都视为「已订阅」 */}
            {(item.isLocal || item.isSubscribed)
              ? <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/90 text-white whitespace-nowrap cursor-pointer hover:bg-red-500/90 transition"
                  onClick={(e) => { e.stopPropagation(); onUnsubscribe(item) }}
                  title={t('calendar.unsubscribeAction')}
                >{t('calendar.subscribed')}</span>
              : <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/90 text-white cursor-pointer hover:bg-indigo-500 transition whitespace-nowrap"
                  onClick={(e) => { e.stopPropagation(); onSubscribe(item) }} title={t('calendar.subscribeAction')}>
                  {isSubscribing ? '⏳' : '➕'} {t('calendar.subscribeAction')}
                </span>}
          </div>
        </div>
        {/* 底部番名 + 小标签 */}
        <div className="p-1.5">
          <Tooltip title={displayTitle} placement="topLeft"><div className="font-bold text-[11px] leading-tight line-clamp-2 h-[28px]">{displayTitle}</div></Tooltip>
          {item.isLocal && item.externalTitles?.length > 0 && (
            <Tooltip title={item.externalTitles.join(' / ')} placement="topLeft">
              <div className="text-[10px] text-gray-400 truncate mt-0.5">↔ {item.externalTitles.slice(0, 2).join(' / ')}</div>
            </Tooltip>
          )}

          <div className="flex items-center gap-1 mt-1 flex-wrap">
            {displayYear && <span className="text-[9px] font-medium text-gray-500 dark:text-gray-400">{displayYear}</span>}
            {!item.isLocal && item.season && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
            {item.isLocal && item.animeType !== 'movie' && item.season && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
            {item.airTime && <span className="text-[9px] text-gray-500 dark:text-gray-400">🕐 {item.airTime}</span>}
          </div>
        </div>
      </div>
    )
  }

  // 横版小卡（unscheduled PC 端使用）
  return (
    <div
      className={`flex gap-2.5 p-2 rounded-xl transition border relative ${baseStyle} ${isExternal ? 'cursor-pointer hover:border-indigo-400/50 dark:hover:border-indigo-500/40' : 'cursor-default'}`}
      style={selected ? { ...selectedStyle, '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)' } : undefined}
      onClick={isExternal ? () => onToggleSelect(item) : undefined}
    >
      {selected && (
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10"
          style={{ backgroundColor: 'var(--color-primary)' }}>
          <span className="text-white text-[10px] font-bold">✓</span>
        </div>
      )}
      {posterSrc ? <img src={posterSrc} loading="lazy" alt="" onError={e => { e.currentTarget.style.visibility = 'hidden' }} className="w-10 h-14 rounded-lg object-cover flex-shrink-0 bg-gray-200 dark:bg-white/6" /> : <div className="w-10 h-14 rounded-lg bg-gray-200 dark:bg-white/6 flex-shrink-0" />}
      <div className="min-w-0 flex-1">
        <Tooltip title={item.animeTitle} placement="topLeft"><div className="font-bold text-xs truncate">{item.animeTitle}</div></Tooltip>
        {item.isLocal && item.externalTitles?.length > 0 && (
          <Tooltip title={item.externalTitles.join(' / ')} placement="topLeft">
            <div className="text-[10px] text-gray-400 truncate mt-0.5">↔ {item.externalTitles.slice(0, 2).join(' / ')}</div>
          </Tooltip>
        )}

        <div className="flex gap-1 mt-1 flex-wrap">
          {item.origin === 'bangumi' && !item.isLocal && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-pink-500/15 text-pink-400">BGM</span>}
          {item.origin === 'trakt' && !item.isLocal && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-red-500/15 text-red-400">Trakt</span>}
          {item.isLocal && item.bangumiId && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-pink-500/10 text-pink-400">BGM</span>}
          {item.isLocal && item.traktId && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">Trakt</span>}
          {item.isLocal && item.externalSources?.map((es, i) => (
            <span key={`${es.origin}-${i}`} className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${es.origin === 'bangumi' ? 'bg-pink-500/10 text-pink-400' : 'bg-red-500/10 text-red-400'}`} title={es.animeTitle || es.titleZh}>
              {es.origin === 'bangumi' ? 'BGM' : 'Trakt'}
            </span>
          ))}

          {/* 平台「我在追/想看」徽章（OAuth 账号下的私人状态，与本地订阅独立） */}
          {!item.isLocal && item.platformWatchStatus === 'watching' && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500" title={t('calendar.platformWatching')}>
              ⭐{item.platformWatchedEpisodes ? ` EP${String(item.platformWatchedEpisodes).padStart(2, '0')}` : ''}
            </span>
          )}
          {!item.isLocal && item.platformWatchStatus === 'wish' && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-500" title={t('calendar.platformWishlist')}>📌</span>
          )}
          {(item.isLocal || item.isSubscribed)
            ? <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 cursor-pointer hover:text-red-400 hover:bg-red-500/10 transition"
                onClick={(e) => { e.stopPropagation(); onUnsubscribe(item) }}
                title={t('calendar.unsubscribeAction')}
              >{t('calendar.subscribed')}</span>
            : <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 cursor-pointer hover:bg-indigo-500/20 transition"
                onClick={(e) => { e.stopPropagation(); onSubscribe(item) }} title={t('calendar.subscribeAction')}>
                {isSubscribing ? '⏳' : '➕'} {t('calendar.subscribeAction')}
              </span>
          }
          {item.isLocal && item.animeType !== 'movie' && item.season && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
          {item.latestEpisodeIndex != null && <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">EP{String(item.latestEpisodeIndex).padStart(2, '0')}</span>}
          {item.providerName && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-gray-500/8 text-gray-500 dark:text-gray-400">{item.providerName}</span>}
          {item.rating && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-yellow-500/10 text-yellow-500">★{item.rating}</span>}
        </div>
        {item.airTime && <div className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">🕐 {item.airTime}</div>}
      </div>
    </div>
  )
})

const CalendarView = ({ data, loading, isMobile, t, filter = 'local', onFilterChange, syncing, onSync, onClearCache, selectedExtItems, setSelectedExtItems, setCalendarData }) => {
  const todayWeekday = new Date().getDay() === 0 ? 7 : new Date().getDay()
  const navigate = useNavigate()
  const [searchKeyword, setSearchKeyword] = useState('')
  // 订阅确认 Modal 状态
  const [subscribeModalOpen, setSubscribeModalOpen] = useState(false)
  const [subscribingItem, setSubscribingItem] = useState(null)
  const [batchSubscribeModalOpen, setBatchSubscribeModalOpen] = useState(false)
  const [runNowChecked, setRunNowChecked] = useState(true)
  const [localizedTitles, setLocalizedTitles] = useState({}) // Trakt 中文标题/年份缓存 {tmdbId: {title, year}}
  // 7 天的横向滚动容器 ref（按 day 索引管理，避免在 renderDayRow 中用 useRef 触发组件类型问题）
  const dayScrollRefs = useRef({}) // { 1: HTMLDivElement, 2: ..., 7: ... }
  // 7 天的「内容是否可滑动」状态（决定是否显示 ‹ › 按钮）
  const [dayCanScroll, setDayCanScroll] = useState({}) // { 1: true, 2: false, ... }

  // 对 Trakt 条目按需拉取 TMDB 中文标题与年份（覆盖英文原标题）
  // 性能优化：并发池（8 路）+ localStorage 持久缓存，避免 350+ 个串行请求拖慢首屏
  useEffect(() => {
    if (!data?.weekly) return
    const ids = new Set()
    for (let d = 1; d <= 7; d++) {
      (data.weekly[d] || []).forEach(i => {
        if (i.origin === 'trakt' && i.traktTmdbId && localizedTitles[i.traktTmdbId] === undefined) ids.add(i.traktTmdbId)
      })
    }
    if (ids.size === 0) return
    let cancelled = false
    // 先尝试从 localStorage 读取已缓存的标题（24h 有效）
    const LS_KEY = 'calendar_tmdb_titles_v1'
    const LS_TTL = 24 * 3600 * 1000
    let lsCache = {}
    try {
      const raw = localStorage.getItem(LS_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (parsed && parsed.ts && Date.now() - parsed.ts < LS_TTL) lsCache = parsed.data || {}
      }
    } catch {}
    // 已在 localStorage 中的直接命中
    const idsToFetch = []
    const fromCache = {}
    for (const id of ids) {
      if (lsCache[id]) fromCache[id] = lsCache[id]
      else idsToFetch.push(id)
    }
    if (Object.keys(fromCache).length > 0) {
      setLocalizedTitles(prev => ({ ...prev, ...fromCache }))
    }
    if (idsToFetch.length === 0) return
    // 并发池：同时跑 8 个请求，比串行快 ~8 倍
    ;(async () => {
      const POOL = 8
      const results = {}
      let cursor = 0
      const worker = async () => {
        while (cursor < idsToFetch.length && !cancelled) {
          const id = idsToFetch[cursor++]
          try {
            const resp = await fetch(`/api/ui/calendar/tmdb-title/${id}`)
            const json = await resp.json()
            results[id] = json || null
          } catch {
            results[id] = null
          }
        }
      }
      await Promise.all(Array.from({ length: POOL }, () => worker()))
      if (cancelled) return
      setLocalizedTitles(prev => ({ ...prev, ...results }))
      // 持久化到 localStorage
      try {
        const merged = { ...lsCache, ...results }
        localStorage.setItem(LS_KEY, JSON.stringify({ ts: Date.now(), data: merged }))
      } catch {}
    })()
    return () => { cancelled = true }
  }, [data])

  // 监听 7 天容器尺寸变化，按需更新 dayCanScroll（控制 ‹ › 按钮显隐）
  // 关键：依赖 [data, filter, searchKeyword]，items 变化时重建 ResizeObserver
  useEffect(() => {
    const observers = []
    const update = (day, el) => {
      if (!el) return
      const can = el.scrollWidth > el.clientWidth + 1
      setDayCanScroll(prev => prev[day] === can ? prev : { ...prev, [day]: can })
    }
    for (let d = 1; d <= 7; d++) {
      const el = dayScrollRefs.current[d]
      if (!el) continue
      update(d, el)
      const ro = new ResizeObserver(() => update(d, el))
      ro.observe(el)
      observers.push(ro)
    }
    return () => observers.forEach(ro => ro.disconnect())
  }, [data, filter, searchKeyword])

  // 取展示用标题/年份（Trakt 优先用 TMDB 中文标题）
  const getDisplayTitle = (item) => {
    if (item.origin === 'trakt' && item.traktTmdbId) {
      const loc = localizedTitles[item.traktTmdbId]
      if (loc?.title) return loc.title
    }
    return item.animeTitle
  }
  const getDisplayYear = (item) => {
    if (item.origin === 'trakt' && item.traktTmdbId) {
      const loc = localizedTitles[item.traktTmdbId]
      if (loc?.year) return loc.year
    }
    return item.year || null
  }

  const getCalPoster = (item) => {
    let src = item.localImagePath || item.imageUrl
    if (src?.startsWith('/images/')) src = src.replace('/images/', '/data/images/')
    // Trakt 番无现成海报时，走按需懒加载端点（浏览器仅加载视口内图片，单个失败不影响整体）
    if (!src && item.traktTmdbId) {
      src = `/api/ui/calendar/tmdb-poster/${item.traktTmdbId}`
    }
    return src
  }

  // 按 filter 过滤数据
  const filterItems = (items) => {
    if (!items) return []
    let filtered = items
    if (filter === 'local') filtered = items.filter(i => i.isLocal)
    else if (filter === 'bangumi') filtered = items.filter(i => i.origin === 'bangumi' || (i.isLocal && i.bangumiId))
    else if (filter === 'trakt') filtered = items.filter(i => i.origin === 'trakt' || (i.isLocal && i.traktId))
    // 搜索关键词过滤
    if (searchKeyword.trim()) {
      const kw = searchKeyword.trim().toLowerCase()
      filtered = filtered.filter(i => {
        const titles = [
          i.animeTitle,
          i.titleZh,
          ...(i.externalTitles || []),
          ...((i.externalSources || []).flatMap(s => [s.animeTitle, s.titleZh])),
        ]
        return titles.some(title => (title || '').toLowerCase().includes(kw))
      })
    }
    return filtered
  }

  // 批量选择辅助（getItemKey 已提到组件外作为纯函数）
  const isSelected = (item) => selectedExtItems.some(s => getItemKey(s) === getItemKey(item))
  const toggleSelect = (item) => {
    const key = getItemKey(item)
    if (selectedExtItems.some(s => getItemKey(s) === key)) {
      setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== key))
    } else {
      setSelectedExtItems(prev => [...prev, item])
    }
  }
  const getFilteredExternalItems = () => {
    const allExt = []
    for (let day = 1; day <= 7; day++) {
      filterItems(data.weekly[day]).filter(i => !i.isLocal).forEach(i => {
        if (!allExt.some(e => getItemKey(e) === getItemKey(i))) allExt.push(i)
      })
    }
    return allExt
  }
  const filteredExternalItems = getFilteredExternalItems()
  const allFilteredExternalSelected = filteredExternalItems.length > 0
    && filteredExternalItems.every(i => selectedExtItems.some(s => getItemKey(s) === getItemKey(i)))
  // 全选/取消全选当前过滤后的外部番
  const toggleSelectAllExternal = () => {
    if (allFilteredExternalSelected) {
      const currentKeys = new Set(filteredExternalItems.map(i => getItemKey(i)))
      setSelectedExtItems(prev => prev.filter(s => !currentKeys.has(getItemKey(s))))
      return
    }
    setSelectedExtItems(prev => {
      const existing = new Set(prev.map(s => getItemKey(s)))
      const toAdd = filteredExternalItems.filter(i => !existing.has(getItemKey(i)))
      return [...prev, ...toAdd]
    })
  }

  // 订阅外部番（自动搜索并导入）
  const [subscribingKeys, setSubscribingKeys] = useState([])
  // 显示订阅确认 Modal
  const handleSubscribe = (item) => {
    setSubscribingItem(item)
    setRunNowChecked(true)
    setSubscribeModalOpen(true)
  }

  const patchCalendarItem = (target, patch) => {
    const isSame = (i) => getItemKey(i) === getItemKey(target)
      || (target.bangumiId && i.bangumiId && String(i.bangumiId) === String(target.bangumiId))
      || (target.traktId && i.traktId && String(i.traktId) === String(target.traktId))
      || (target.traktTmdbId && i.traktTmdbId && String(i.traktTmdbId) === String(target.traktTmdbId))
    setCalendarData(prev => {
      const patchList = (items = []) => items.map(i => isSame(i) ? { ...i, ...patch } : i)
      const weekly = {}
      for (const [day, items] of Object.entries(prev.weekly || {})) weekly[day] = patchList(items)
      return { ...prev, weekly, unscheduled: patchList(prev.unscheduled || []) }
    })
  }

  const patchCalendarItems = (targets, patch) => {
    targets.forEach(target => patchCalendarItem(target, patch))
  }

  // 确认订阅
  const handleConfirmSubscribe = async () => {
    if (!subscribingItem) return
    const item = subscribingItem
    const key = getItemKey(item)

    if (subscribingKeys.includes(key)) return
    setSubscribingKeys(prev => [...prev, key])
    setSubscribeModalOpen(false)
    setSubscribingItem(null)

    try {
      await subscribeCalendarItem({
        animeTitle: item.animeTitle,
        mediaType: item.animeType === 'movie' ? 'movie' : 'tv_series',
        season: item.season || null,
        traktTmdbId: item.traktTmdbId ? String(item.traktTmdbId) : null,
        traktId: item.traktId ? String(item.traktId) : null,
        bangumiId: item.bangumiId ? String(item.bangumiId) : null,
        provider: item.provider || item.origin || null,
        externalId: item.externalId || item.bangumiId || item.traktId || item.traktTmdbId || null,
        runNow: runNowChecked,
      })
      patchCalendarItem(item, { isSubscribed: true, subscriptionStatus: runNowChecked ? 'importing' : 'pending' })
      setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== getItemKey(item)))
      message.success(t('calendar.subscribeSubmitted', { title: item.animeTitle }))
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.subscribeFailed'))
    } finally {
      setSubscribingKeys(prev => prev.filter(k => k !== key))
    }
  }

  // 取消订阅/取消追更（统一调一次接口，后端内部处理本地+外部）
  const handleUnsubscribe = async (item) => {
    try {
      const externalTarget = item.isLocal && item.externalSources?.length > 0 ? item.externalSources[0] : null
      const payload = {
        provider: externalTarget?.provider || externalTarget?.origin || item.provider || item.origin || null,
        externalId: externalTarget?.externalId || item.externalId || null,
        bangumiId: (externalTarget?.bangumiId || item.bangumiId) ? String(externalTarget?.bangumiId || item.bangumiId) : null,
        traktId: (externalTarget?.traktId || item.traktId) ? String(externalTarget?.traktId || item.traktId) : null,
        traktTmdbId: (externalTarget?.tmdbId || item.traktTmdbId) ? String(externalTarget?.tmdbId || item.traktTmdbId) : null,
      }
      if (item.isLocal && item.sourceId && !externalTarget) {
        payload.sourceId = item.sourceId
      }
      if (!payload.externalId) {
        if (payload.provider === 'trakt') payload.externalId = payload.traktId || payload.traktTmdbId
        else if (payload.provider === 'bangumi') payload.externalId = payload.bangumiId
      }
      if (!payload.sourceId && !payload.provider && !payload.bangumiId && !payload.traktId && !payload.traktTmdbId) {
        message.warning(t('calendar.unsubscribeFailed'))
        return
      }

      await unsubscribeCalendarItem(payload)

      if (item.isLocal && !externalTarget) {
        setCalendarData(prev => {
          const newWeekly = {}
          for (const [day, items] of Object.entries(prev.weekly || {})) {
            newWeekly[day] = items.filter(i => i.sourceId !== item.sourceId)
          }
          const newUnscheduled = (prev.unscheduled || []).filter(i => i.sourceId !== item.sourceId)
          return { ...prev, weekly: newWeekly, unscheduled: newUnscheduled }
        })
      } else {
        patchCalendarItem(item, { isSubscribed: false, subscriptionStatus: null })
        setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== getItemKey(item)))
      }

      message.success(t('calendar.unsubscribeSuccess', { title: item.animeTitle }))
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.unsubscribeFailed'))
    }
  }

  const handleBatchSubscribe = () => {
    if (selectedExtItems.length === 0) return
    setRunNowChecked(true)
    setBatchSubscribeModalOpen(true)
  }

  const handleConfirmBatchSubscribe = async () => {
    const list = [...selectedExtItems]
    if (list.length === 0) return
    try {
      const res = await batchSubscribeCalendarItems({
        runNow: runNowChecked,
        items: list.map(it => ({
          animeTitle: it.animeTitle,
          mediaType: it.animeType === 'movie' ? 'movie' : 'tv_series',
          season: it.season || null,
          traktTmdbId: it.traktTmdbId ? String(it.traktTmdbId) : null,
          traktId: it.traktId ? String(it.traktId) : null,
          bangumiId: it.bangumiId ? String(it.bangumiId) : null,
          provider: it.origin || it.provider || null,
          externalId: it.externalId || it.bangumiId || it.traktId || it.traktTmdbId || null,
          runNow: runNowChecked,
        })),
      })
      const ok = res?.data?.successCount ?? list.length
      message.success(t('calendar.batchSubscribeDone', { count: ok }))
      patchCalendarItems(list, { isSubscribed: true, subscriptionStatus: runNowChecked ? 'importing' : 'pending' })
      setSelectedExtItems([])
      setBatchSubscribeModalOpen(false)
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.subscribeFailed'))
    }
  }

  if (loading) return <div className="flex items-center justify-center h-[40vh]"><Spin size="large" /></div>
  if (!data.stats?.total && !data.stats?.local) return <Empty className="py-16" description={t('calendar.noData')} />

  // CalCard 已提到组件外（顶层 + React.memo）。这里仅作辅助渲染：传入预计算好的 props
  const renderCalCard = (item, idx, { isToday = false, horizontal = false, day = null, keyPrefix = '' } = {}) => (
    <CalCard
      key={item.sourceId || `${keyPrefix}${item.origin}-${item.bangumiId || item.traktId}-${idx}`}
      item={item}
      isToday={isToday}
      horizontal={horizontal}
      day={day}
      isMobile={isMobile}
      selected={isSelected(item)}
      t={t}
      posterSrc={getCalPoster(item)}
      displayTitle={getDisplayTitle(item)}
      displayYear={getDisplayYear(item)}
      countdown={isMobile && day ? getCountdown(day) : null}
      onToggleSelect={toggleSelect}
      onSubscribe={handleSubscribe}
      onUnsubscribe={handleUnsubscribe}
      isSubscribing={subscribingKeys.includes(getItemKey(item))}
    />
  )

  const orderedDays = Array.from({ length: 7 }, (_, i) => ((todayWeekday - 1 + i) % 7) + 1)

  // 渲染一天的横向滚动行（普通函数 ✅ 不是 React 组件，所以不会因 CalendarView 重渲染产生新组件类型，
  // 内部 <CalCard>（顶层 + memo）的 DOM 节点会被 React 复用，<img> 不会重新挂载，海报也就不会重发请求）
  const renderDayRow = (day) => {
    const items = filterItems(data.weekly[day])
    const isToday = day === todayWeekday
    const canScroll = !!dayCanScroll[day]
    const scrollBy = (dir) => dayScrollRefs.current[day]?.scrollBy({ left: dir * 320, behavior: 'smooth' })
    // 全选/取消全选当天的外部番（filter ≠ local 才有意义）
    const externalItems = items.filter(i => !i.isLocal)
    const allDaySelected = externalItems.length > 0 && externalItems.every(i => isSelected(i))
    const toggleSelectDay = () => {
      if (allDaySelected) {
        const dayKeys = new Set(externalItems.map(i => getItemKey(i)))
        setSelectedExtItems(prev => prev.filter(s => !dayKeys.has(getItemKey(s))))
      } else {
        setSelectedExtItems(prev => {
          const existing = new Set(prev.map(s => getItemKey(s)))
          const toAdd = externalItems.filter(i => !existing.has(getItemKey(i)))
          return [...prev, ...toAdd]
        })
      }
    }
    return (
      <div key={day} className={`group rounded-2xl border overflow-hidden ${isToday ? 'border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/[0.03]' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/[0.02]'}`}>
        <div className={`px-3 py-2 border-b ${isToday ? 'border-indigo-200 dark:border-indigo-500/20' : 'border-gray-100 dark:border-white/4'} flex items-center gap-1.5`}>
          <span className={`text-xs font-bold ${isToday ? 'text-indigo-500 dark:text-indigo-400' : 'text-gray-600 dark:text-gray-400'}`}>{t(DAYS_KEYS[day - 1])}</span>
          {isToday && <span className="text-[8px] bg-indigo-500 text-white px-1.5 py-0.5 rounded-full font-bold">TODAY</span>}
          <span className="text-[10px] text-gray-400 bg-gray-100 dark:bg-white/4 px-1.5 py-0.5 rounded-md">{items.length}</span>
          {/* 当天全选按钮：filter ≠ local 且有外部番时显示。移动端 ml-auto 推到右侧 */}
          {filter !== 'local' && externalItems.length > 0 && (
            <button onClick={toggleSelectDay}
              className={`${isMobile ? 'ml-auto' : ''} text-[10px] font-medium px-2 py-0.5 rounded-md border transition ${allDaySelected ? 'bg-indigo-500/15 text-indigo-400 border-indigo-500/40' : 'border-gray-300 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:border-indigo-400/40'}`}>
              {allDaySelected ? `☑ ${t('calendar.deselectDay')}` : `☐ ${t('calendar.selectDay')}`}
            </button>
          )}
          {/* 右侧滑动按钮（PC 端 + 内容可滑动时常驻显示；不可滑动隐藏） */}
          {!isMobile && canScroll && (
            <div className="ml-auto flex items-center gap-1">
              <button onClick={() => scrollBy(-1)} className="w-6 h-6 rounded-md flex items-center justify-center bg-white dark:bg-white/8 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/12 text-base leading-none">‹</button>
              <button onClick={() => scrollBy(1)} className="w-6 h-6 rounded-md flex items-center justify-center bg-white dark:bg-white/8 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/12 text-base leading-none">›</button>
            </div>
          )}
        </div>
        {items.length === 0
          ? <div className="flex items-center justify-center h-20 text-gray-400 text-xs">{t('calendar.noUpdate')}</div>
          : <div className="relative">
              <div ref={(el) => { dayScrollRefs.current[day] = el }} className="flex gap-2.5 p-2.5 overflow-x-auto scrollbar-thin">
                {items.map((item, idx) => renderCalCard(item, idx, { isToday, horizontal: true, day }))}
              </div>
            </div>}
      </div>
    )
  }

  // 倒计时计算
  const getCountdown = (day) => {
    const diff = day >= todayWeekday ? day - todayWeekday : 7 - todayWeekday + day
    if (diff === 0) return { text: t('calendar.justNow'), isNow: true }
    return { text: String(diff), unit: t('calendar.dayUnit'), isNow: false }
  }

  const filterBtnClass = (v) =>
    `px-3 py-1.5 text-xs font-medium transition ${filter === v ? 'bg-indigo-500 text-white' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/4'}`

  return (
    <div className="space-y-4">
      {/* 过滤器 + 统计 + 搜索 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-3 flex-wrap items-center">
          <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('calendar.statLocal')}</span> <strong className="text-indigo-400 ml-1">{data.stats.local || 0}</strong>
          </div>
          {(data.stats.bangumi > 0) && (
            <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
              <span className="text-gray-500 dark:text-gray-400">BGM</span> <strong className="text-pink-400 ml-1">{data.stats.bangumi}</strong>
            </div>
          )}
          {(data.stats.trakt > 0) && (
            <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
              <span className="text-gray-500 dark:text-gray-400">Trakt</span> <strong className="text-red-400 ml-1">{data.stats.trakt}</strong>
            </div>
          )}
          {/* 搜索框 */}
          <Popover content={<div style={{ width: 200 }}><Input placeholder={t('calendar.searchPlaceholder')} allowClear value={searchKeyword} onChange={e => setSearchKeyword(e.target.value)} autoFocus /></div>} trigger="click" placement="bottom">
            <button className={`px-3 py-1.5 rounded-xl text-xs font-medium border transition flex items-center gap-1 ${searchKeyword ? 'bg-indigo-500/8 text-indigo-400 border-indigo-500/30' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
              🔍 {searchKeyword || t('calendar.search')}
            </button>
          </Popover>
        </div>
        <div className="flex items-center gap-2">
          {filter !== 'local' && filteredExternalItems.length > 0 && (
            <button onClick={toggleSelectAllExternal} className={`px-3 py-1.5 rounded-xl text-xs font-medium border transition ${allFilteredExternalSelected ? 'bg-indigo-500/15 text-indigo-400 border-indigo-500/40' : 'border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}`}>
              {allFilteredExternalSelected ? `☑ ${t('calendar.deselectAll')}` : `☐ ${t('calendar.selectAll')}`}
            </button>
          )}
          {/* 同步日程按钮 */}
          <button onClick={onSync} disabled={syncing}
            className="px-3 py-1.5 rounded-xl text-xs font-semibold border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/8 transition flex items-center gap-1.5 disabled:opacity-50">
            <SyncOutlined spin={syncing} /> {t('calendar.syncSchedule')}
          </button>
          {/* 清除缓存按钮 */}
          <button onClick={onClearCache}
            className="px-3 py-1.5 rounded-xl text-xs font-semibold border border-orange-500/30 text-orange-400 hover:bg-orange-500/8 transition flex items-center gap-1.5">
            <DeleteOutlined /> {t('calendar.clearCache')}
          </button>
          {/* 过滤器组：PC 端跟在操作组后面；移动端独立成一行（见下方） */}
          {!isMobile && (
            <div className="flex rounded-xl border border-gray-200 dark:border-white/6 overflow-hidden">
              <button onClick={() => onFilterChange('local')} className={filterBtnClass('local')}>{t('calendar.filterLocal')}</button>
              <button onClick={() => onFilterChange('all')} className={filterBtnClass('all')}>{t('calendar.filterAll')}</button>
              {(data.stats.bangumi > 0) && <button onClick={() => onFilterChange('bangumi')} className={filterBtnClass('bangumi')}>BGM</button>}
              {(data.stats.trakt > 0) && <button onClick={() => onFilterChange('trakt')} className={filterBtnClass('trakt')}>Trakt</button>}
            </div>
          )}
        </div>
      </div>

      {/* 移动端：过滤器组独占一行，按钮可放大 */}
      {isMobile && (
        <div className="flex rounded-xl border border-gray-200 dark:border-white/6 overflow-hidden">
          <button onClick={() => onFilterChange('local')} className={`flex-1 ${filterBtnClass('local')}`}>{t('calendar.filterLocal')}</button>
          <button onClick={() => onFilterChange('all')} className={`flex-1 ${filterBtnClass('all')}`}>{t('calendar.filterAll')}</button>
          {(data.stats.bangumi > 0) && <button onClick={() => onFilterChange('bangumi')} className={`flex-1 ${filterBtnClass('bangumi')}`}>BGM</button>}
          {(data.stats.trakt > 0) && <button onClick={() => onFilterChange('trakt')} className={`flex-1 ${filterBtnClass('trakt')}`}>Trakt</button>}
        </div>
      )}


      {/* 已选操作条 - 贴底悬浮工具栏（PC 端居中、移动端右下角） */}
      {selectedExtItems.length > 0 && (
        <div className={`fixed ${isMobile ? 'right-4 bottom-[calc(6rem+env(safe-area-inset-bottom))] z-[1000]' : 'left-1/2 -translate-x-1/2 bottom-3 z-40'} flex items-center gap-2 px-3 py-2 rounded-2xl shadow-xl border border-indigo-500/30 bg-white/95 dark:bg-[#1a1e2e]/95 backdrop-blur supports-[backdrop-filter]:bg-white/80 dark:supports-[backdrop-filter]:bg-[#1a1e2e]/80 max-w-[calc(100vw-1.5rem)]`}>
          <span className="text-xs font-semibold text-indigo-500 dark:text-indigo-400 whitespace-nowrap">{t('calendar.selected', { count: selectedExtItems.length })}</span>
          <button onClick={() => setSelectedExtItems([])} className="text-xs text-gray-400 hover:text-gray-600 transition whitespace-nowrap">{t('calendar.clearSelection')}</button>
          <button onClick={handleBatchSubscribe} className="px-3 py-1.5 rounded-xl text-xs font-semibold bg-indigo-500 text-white hover:bg-indigo-600 transition whitespace-nowrap shadow-sm">
            ➕ {t('calendar.batchSubscribe')}
          </button>
        </div>
      )}

      {/* Weekly grid - 统一使用 renderDayRow（PC + 移动端横滑布局）。
          renderDayRow 是普通函数（非组件）→ React 看到的是稳定的 div 结构，不会重建子树 → <img> 不会重新挂载 */}
      {/* 底部 padding：避免悬浮工具栏遮挡最后一个 DayRow（PC 端底部居中，移动端右下角，都需要预留） */}
      <div className={`space-y-3 ${selectedExtItems.length > 0 ? (isMobile ? 'pb-36' : 'pb-20') : ''}`}>
        {orderedDays.map(day => renderDayRow(day))}
      </div>

      {/* Unscheduled (only in local filter) */}

      {filter === 'local' && data.unscheduled?.length > 0 && (
        <div className="rounded-2xl border border-gray-200 dark:border-white/6 bg-gray-50 dark:bg-white/[0.02] p-4">
          <div className="text-sm font-bold text-gray-600 dark:text-gray-400 mb-3 flex items-center gap-2">📂 {t('calendar.unscheduledTitle')} ({data.unscheduled.length})</div>
          <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">{t('calendar.unscheduledDesc')}</p>
          <div className={isMobile ? 'flex gap-2.5 overflow-x-auto scrollbar-thin pb-1' : 'grid grid-cols-3 lg:grid-cols-4 gap-2'}>
            {data.unscheduled.map((item, idx) => (
              renderCalCard(item, idx, { isToday: false, horizontal: !!isMobile, keyPrefix: 'unsched-' })
            ))}
          </div>
        </div>
      )}

      {/* 订阅确认 Modal */}
      <Modal
        title={t('calendar.subscribeConfirm')}
        open={subscribeModalOpen}
        onOk={handleConfirmSubscribe}
        onCancel={() => {
          setSubscribeModalOpen(false)
          setSubscribingItem(null)
        }}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        width={400}
      >
        {subscribingItem && (
          <div className="space-y-3">
            <div className="text-sm">
              <div className="font-medium mb-1">{subscribingItem.animeTitle}</div>
              <div className="text-gray-500 dark:text-gray-400 text-xs space-y-0.5">
                <div>{t('calendar.type')}: {subscribingItem.animeType === 'movie' ? t('calendar.movie') : t('calendar.tvSeries')}</div>
                {subscribingItem.season && <div>{t('calendar.season')}: {subscribingItem.season}</div>}
              </div>
            </div>
            <div className="pt-2 border-t border-gray-200 dark:border-white/10">
              <Checkbox
                checked={runNowChecked}
                onChange={(e) => setRunNowChecked(e.target.checked)}
              >
                <span className="text-sm">{t('calendar.runNowLabel')}</span>
              </Checkbox>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1 ml-6">
                {t('calendar.runNowDesc')}
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* 批量订阅确认 Modal */}
      <Modal
        title={t('calendar.batchSubscribeConfirm')}
        open={batchSubscribeModalOpen}
        onOk={handleConfirmBatchSubscribe}
        onCancel={() => setBatchSubscribeModalOpen(false)}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        width={420}
      >
        <div className="space-y-3">
          <div className="text-sm">
            {t('calendar.batchSubscribeConfirmDesc', { count: selectedExtItems.length })}
          </div>
          {selectedExtItems.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-xl bg-gray-50 dark:bg-white/[0.03] p-2 space-y-1">
              {selectedExtItems.slice(0, 8).map((item, idx) => (
                <div key={getItemKey(item) || idx} className="flex items-center justify-between gap-2 text-xs px-2 py-1 rounded-lg bg-white dark:bg-white/[0.03]">
                  <span className="truncate font-medium text-gray-600 dark:text-gray-300">{item.animeTitle}</span>
                  <span className="shrink-0 text-[10px] text-gray-400">{item.origin === 'trakt' ? 'Trakt' : 'BGM'}</span>
                </div>
              ))}
              {selectedExtItems.length > 8 && (
                <div className="text-xs text-gray-400 px-2 py-1">+{selectedExtItems.length - 8}</div>
              )}
            </div>
          )}

          <div className="pt-2 border-t border-gray-200 dark:border-white/10">
            <Checkbox
              checked={runNowChecked}
              onChange={(e) => setRunNowChecked(e.target.checked)}
            >
              <span className="text-sm">{t('calendar.runNowLabel')}</span>
            </Checkbox>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-1 ml-6">
              {t('calendar.runNowDesc')}
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}

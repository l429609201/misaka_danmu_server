import { useEffect, useState, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Input, Button, Card, Checkbox, Spin, Empty, message, Dropdown, Pagination, Popover, Modal, Tooltip } from 'antd'
import { DownOutlined } from '@ant-design/icons'
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
} from '../../apis'
import dayjs from 'dayjs'
import { useDefaultPageSize } from '../../hooks/useDefaultPageSize'



export const BatchManagePage = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const defaultPageSize = useDefaultPageSize('refreshModal')

  // ---- State ----
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
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1.5">{t('batchManage.pageDesc')}</p>
      </div>


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





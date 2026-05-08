import React, { useEffect, useState, useCallback } from 'react'
import { Modal, Drawer, Input, Switch, Button, Checkbox, Collapse, Tag, Spin, Empty, Space, message, Alert, Dropdown, Pagination, Popover } from 'antd'
import { SyncOutlined, ClockCircleOutlined, WarningOutlined, CheckCircleOutlined, CloseCircleOutlined, DownOutlined, SearchOutlined, DeleteOutlined } from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store/index.js'
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
} from '../apis'
import dayjs from 'dayjs'
import { useDefaultPageSize } from '../hooks/useDefaultPageSize'
import { MyIcon } from '@/components/MyIcon'

/**
 * 追更与标记管理弹窗组件
 */
export const IncrementalRefreshModal = ({ open, onCancel, onSuccess }) => {
  const isMobile = useAtomValue(isMobileAtom)
  // 从后端配置获取默认分页大小
  const defaultPageSize = useDefaultPageSize('refreshModal')

  const [loading, setLoading] = useState(false)
  const [taskStatus, setTaskStatus] = useState(null)
  const [animeGroups, setAnimeGroups] = useState([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selectedSourceIds, setSelectedSourceIds] = useState([])
  const [operationLoading, setOperationLoading] = useState(false)

  // 分页和过滤状态
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(defaultPageSize)
  const [favoriteFilter, setFavoriteFilter] = useState('all')
  const [refreshFilter, setRefreshFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [finishedFilter, setFinishedFilter] = useState('all')
  const [sortBy, setSortBy] = useState('created')
  const [sortOrder, setSortOrder] = useState('desc')
  const [stats, setStats] = useState({ total: 0, totalSources: 0, refreshEnabled: 0, favorited: 0, maxFailures: 10 })

  // 批量删除确认弹窗状态
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [deleteFiles, setDeleteFiles] = useState(true)

  // 当默认分页大小加载完成后，更新 pageSize
  useEffect(() => {
    if (defaultPageSize) {
      setPageSize(defaultPageSize)
    }
  }, [defaultPageSize])

  // 加载数据
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
      message.error('加载数据失败: ' + error.message)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, searchKeyword, favoriteFilter, refreshFilter, typeFilter, finishedFilter, sortBy, sortOrder])

  useEffect(() => {
    if (open) {
      setPage(1)
      setFavoriteFilter('all')
      setRefreshFilter('all')
      setTypeFilter('all')
      setFinishedFilter('all')
      setSortBy('created')
      setSortOrder('desc')
      setSearchKeyword('')
      setSelectedSourceIds([])
      fetchData({ page: 1, keyword: '', favoriteFilter: 'all', refreshFilter: 'all', typeFilter: 'all', finishedFilter: 'all', sortBy: 'created', sortOrder: 'desc' })
    }
  }, [open])

  // 搜索处理（防抖）
  const handleSearch = (value) => {
    setSearchKeyword(value)
    setPage(1)
    fetchData({ page: 1, keyword: value })
  }

  // 过滤器变更处理
  const handleFavoriteFilterChange = (filter) => {
    setFavoriteFilter(filter)
    setPage(1)
    fetchData({ page: 1, favoriteFilter: filter })
  }

  const handleRefreshFilterChange = (filter) => {
    setRefreshFilter(filter)
    setPage(1)
    fetchData({ page: 1, refreshFilter: filter })
  }

  const handleTypeFilterChange = (filter) => {
    setTypeFilter(filter)
    setPage(1)
    fetchData({ page: 1, typeFilter: filter })
  }

  const handleFinishedFilterChange = (filter) => {
    setFinishedFilter(filter)
    setPage(1)
    fetchData({ page: 1, finishedFilter: filter })
  }

  // 排序选项配置（与弹幕库风格一致）
  const SORT_OPTIONS = [
    { key: 'created', label: '入库时间' },
    { key: 'title',   label: '标题排序' },
  ]
  const currentSortLabel = SORT_OPTIONS.find(o => o.key === sortBy)?.label || '排序'

  const sortDropdownItems = {
    items: SORT_OPTIONS.map(opt => {
      const isActive = opt.key === sortBy
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
            <MyIcon icon={arrowIcon} size={13} style={{ color: isActive ? 'var(--ant-color-primary)' : undefined }} />
          </span>
        ),
      }
    }),
    onClick: ({ key }) => {
      if (key === sortBy) {
        const newOrder = sortOrder === 'desc' ? 'asc' : 'desc'
        setSortOrder(newOrder)
        setPage(1)
        fetchData({ page: 1, sortOrder: newOrder })
      } else {
        setSortBy(key)
        setPage(1)
        fetchData({ page: 1, sortBy: key })
      }
    },
  }

  // 分页变更
  const handlePageChange = (newPage) => {
    setPage(newPage)
    fetchData({ page: newPage })
  }

  // 每页数量变更
  const handlePageSizeChange = (newSize) => {
    setPageSize(newSize)
    setPage(1)
    fetchData({ page: 1, pageSize: newSize })
  }

  // 切换单个源的追更状态（本地乐观更新）
  const handleToggleRefresh = async (sourceId) => {
    // 找到源所属的番剧
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return

    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.incrementalRefreshEnabled

    // 乐观更新本地状态
    setAnimeGroups(prev => prev.map(g => {
      if (g.animeId !== group.animeId) return g
      return {
        ...g,
        sources: g.sources.map(s => {
          if (s.sourceId === sourceId) {
            return { ...s, incrementalRefreshEnabled: newState }
          }
          // 互斥：开启一个源时关闭同组其他源
          if (newState) {
            return { ...s, incrementalRefreshEnabled: false }
          }
          return s
        })
      }
    }))

    try {
      await toggleSourceIncremental({ sourceId })
    } catch (error) {
      message.error('操作失败: ' + error.message)
      fetchData() // 失败时重新获取数据恢复状态
    }
  }

  // 切换单个源的标记状态（本地乐观更新）
  const handleToggleFavorite = async (sourceId) => {
    // 找到源所属的番剧
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return

    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.isFavorited

    // 乐观更新本地状态
    setAnimeGroups(prev => prev.map(g => {
      if (g.animeId !== group.animeId) return g
      return {
        ...g,
        sources: g.sources.map(s => {
          if (s.sourceId === sourceId) {
            return { ...s, isFavorited: newState }
          }
          // 互斥：开启一个源时关闭同组其他源
          if (newState) {
            return { ...s, isFavorited: false }
          }
          return s
        })
      }
    }))

    try {
      await toggleSourceFavorite({ sourceId })
    } catch (error) {
      message.error('操作失败: ' + error.message)
      fetchData() // 失败时重新获取数据恢复状态
    }
  }

  // 切换单个源的完结状态（本地乐观更新）
  const handleToggleFinished = async (sourceId) => {
    const group = animeGroups.find(g => g.sources.some(s => s.sourceId === sourceId))
    if (!group) return
    const source = group.sources.find(s => s.sourceId === sourceId)
    const newState = !source.isFinished

    setAnimeGroups(prev => prev.map(g => ({
      ...g,
      sources: g.sources.map(s =>
        s.sourceId === sourceId ? { ...s, isFinished: newState } : s
      )
    })))

    try {
      await toggleSourceFinished({ sourceId })
    } catch (error) {
      message.error('操作失败: ' + error.message)
      fetchData()
    }
  }

  // 批量开启追更
  const handleBatchEnableRefresh = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchToggleIncrementalRefresh({ sourceIds: selectedSourceIds, enabled: true })
      message.success('批量开启成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 批量关闭追更
  const handleBatchDisableRefresh = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchToggleIncrementalRefresh({ sourceIds: selectedSourceIds, enabled: false })
      message.success('批量关闭成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 批量设置标记
  const handleBatchSetFavorite = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchSetFavorite({ sourceIds: selectedSourceIds })
      message.success('批量标记成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 批量取消标记
  const handleBatchUnsetFavorite = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchUnsetFavorite({ sourceIds: selectedSourceIds })
      message.success('批量取消标记成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 批量标记完结
  const handleBatchSetFinished = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchSetSourceFinished({ sourceIds: selectedSourceIds })
      message.success('批量标记完结成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 批量取消完结
  const handleBatchUnsetFinished = async () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setOperationLoading(true)
    try {
      await batchUnsetSourceFinished({ sourceIds: selectedSourceIds })
      message.success('批量取消完结成功')
      setSelectedSourceIds([])
      fetchData()
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 打开批量删除确认弹窗
  const openDeleteModal = () => {
    if (selectedSourceIds.length === 0) {
      message.warning('请先选择源')
      return
    }
    setDeleteModalOpen(true)
  }

  // 执行批量删除
  const handleBatchDelete = async () => {
    setOperationLoading(true)
    try {
      const deletedCount = selectedSourceIds.length
      await deleteAnimeSource({ sourceIds: selectedSourceIds, deleteFiles })
      message.success(`批量删除任务已提交，共 ${deletedCount} 个源`)
      setSelectedSourceIds([])
      setDeleteModalOpen(false)
      setDeleteFiles(true)  // 重置为默认值

      // 计算删除后当前页是否还有数据
      // 如果当前页的所有条目都被删除了，且不是第一页，则跳转到上一页
      const currentPageItemCount = animeGroups.flatMap(g => g.sources).length
      if (deletedCount >= currentPageItemCount && page > 1) {
        setPage(page - 1)
        fetchData({ page: page - 1 })
      } else {
        fetchData()
      }
    } catch (error) {
      message.error('操作失败: ' + error.message)
    } finally {
      setOperationLoading(false)
    }
  }

  // 全选当前页
  const handleSelectAll = () => {
    const allSourceIds = animeGroups.flatMap(g => g.sources.map(s => s.sourceId))
    setSelectedSourceIds(allSourceIds)
  }

  // 取消全选
  const handleDeselectAll = () => {
    setSelectedSourceIds([])
  }

  // 选择框变化
  const handleCheckboxChange = (sourceId, checked) => {
    setSelectedSourceIds(prev =>
      checked ? [...prev, sourceId] : prev.filter(id => id !== sourceId)
    )
  }

  // 渲染定时任务状态
  const renderTaskStatus = () => {
    if (!taskStatus) return null

    if (!taskStatus.exists) {
      return (
        <Alert
          type="warning"
          icon={<WarningOutlined />}
          message="增量追更定时任务未配置"
          description="请在设置中配置增量追更定时任务，否则追更功能不会自动执行。"
          showIcon
          style={{ marginBottom: 12 }}
          banner
        />
      )
    }

    return (
      <Alert
        type={taskStatus.enabled ? 'success' : 'warning'}
        icon={taskStatus.enabled ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
        message={
          <span>
            追更任务：{taskStatus.enabled ? '已启用' : '已禁用'}
            {taskStatus.cronExpression && (
              <span className="text-xs text-gray-400 ml-2">{taskStatus.cronExpression}</span>
            )}
            {taskStatus.nextRunTime && taskStatus.enabled && (
              <span className="text-xs text-gray-400 ml-2">
                下次 {dayjs(taskStatus.nextRunTime).format('MM-DD HH:mm')}
              </span>
            )}
          </span>
        }
        showIcon
        style={{ marginBottom: 12 }}
        banner
      />
    )
  }

  // 渲染源列表项
  const renderSourceItem = (source, animeTitle) => (
    <div key={source.sourceId} className="source-item flex items-center gap-3 py-2.5 px-3 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors" style={{ borderBottom: '1px solid var(--ant-color-border-secondary, #f0f0f0)' }}>
      <Checkbox
        checked={selectedSourceIds.includes(source.sourceId)}
        onChange={(e) => handleCheckboxChange(source.sourceId, e.target.checked)}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{source.providerName}</span>
          <span className="text-xs text-gray-400">第{source.episodeCount}集</span>
          {source.incrementalRefreshEnabled && source.incrementalRefreshFailures > 0 && (
            <Tag color="error" bordered={false} style={{ fontSize: 11, lineHeight: '18px', padding: '0 6px', margin: 0 }}>
              失败{source.incrementalRefreshFailures}/{stats.maxFailures}
            </Tag>
          )}
          {source.lastRefreshLatestEpisodeAt && (
            <span className="text-xs text-gray-400 hidden sm:inline">
              追更于 {dayjs(source.lastRefreshLatestEpisodeAt).format('MM-DD HH:mm')}
            </span>
          )}
        </div>
      </div>
      <Space size={4}>
        <Switch
          size="small"
          checkedChildren="追更"
          unCheckedChildren="追更"
          checked={source.incrementalRefreshEnabled}
          onChange={() => handleToggleRefresh(source.sourceId)}
        />
        <Switch
          size="small"
          checkedChildren="标记"
          unCheckedChildren="标记"
          checked={source.isFavorited}
          onChange={() => handleToggleFavorite(source.sourceId)}
        />
        <Switch
          size="small"
          checkedChildren="完结"
          unCheckedChildren="完结"
          checked={source.isFinished}
          onChange={() => handleToggleFinished(source.sourceId)}
        />
      </Space>
    </div>
  )

  // 渲染内容
  const renderContent = () => (
    <div className="flex flex-col h-full">
      {/* 定时任务状态 */}
      {renderTaskStatus()}

      {/* 统计信息 */}
      <div className="mb-3 text-sm text-gray-500">
        共 <span className="font-medium text-gray-700 dark:text-gray-300">{stats.totalSources}</span> 个源，
        追更 <span className="font-medium text-blue-500">{stats.refreshEnabled}</span>，
        标记 <span className="font-medium text-green-500">{stats.favorited}</span>
      </div>

      {/* 筛选器 */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        <Dropdown
          menu={{
            items: [
              { key: 'all', label: '全部类型' },
              { key: 'movie', label: '电影' },
              { key: 'tv_series', label: '电视节目' },
            ],
            selectedKeys: [typeFilter],
            onClick: ({ key }) => handleTypeFilterChange(key),
          }}
          trigger={['click']}
        >
          <Button size="small" type={typeFilter !== 'all' ? 'primary' : 'default'} ghost={typeFilter !== 'all'}>
            {typeFilter === 'all' ? '类型' : typeFilter === 'movie' ? '电影' : '电视'} <DownOutlined />
          </Button>
        </Dropdown>
        <Dropdown
          menu={{
            items: [
              { key: 'all', label: '全部' },
              { key: 'enabled', label: '已追更' },
              { key: 'disabled', label: '未追更' },
            ],
            selectedKeys: [refreshFilter],
            onClick: ({ key }) => handleRefreshFilterChange(key),
          }}
          trigger={['click']}
        >
          <Button size="small" type={refreshFilter !== 'all' ? 'primary' : 'default'} ghost={refreshFilter !== 'all'}>
            {refreshFilter === 'all' ? '追更' : refreshFilter === 'enabled' ? '已追更' : '未追更'} <DownOutlined />
          </Button>
        </Dropdown>
        <Dropdown
          menu={{
            items: [
              { key: 'all', label: '全部' },
              { key: 'favorited', label: '已标记' },
              { key: 'unfavorited', label: '未标记' },
            ],
            selectedKeys: [favoriteFilter],
            onClick: ({ key }) => handleFavoriteFilterChange(key),
          }}
          trigger={['click']}
        >
          <Button size="small" type={favoriteFilter !== 'all' ? 'primary' : 'default'} ghost={favoriteFilter !== 'all'}>
            {favoriteFilter === 'all' ? '标记' : favoriteFilter === 'favorited' ? '已标记' : '未标记'} <DownOutlined />
          </Button>
        </Dropdown>
        <Dropdown
          menu={{
            items: [
              { key: 'all', label: '全部' },
              { key: 'finished', label: '已完结' },
              { key: 'unfinished', label: '未完结' },
            ],
            selectedKeys: [finishedFilter],
            onClick: ({ key }) => handleFinishedFilterChange(key),
          }}
          trigger={['click']}
        >
          <Button size="small" type={finishedFilter !== 'all' ? 'primary' : 'default'} ghost={finishedFilter !== 'all'}>
            {finishedFilter === 'all' ? '完结' : finishedFilter === 'finished' ? '已完结' : '未完结'} <DownOutlined />
          </Button>
        </Dropdown>
        <Dropdown menu={sortDropdownItems} trigger={['click']}>
          <Button size="small">
            <span className="flex items-center gap-1">
              {currentSortLabel}
              <MyIcon icon={sortOrder === 'asc' ? 'arrowTop-fill' : 'xiajiantou-'} size={13} />
            </span>
          </Button>
        </Dropdown>
        {!isMobile && (
          <Popover
            content={
              <div style={{ width: 220 }}>
                <Input
                  placeholder="搜索番剧或源名称..."
                  allowClear
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  onPressEnter={(e) => handleSearch(e.target.value)}
                  autoFocus
                />
              </div>
            }
            title="搜索"
            trigger="click"
            placement="bottom"
          >
            <Button size="small" icon={<SearchOutlined />} type={searchKeyword ? 'primary' : 'default'} ghost={!!searchKeyword}>
              {searchKeyword ? `${searchKeyword.length > 4 ? searchKeyword.slice(0, 4) + '...' : searchKeyword}` : '搜索'}
            </Button>
          </Popover>
        )}
      </div>

      {/* 源列表 */}
      <div className="flex-1 overflow-auto" style={{ maxHeight: isMobile ? 'calc(100vh - 400px)' : 350 }}>
        {loading ? (
          <div className="flex justify-center py-8"><Spin /></div>
        ) : animeGroups.length === 0 ? (
          <Empty description="暂无数据" />
        ) : (
          <Collapse
            bordered={false}
            size="small"
            defaultActiveKey={animeGroups.slice(0, 3).map(g => g.animeId)}
            items={animeGroups.map(group => ({
              key: group.animeId,
              label: (
                <div className="flex items-center gap-2">
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{
                    background: group.animeType === 'movie' ? 'var(--ant-purple-1, #f9f0ff)' : 'var(--ant-blue-1, #e6f4ff)',
                    color: group.animeType === 'movie' ? 'var(--ant-purple-6, #722ed1)' : 'var(--ant-blue-6, #1677ff)',
                  }}>
                    {group.animeType === 'movie' ? '电影' : 'TV'}
                  </span>
                  <span className="font-medium text-sm">{group.animeTitle}</span>
                  <span className="text-xs text-gray-400">{group.sources.length}源</span>
                </div>
              ),
              children: group.sources.map(source => renderSourceItem(source, group.animeTitle)),
            }))}
          />
        )}
      </div>

      {/* 分页 */}
      {stats.total > pageSize && (
        <div className="mt-3 flex justify-center items-center gap-3">
          <Pagination
            current={page}
            pageSize={pageSize}
            total={stats.total}
            onChange={handlePageChange}
            showSizeChanger={false}
            showQuickJumper={stats.total > pageSize * 3}
            size="small"
          />
          <Dropdown
            menu={{
              items: [
                { key: '10', label: '10 条/页' },
                { key: '20', label: '20 条/页' },
                { key: '50', label: '50 条/页' },
                { key: '100', label: '100 条/页' },
              ],
              selectedKeys: [String(pageSize)],
              onClick: ({ key }) => handlePageSizeChange(Number(key)),
            }}
            trigger={['click']}
          >
            <Button size="small">
              {pageSize} 条/页 <DownOutlined />
            </Button>
          </Dropdown>
        </div>
      )}

      {/* 批量操作按钮 */}
      <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-600">
        {/* 第一行：已选数量 + 搜索（移动端） */}
        <div className="flex items-center gap-2 mb-2">
          <span className="text-gray-500 text-sm">
            已选 <span className="font-medium text-blue-500">{selectedSourceIds.length}</span> 项
          </span>
          {isMobile && (
            <Popover
              content={
                <div style={{ width: 220 }}>
                  <Input
                    placeholder="搜索番剧或源名称..."
                    allowClear
                    value={searchKeyword}
                    onChange={(e) => setSearchKeyword(e.target.value)}
                    onPressEnter={(e) => handleSearch(e.target.value)}
                    autoFocus
                  />
                </div>
              }
              title="搜索"
              trigger="click"
              placement="top"
            >
              <Button size="small" icon={<SearchOutlined />} className="ml-auto">
                {searchKeyword ? `搜索: ${searchKeyword.length > 4 ? searchKeyword.slice(0, 4) + '...' : searchKeyword}` : '搜索'}
              </Button>
            </Popover>
          )}
        </div>
        {/* 移动端：分两行显示按钮 */}
        {isMobile ? (
          <div className="space-y-2">
            {/* 第一行：操作 + 批量删除 */}
            <div className="flex gap-2">
              <Dropdown
                menu={{
                  items: [
                    { key: 'selectAll', label: '全选当前页', onClick: handleSelectAll },
                    { key: 'deselectAll', label: '取消全选', onClick: handleDeselectAll },
                  ],
                }}
                trigger={['click']}
              >
                <Button size="small" className="flex-1">
                  操作 <DownOutlined />
                </Button>
              </Dropdown>
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={operationLoading}
                disabled={selectedSourceIds.length === 0}
                className="flex-1"
                onClick={openDeleteModal}
              >
                批量删除
              </Button>
            </div>
            {/* 第二行：批量追更 + 批量标记 */}
            <div className="flex gap-2">
              <Dropdown
                menu={{
                  items: [
                    { key: 'enable', label: '批量开启', onClick: handleBatchEnableRefresh, disabled: selectedSourceIds.length === 0 },
                    { key: 'disable', label: '批量关闭', onClick: handleBatchDisableRefresh, disabled: selectedSourceIds.length === 0 },
                  ],
                }}
                trigger={['click']}
                disabled={operationLoading}
              >
                <Button size="small" loading={operationLoading} className="flex-1">
                  批量追更 <DownOutlined />
                </Button>
              </Dropdown>
              <Dropdown
                menu={{
                  items: [
                    { key: 'set', label: '批量开启', onClick: handleBatchSetFavorite, disabled: selectedSourceIds.length === 0 },
                    { key: 'unset', label: '批量关闭', onClick: handleBatchUnsetFavorite, disabled: selectedSourceIds.length === 0 },
                  ],
                }}
                trigger={['click']}
                disabled={operationLoading}
              >
                <Button size="small" loading={operationLoading} className="flex-1">
                  批量标记 <DownOutlined />
                </Button>
              </Dropdown>
              <Dropdown
                menu={{
                  items: [
                    { key: 'set', label: '批量完结', onClick: handleBatchSetFinished, disabled: selectedSourceIds.length === 0 },
                    { key: 'unset', label: '批量取消', onClick: handleBatchUnsetFinished, disabled: selectedSourceIds.length === 0 },
                  ],
                }}
                trigger={['click']}
                disabled={operationLoading}
              >
                <Button size="small" loading={operationLoading} className="flex-1">
                  批量完结 <DownOutlined />
                </Button>
              </Dropdown>
            </div>
          </div>
        ) : (
          /* 桌面端：一行显示所有按钮 */
          <Space size="small" wrap>
            <Dropdown
              menu={{
                items: [
                  { key: 'selectAll', label: '全选当前页', onClick: handleSelectAll },
                  { key: 'deselectAll', label: '取消全选', onClick: handleDeselectAll },
                ],
              }}
              trigger={['click']}
            >
              <Button size="small">
                操作 <DownOutlined />
              </Button>
            </Dropdown>
            <Dropdown
              menu={{
                items: [
                  { key: 'enable', label: '批量开启', onClick: handleBatchEnableRefresh, disabled: selectedSourceIds.length === 0 },
                  { key: 'disable', label: '批量关闭', onClick: handleBatchDisableRefresh, disabled: selectedSourceIds.length === 0 },
                ],
              }}
              trigger={['click']}
              disabled={operationLoading}
            >
              <Button size="small" loading={operationLoading}>
                批量追更 <DownOutlined />
              </Button>
            </Dropdown>
            <Dropdown
              menu={{
                items: [
                  { key: 'set', label: '批量开启', onClick: handleBatchSetFavorite, disabled: selectedSourceIds.length === 0 },
                  { key: 'unset', label: '批量关闭', onClick: handleBatchUnsetFavorite, disabled: selectedSourceIds.length === 0 },
                ],
              }}
              trigger={['click']}
              disabled={operationLoading}
            >
              <Button size="small" loading={operationLoading}>
                批量标记 <DownOutlined />
              </Button>
            </Dropdown>
            <Dropdown
              menu={{
                items: [
                  { key: 'set', label: '批量完结', onClick: handleBatchSetFinished, disabled: selectedSourceIds.length === 0 },
                  { key: 'unset', label: '批量取消', onClick: handleBatchUnsetFinished, disabled: selectedSourceIds.length === 0 },
                ],
              }}
              trigger={['click']}
              disabled={operationLoading}
            >
              <Button size="small" loading={operationLoading}>
                批量完结 <DownOutlined />
              </Button>
            </Dropdown>
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={operationLoading}
              disabled={selectedSourceIds.length === 0}
              onClick={openDeleteModal}
            >
              批量删除
            </Button>
          </Space>
        )}
      </div>
    </div>
  )

  // 删除确认弹窗
  const renderDeleteModal = () => (
    <Modal
      title="批量删除确认"
      open={deleteModalOpen}
      onCancel={() => {
        setDeleteModalOpen(false)
        setDeleteFiles(true)
      }}
      onOk={handleBatchDelete}
      okText="确定删除"
      cancelText="取消"
      okButtonProps={{ danger: true, loading: operationLoading }}
    >
      <div className="py-4">
        <p className="mb-4">确定要删除选中的 <strong>{selectedSourceIds.length}</strong> 个源吗？</p>
        <div className="flex items-center gap-2">
          <Checkbox
            checked={deleteFiles}
            onChange={(e) => setDeleteFiles(e.target.checked)}
          >
            同时删除弹幕XML文件
          </Checkbox>
        </div>
        <p className="text-gray-500 text-sm mt-2">
          {deleteFiles ? '将删除源及其关联的弹幕文件' : '仅删除数据库记录，保留弹幕文件'}
        </p>
      </div>
    </Modal>
  )

  // 响应式渲染
  if (isMobile) {
    return (
      <>
        <Drawer
          title="批量管理"
          placement="bottom"
          onClose={onCancel}
          open={open}
          height="85vh"
        >
          {renderContent()}
        </Drawer>
        {renderDeleteModal()}
      </>
    )
  }

  return (
    <>
      <Modal
        title="批量管理"
        open={open}
        onCancel={onCancel}
        footer={null}
        width={700}
      >
        {renderContent()}
      </Modal>
      {renderDeleteModal()}
    </>
  )
}

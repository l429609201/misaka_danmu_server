import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  deleteTask,
  getTaskList,
  pauseTask,
  resumeTask,
  retryTask,
  stopTask,
} from '@/apis'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  List,
  message,
  Modal,
  Progress,
  Space,
  Tag,
  Tooltip,
  Dropdown,
  Input as AntInput,
} from 'antd'
import {
  CheckOutlined,
  DeleteOutlined,
  MinusOutlined,
  PauseOutlined,
  RetweetOutlined,
  StepBackwardOutlined,
  StopOutlined,
  FilterOutlined,
  DownloadOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import classNames from 'classnames'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'
import { useAtom } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { ResponsiveTable } from '@/components/ResponsiveTable'

export const ImportTask = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [taskList, setTaskList] = useState([])
  const [selectList, setSelectList] = useState([])
  const timer = useRef()

  const [isMobile] = useAtom(isMobileAtom)

  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 100,
    total: 0,
  })

  const navigate = useNavigate()
  const modalApi = useModal()
  const messageApi = useMessage()

  const [canPause, isPause] = useMemo(() => {
    return [
      (selectList.every(item => item.status === '运行中') &&
        selectList.length > 0) ||
      (selectList.every(item => item.status === '已暂停') &&
        selectList.length > 0),
      selectList.every(item => item.status === '已暂停'),
    ]
  }, [selectList])

  const canStop = useMemo(() => {
    return selectList.some(item =>
      item.status === '运行中' || item.status === '已暂停'
    ) && selectList.length > 0
  }, [selectList])

  const canDelete = useMemo(() => {
    return (
      selectList.every(
        item =>
          item.status === '已完成' ||
          item.status === '失败' ||
          item.status === '排队中'
      ) && selectList.length > 0
    )
  }, [selectList])

  // 后端 _rebuild_coro_factory 支持重建的任务类型白名单
  const RETRYABLE_TYPES = ['generic_import', 'webhook_search', 'full_refresh', 'incremental_refresh', 'auto_import']

  // 只有失败的且 taskType 在可重试白名单内的任务才能重试
  const canRetry = useMemo(() => {
    return (
      selectList.every(item => item.status === '失败' && RETRYABLE_TYPES.includes(item.taskType)) &&
      selectList.length > 0
    )
  }, [selectList])

  const [searchParams] = useSearchParams()
  const [queueFilter, setQueueFilter] = useState('all') // 队列类型过滤: all, download, management
  const [searchInputValue, setSearchInputValue] = useState('')

  const [search, status] = useMemo(() => {
    return [
      searchParams.get('search') ?? '',
      searchParams.get('status') ?? 'in_progress',
    ]
  }, [searchParams])

  useEffect(() => {
    setPagination(n => ({
      ...n,
      pageSize: 100,
      current: 1,
    }))
  }, [search, status, queueFilter])

  useEffect(() => {
    setSearchInputValue(search)
  }, [search])

  /**
   * 轮询刷新当前页面任务列表
   */
  const pollTasks = useCallback(async () => {
    try {
      const res = await getTaskList({
        search,
        status,
        queueType: queueFilter,  // 传递队列类型参数给后端
        page: pagination.current,
        pageSize: pagination.pageSize,
      })

      const newData = res.data?.list || []
      setTaskList(newData)

    } catch (error) {
      console.error(t('importTask.pollFailed'), error)
    }
  }, [search, status, pagination.current, pagination.pageSize, queueFilter])

  /**
   * 刷新任务列表
   */
  const refreshTasks = useCallback(async () => {
    try {
      setLoading(true)

      const res = await getTaskList({
        search,
        status,
        queueType: queueFilter,  // 传递队列类型参数给后端
        page: pagination.current,
        pageSize: pagination.pageSize,
      })

      const newData = res.data?.list || []
      setTaskList(newData)

      setLoading(false)
      setPagination(prev => ({
        ...prev,
        total: res.data?.total || 0,
      }))
    } catch (error) {
      console.error(error)
      setLoading(false)
    }
  }, [search, status, pagination.current, pagination.pageSize, queueFilter])

  /**
   * 处理搜索操作
   */
  const handleSearch = () => {
    navigate(`/task?search=${searchInputValue}&status=${status}`, { replace: true })
  }

  /**
   * 处理暂停/恢复任务操作
   */
  const handlePause = async () => {
    if (isPause) {
      try {
        await Promise.all(
          selectList.map(it => resumeTask({ taskId: it.taskId }))
        )
        refreshTasks()
        setSelectList([])
      } catch (error) {
        message.error(t('importTask.operationFailed', { msg: error.message }))
      }
    } else {
      try {
        await Promise.all(
          selectList.map(it => pauseTask({ taskId: it.taskId }))
        )
        refreshTasks()
        setSelectList([])
      } catch (error) {
        message.error(t('importTask.operationFailed', { msg: error.message }))
      }
    }
  }

  /**
   * 处理中止任务操作
   */
  const handleStop = () => {

    let forceStop = false

    const StopConfirmContent = () => {
      const [force, setForce] = useState(false)

      useEffect(() => {
        forceStop = force
      }, [force])

      return (
        <div>
          <div>{t('importTask.abortConfirm')}</div>
          <div className="max-h-[310px] overflow-y-auto mt-3">
            {selectList.map((it, i) => (
              <div key={it.taskId}>
                {i + 1}、{it.title}
              </div>
            ))}
          </div>

          <div className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded">
            <label className="flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
                className="mr-2"
              />
              <span className="text-sm">
                {t('importTask.forceAbort')}
                <span className="text-gray-500 ml-1">
                  {t('importTask.forceAbortDesc')}
                </span>
              </span>
            </label>
            {force && (
              <div className="mt-2 text-xs text-orange-600">
                {t('importTask.forceAbortWarn')}
              </div>
            )}
          </div>
        </div>
      )
    }

    modalApi.confirm({
      title: t('importTask.abortTitle'),
      content: <StopConfirmContent />,
      okText: t('importTask.confirm'),
      cancelText: t('importTask.cancel'),
      onOk: async () => {
        try {
          await Promise.all(
            selectList.map(it => stopTask({ taskId: it.taskId, force: forceStop }))
          )
          refreshTasks()
          setSelectList([])
          messageApi.success(forceStop ? t('importTask.forceAbortSuccess') : t('importTask.abortSuccess'))
        } catch (error) {
          messageApi.error(t('importTask.abortFailed', { msg: error.message }))
          throw error
        }
      },
    })
  }

  /**
   * 处理删除任务操作
   */
  const handleDelete = () => {

    const hasStuckTasks = selectList.some(task =>
      task.status === '运行中' || task.status === '已暂停'
    )

    let forceDelete = false

    const DeleteConfirmContent = () => {
      const [force, setForce] = useState(false)

      useEffect(() => {
        forceDelete = force
      }, [force])

      return (
        <div>
          <div>{t('importTask.deleteConfirm')}</div>
          <div className="max-h-[310px] overflow-y-auto mt-3">
            {selectList.map((it, i) => (
              <div key={it.taskId}>
                {i + 1}、{it.title}
                {(it.status === '运行中' || it.status === '已暂停') && (
                  <span className="text-orange-500 ml-2">({it.status})</span>
                )}
              </div>
            ))}
          </div>

          <div className="mt-4 p-3 bg-gray-50 border border-gray-200 rounded">
            <label className="flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
                className="mr-2"
              />
              <span className="text-sm">
                {t('importTask.forceDelete')}
                <span className="text-gray-500 ml-1">
                  {t('importTask.forceDeleteDesc')}
                </span>
              </span>
            </label>
            {force && (
              <div className="mt-2 text-xs text-orange-600">
                {t('importTask.forceDeleteWarn')}
              </div>
            )}
          </div>

          {hasStuckTasks && !force && (
            <div className="mt-3 p-2 bg-yellow-50 border border-yellow-200 rounded">
              <div className="text-sm text-yellow-700">
                {t('importTask.forceDeleteHint')}
              </div>
            </div>
          )}
        </div>
      )
    }

    modalApi.confirm({
      title: t('importTask.deleteTitle'),
      content: <DeleteConfirmContent />,
      okText: t('importTask.confirm'),
      cancelText: t('importTask.cancel'),
      onOk: async () => {
        try {
          // 如果有卡住的任务但没有勾选强制删除，阻止执行
          if (hasStuckTasks && !forceDelete) {
            messageApi.warning(t('importTask.needForceDelete'))
            return Promise.reject(new Error(t('importTask.needForceDeleteErr')))
          }

          await Promise.all(
            selectList.map(it => deleteTask({ taskId: it.taskId, force: forceDelete }))
          )
          refreshTasks()
          setSelectList([])
          messageApi.success(forceDelete ? t('importTask.forceDeleteSuccess') : t('importTask.deleteSuccess'))
        } catch (error) {
          messageApi.error(t('importTask.deleteFailed', { msg: error.message }))
          throw error
        }
      },
    })
  }

  /**
   * 处理重试失败任务操作
   */
  const handleRetry = async () => {
    const results = await Promise.allSettled(
      selectList.map(it => retryTask({ taskId: it.taskId }))
    )
    const succeeded = results.filter(r => r.status === 'fulfilled').length
    const failed = results.filter(r => r.status === 'rejected').length

    refreshTasks()
    setSelectList([])

    if (failed === 0) {
      messageApi.success(t('importTask.resubmitted', { count: succeeded }))
    } else if (succeeded === 0) {
      messageApi.error(t('importTask.retryFailed', { count: failed }))
    } else {
      messageApi.warning(t('importTask.retryPartial', { succeeded, failed }))
    }
  }

  useEffect(() => {
    const isLoadMore = pagination.current > 1
    refreshTasks(isLoadMore)
    if (!isLoadMore) {
      setSelectList([])
    }
  }, [search, status, pagination.current, pagination.pageSize])

  useEffect(() => {
    // 清除之前的定时器
    clearInterval(timer.current)

    // 启动轮询定时器，每3秒刷新当前页面任务列表
    timer.current = setInterval(() => {
      pollTasks()
    }, 3000)

    return () => {
      clearInterval(timer.current)
    }
  }, [pollTasks])

  // 状态筛选菜单
  const statusMenu = {
    items: [
      { key: 'in_progress', label: t('importTask.filterInProgress') },
      { key: 'completed', label: t('importTask.filterCompleted') },
      { key: 'all', label: t('importTask.filterAll') },
    ],
    onClick: ({ key }) => {
      navigate(`/task?search=${search}&status=${key}`, {
        replace: true,
      })
    },
  }

  const getStatusLabel = (status) => {
    switch (status) {
      case 'in_progress': return t('importTask.filterInProgress')
      case 'completed': return t('importTask.filterCompleted')
      case 'all': return t('importTask.filterAll')
      default: return t('importTask.filterInProgress')
    }
  }

  // 队列类型筛选菜单
  const queueMenu = {
    items: [
      { key: 'all', label: t('importTask.queueAll') },
      { key: 'download', label: t('importTask.queueDownload') },
      { key: 'management', label: t('importTask.queueManagement') },
      { key: 'fallback', label: t('importTask.queueFallback') },
    ],
    onClick: ({ key }) => {
      setQueueFilter(key)
      // 立即刷新任务列表，不等待轮询
      setTimeout(() => refreshTasks(), 0)
    },
  }

  const getQueueLabel = (queue) => {
    switch (queue) {
      case 'all': return t('importTask.queueAll')
      case 'download': return t('importTask.queueDownload')
      case 'management': return t('importTask.queueManagement')
      case 'fallback': return t('importTask.queueFallback')
      default: return t('importTask.queueAll')
    }
  }

  // 获取队列类型图标
  const getQueueIcon = (queueType) => {
    if (queueType === 'management') return <SettingOutlined />
    if (queueType === 'fallback') return <ThunderboltOutlined />
    return <DownloadOutlined />
  }

  // 移动端任务卡片渲染
  const renderTaskCard = (item) => {
    const isActive = selectList.some(it => it.taskId === item.taskId)

    return (
      <div
        className={`p-4 rounded-lg transition-all relative cursor-pointer ${isActive
            ? 'shadow-lg ring-2 ring-pink-400/50 bg-pink-50/30 dark:bg-pink-900/10'
            : 'hover:shadow-md hover:bg-gray-50 dark:hover:bg-gray-800/30'
          }`}
        onClick={() => {
          setSelectList(list => {
            return list.map(it => it.taskId).includes(item.taskId)
              ? list.filter(i => i.taskId !== item.taskId)
              : [...list, item]
          })
        }}
      >
        <div className="space-y-3 relative">
          {isActive && (
            <div className="absolute -top-1 -right-1 w-3 h-3 bg-pink-400 rounded-full border-2 border-white dark:border-gray-800 z-10"></div>
          )}

          {/* 标题区域 */}
          <div className="flex items-start justify-between">
            <div className="flex items-start gap-3 flex-1 min-w-0">
              <div className="flex-shrink-0 mt-0.5">
                {getQueueIcon(item.queueType)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-base break-words mb-2">
                  {item.title}
                </div>
                <div className="flex flex-wrap gap-1 mb-2">
                  <Tag
                    color={
                      item.status.includes('失败')
                        ? 'red'
                        : item.status.includes('运行中')
                          ? 'green'
                          : item.status.includes('已暂停')
                            ? 'orange'
                            : item.status.includes('已完成')
                              ? 'blue'
                              : 'default'
                    }
                    className="text-xs"
                  >
                    {item.status}
                  </Tag>
                  <Tag
                    color={
                      item.queueType === 'management'
                        ? 'cyan'
                        : item.queueType === 'fallback'
                          ? 'orange'
                          : 'geekblue'
                    }
                    className="text-xs"
                  >
                    {item.queueType === 'management'
                      ? t('importTask.typeManagement')
                      : item.queueType === 'fallback'
                        ? t('importTask.typeFallback')
                        : t('importTask.typeDownload')}
                  </Tag>
                </div>
              </div>
            </div>
          </div>

          {/* 描述 */}
          {item.description && (
            <div className="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">
              {item.description}
            </div>
          )}

          {/* 时间 */}
          {item.createdAt && (
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {(() => {
                const date = new Date(item.createdAt)
                const year = date.getFullYear()
                const month = String(date.getMonth() + 1).padStart(2, '0')
                const day = String(date.getDate()).padStart(2, '0')
                const hour = String(date.getHours()).padStart(2, '0')
                const minute = String(date.getMinutes()).padStart(2, '0')
                const second = String(date.getSeconds()).padStart(2, '0')
                return `${year}-${month}-${day} ${hour}:${minute}:${second}`
              })()}
            </div>
          )}

          {/* 进度条 */}
          <div className="pt-2">
            <Progress
              percent={item.progress}
              status={item.status.includes('失败') && 'exception'}
              strokeColor={item.status.includes('失败') ? undefined : {
                '0%': '#108ee9',
                '100%': '#87d068',
              }}
              strokeWidth={8}
              showInfo={true}
              size="small"
            />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title={t('importTask.cardTitle')}
        extra={
          !isMobile && (
            <div className='flex items-center justify-end gap-2 flex-wrap' style={{ maxWidth: '100%' }}>
              <Dropdown menu={statusMenu}>
                <Button icon={<FilterOutlined />}>
                  {getStatusLabel(status)}
                </Button>
              </Dropdown>
              <Dropdown menu={queueMenu}>
                <Button icon={<FilterOutlined />}>
                  {getQueueLabel(queueFilter)}
                </Button>
              </Dropdown>
              <Tooltip title={t('importTask.selectAllTip')}>
                <Button
                  type="default"
                  shape="circle"
                  icon={
                    selectList.length === taskList.length &&
                      !!selectList.length ? (
                      <CheckOutlined />
                    ) : (
                      <MinusOutlined />
                    )
                  }
                  onClick={() => {
                    if (
                      selectList.length === taskList.length &&
                      !!selectList.length
                    ) {
                      setSelectList([])
                    } else {
                      setSelectList(taskList)
                    }
                  }}
                />
              </Tooltip>
              <Tooltip title={t('importTask.pauseResumeTip')}>
                <Button
                  disabled={!canPause}
                  type="default"
                  shape="circle"
                  icon={isPause ? <PauseOutlined /> : <StepBackwardOutlined />}
                  onClick={handlePause}
                />
              </Tooltip>
              <Tooltip title={t('importTask.retryTip')}>
                <Button
                  disabled={!canRetry}
                  type="default"
                  shape="circle"
                  icon={<RetweetOutlined />}
                  onClick={handleRetry}
                />
              </Tooltip>
              <Tooltip title={t('importTask.deleteTip')}>
                <Button
                  disabled={!canDelete}
                  type="default"
                  shape="circle"
                  icon={<DeleteOutlined />}
                  onClick={handleDelete}
                />
              </Tooltip>
              <Tooltip title={t('importTask.abortTip')}>
                <Button
                  disabled={!canStop}
                  type="default"
                  shape="circle"
                  icon={<StopOutlined />}
                  onClick={handleStop}
                />
              </Tooltip>
              <Input.Search
                placeholder={t('importTask.searchByTitle')}
                allowClear
                enterButton
                style={{ width: isMobile ? '100%' : '200px' }}
                onSearch={value => {
                  navigate(`/task?search=${value}&status=${status}`, {
                    replace: true,
                  })
                }}
              />
            </div>
          )
        }
      >
        {isMobile && (
          <div className="mb-4 space-y-3">
            {/* 筛选器区域 */}
            <div className="grid grid-cols-2 gap-2">
              <Dropdown menu={statusMenu} trigger={['click']}>
                <Button icon={<FilterOutlined />} block>
                  {getStatusLabel(status)}
                </Button>
              </Dropdown>
              <Dropdown menu={queueMenu} trigger={['click']}>
                <Button icon={<FilterOutlined />} block>
                  {getQueueLabel(queueFilter)}
                </Button>
              </Dropdown>
            </div>

            {/* 搜索框 */}
            <div className="mb-4">
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  placeholder={t('importTask.searchTask')}
                  value={searchInputValue}
                  onChange={(e) => setSearchInputValue(e.target.value)}
                  onPressEnter={handleSearch}
                  allowClear
                  style={{
                    height: 44,
                    lineHeight: '44px',
                    paddingTop: 0,
                    paddingBottom: 0,
                    borderTopLeftRadius: 20,
                    borderBottomLeftRadius: 20,

                    fontSize: 14
                  }}
                  className="flex-1"
                />
                <Button type="primary" onClick={handleSearch} style={{
                  height: 44, 
                  borderTopLeftRadius: 0,
                  borderTopRightRadius: 20,
                  borderBottomLeftRadius: 0,
                  borderBottomRightRadius: 20,
                  fontSize: 14
                }}>{t('importTask.search')}</Button>
              </Space.Compact>
            </div>

            {/* 批量操作按钮 */}
            <div className="grid grid-cols-2 gap-2">
              <Button
                icon={
                  selectList.length === taskList.length &&
                    !!selectList.length ? (
                    <CheckOutlined />
                  ) : (
                    <MinusOutlined />
                  )
                }
                onClick={() => {
                  if (
                    selectList.length === taskList.length &&
                    !!selectList.length
                  ) {
                    setSelectList([])
                  } else {
                    setSelectList(taskList)
                  }
                }}
                block
              >
                {selectList.length === taskList.length && !!selectList.length
                  ? t('importTask.deselectAll')
                  : t('importTask.selectAll')}
              </Button>
              <Button
                disabled={!canPause}
                icon={isPause ? <PauseOutlined /> : <StepBackwardOutlined />}
                onClick={handlePause}
                block
              >
                {isPause ? t('importTask.resume') : t('importTask.pause')}
              </Button>
            </div>

            {/* 重试操作 */}
            <div className="grid grid-cols-1 gap-2">
              <Button
                disabled={!canRetry}
                icon={<RetweetOutlined />}
                onClick={handleRetry}
                block
              >
                {t('importTask.retry')}
              </Button>
            </div>

            {/* 危险操作按钮 */}
            <div className="grid grid-cols-2 gap-2">
              <Button
                disabled={!canDelete}
                danger
                icon={<DeleteOutlined />}
                onClick={handleDelete}
                block
              >
                {t('importTask.delete')}
              </Button>
              <Button
                disabled={!canStop}
                danger
                icon={<StopOutlined />}
                onClick={handleStop}
                block
              >
                {t('importTask.abort')}
              </Button>
            </div>

            {/* 选中任务提示 */}
            {selectList.length > 0 && (
              <div className="text-sm text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 p-2 rounded">
                {t('importTask.selectedCount', { count: selectList.length })}
              </div>
            )}
          </div>
        )}

        <div>
          {!!taskList?.length ? (
            isMobile ? (
              <ResponsiveTable
                pagination={false}
                size="small"
                dataSource={taskList}
                columns={[]} // 移动端不需要表格列
                rowKey={'taskId'}
                scroll={{ x: '100%' }}
                renderCard={renderTaskCard}
              />
            ) : (
              <List
                className="task-manager-list"
                itemLayout="vertical"
                size="small"
                dataSource={taskList}
                pagination={{
                  ...pagination,
                  showLessItems: true,
                  align: 'center',
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
                  showSizeChanger: true,
                  showTotal: (total, range) => t('importTask.paginationTotal', { from: range[0], to: range[1], total }),
                  locale: {
                    items_per_page: t('importTask.itemsPerPage'),
                    jump_to: t('importTask.jumpTo'),
                    jump_to_confirm: t('importTask.jumpConfirm'),
                    page: t('importTask.page'),
                    prev_page: t('importTask.prevPage'),
                    next_page: t('importTask.nextPage'),
                    prev_5: t('importTask.prev5'),
                    next_5: t('importTask.next5'),
                    prev_3: t('importTask.prev3'),
                    next_3: t('importTask.next3'),
                  },
                }}
                renderItem={(item, index) => {
                  const isActive = selectList.some(
                    it => it.taskId === item.taskId
                  )

                  return (
                    <List.Item
                      key={index}
                      onClick={() => {
                        setSelectList(list => {
                          return list.map(it => it.taskId).includes(item.taskId)
                            ? list.filter(i => i.taskId !== item.taskId)
                            : [...list, item]
                        })
                      }}
                      style={{ padding: '16px 24px' }}
                    >
                      <div
                        className={classNames('relative w-full', {
                          'pl-9': isActive,
                        })}
                      >
                        {isActive && (
                          <Checkbox
                            checked={isActive}
                            className="absolute top-1/2 left-0 transform -translate-y-1/2"
                          />
                        )}

                        {/* 第一行: 标题 + 状态标签 + 队列标签 */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                          <div className="text-base font-semibold" style={{ flex: 1 }}>
                            <span style={{ marginRight: '8px', fontSize: '18px' }}>
                              {getQueueIcon(item.queueType)}
                            </span>
                            {item.title}
                          </div>
                          <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                            <Tag
                              color={
                                item.status.includes('失败')
                                  ? 'red'
                                  : item.status.includes('运行中')
                                    ? 'green'
                                    : item.status.includes('已暂停')
                                      ? 'orange'
                                      : item.status.includes('已完成')
                                        ? 'blue'
                                        : 'default'
                              }
                            >
                              {item.status}
                            </Tag>
                            <Tag
                              color={
                                item.queueType === 'management'
                                  ? 'cyan'
                                  : item.queueType === 'fallback'
                                    ? 'orange'
                                    : 'geekblue'
                              }
                            >
                              <span style={{ marginRight: '4px' }}>
                                {getQueueIcon(item.queueType)}
                              </span>
                              {item.queueType === 'management'
                                ? t('importTask.typeManagementQueue')
                                : item.queueType === 'fallback'
                                  ? t('importTask.typeFallbackQueue')
                                  : t('importTask.typeDownloadQueue')}
                            </Tag>
                          </div>
                        </div>

                        {/* 第二行: 描述 + 时间 */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                          <Tooltip title={item.description}>
                            <div
                              className="text-gray-600"
                              style={{
                                flex: 1,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                                marginRight: '16px'
                              }}
                            >
                              {item.description}
                            </div>
                          </Tooltip>
                          {item.createdAt && (
                            <Tag style={{ flexShrink: 0 }}>
                              {(() => {
                                const date = new Date(item.createdAt)
                                const year = date.getFullYear()
                                const month = String(date.getMonth() + 1).padStart(2, '0')
                                const day = String(date.getDate()).padStart(2, '0')
                                const hour = String(date.getHours()).padStart(2, '0')
                                const minute = String(date.getMinutes()).padStart(2, '0')
                                const second = String(date.getSeconds()).padStart(2, '0')
                                return `${year}-${month}-${day} ${hour}:${minute}:${second}`
                              })()}
                            </Tag>
                          )}
                        </div>

                        {/* 第三行: 进度条 */}
                        <Progress
                          percent={item.progress}
                          status={item.status.includes('失败') && 'exception'}
                          strokeColor={item.status.includes('失败') ? undefined : {
                            '0%': '#108ee9',
                            '100%': '#87d068',
                          }}
                          strokeWidth={10}
                          showInfo={true}
                        />
                      </div>
                    </List.Item>
                  )
                }}
              />
            )
          ) : (
            <Empty description={t('importTask.emptyDesc')} />
          )}
        </div>
      </Card>
    </div>
  )
}
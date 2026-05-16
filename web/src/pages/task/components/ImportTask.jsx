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
import { useAtom } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { ResponsiveTable } from '@/components/ResponsiveTable'

export const ImportTask = () => {
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
      console.error('轮询获取数据失败:', error)
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
        message.error(`操作失败: ${error.message}`)
      }
    } else {
      try {
        await Promise.all(
          selectList.map(it => pauseTask({ taskId: it.taskId }))
        )
        refreshTasks()
        setSelectList([])
      } catch (error) {
        message.error(`操作失败: ${error.message}`)
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
          <div>您确定要中止任务吗？</div>
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
                强制中止
                <span className="text-gray-500 ml-1">
                  (直接标记为失败状态，适用于卡住的任务)
                </span>
              </span>
            </label>
            {force && (
              <div className="mt-2 text-xs text-orange-600">
                ⚠️ 强制中止将直接标记任务为失败状态
              </div>
            )}
          </div>
        </div>
      )
    }

    modalApi.confirm({
      title: '中止任务',
      content: <StopConfirmContent />,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await Promise.all(
            selectList.map(it => stopTask({ taskId: it.taskId, force: forceStop }))
          )
          refreshTasks()
          setSelectList([])
          messageApi.success(forceStop ? '强制中止成功' : '中止成功')
        } catch (error) {
          messageApi.error(`中止任务失败: ${error.message}`)
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
          <div>您确定要从历史记录中删除任务吗？</div>
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
                强制删除
                <span className="text-gray-500 ml-1">
                  (跳过中止逻辑，直接删除历史记录，适用于卡住的任务)
                </span>
              </span>
            </label>
            {force && (
              <div className="mt-2 text-xs text-orange-600">
                ⚠️ 强制删除将绕过正常的任务中止流程
              </div>
            )}
          </div>

          {hasStuckTasks && !force && (
            <div className="mt-3 p-2 bg-yellow-50 border border-yellow-200 rounded">
              <div className="text-sm text-yellow-700">
                💡 检测到运行中或暂停的任务，必须勾选"强制删除"才能删除
              </div>
            </div>
          )}
        </div>
      )
    }

    modalApi.confirm({
      title: '删除任务',
      content: <DeleteConfirmContent />,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          // 如果有卡住的任务但没有勾选强制删除，阻止执行
          if (hasStuckTasks && !forceDelete) {
            messageApi.warning('检测到运行中或暂停的任务，请勾选"强制删除"选项')
            return Promise.reject(new Error('需要强制删除'))
          }

          await Promise.all(
            selectList.map(it => deleteTask({ taskId: it.taskId, force: forceDelete }))
          )
          refreshTasks()
          setSelectList([])
          messageApi.success(forceDelete ? '强制删除成功' : '删除成功')
        } catch (error) {
          messageApi.error(`删除任务失败: ${error.message}`)
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
      messageApi.success(`已重新提交 ${succeeded} 个任务`)
    } else if (succeeded === 0) {
      messageApi.error(`${failed} 个任务重试失败`)
    } else {
      messageApi.warning(`${succeeded} 个任务已重新提交，${failed} 个失败`)
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
      { key: 'in_progress', label: '进行中' },
      { key: 'completed', label: '已完成' },
      { key: 'all', label: '全部' },
    ],
    onClick: ({ key }) => {
      navigate(`/task?search=${search}&status=${key}`, {
        replace: true,
      })
    },
  }

  const getStatusLabel = (status) => {
    switch (status) {
      case 'in_progress': return '进行中'
      case 'completed': return '已完成'
      case 'all': return '全部'
      default: return '进行中'
    }
  }

  // 队列类型筛选菜单
  const queueMenu = {
    items: [
      { key: 'all', label: '全部队列' },
      { key: 'download', label: '下载队列' },
      { key: 'management', label: '管理队列' },
      { key: 'fallback', label: '后备队列' },
    ],
    onClick: ({ key }) => {
      setQueueFilter(key)
      // 立即刷新任务列表，不等待轮询
      setTimeout(() => refreshTasks(), 0)
    },
  }

  const getQueueLabel = (queue) => {
    switch (queue) {
      case 'all': return '全部队列'
      case 'download': return '下载队列'
      case 'management': return '管理队列'
      case 'fallback': return '后备队列'
      default: return '全部队列'
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
                      ? '管理'
                      : item.queueType === 'fallback'
                        ? '后备'
                        : '下载'}
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
        title="任务管理器"
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
              <Tooltip title="全选/取消全选">
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
              <Tooltip title="启用/暂停任务">
                <Button
                  disabled={!canPause}
                  type="default"
                  shape="circle"
                  icon={isPause ? <PauseOutlined /> : <StepBackwardOutlined />}
                  onClick={handlePause}
                />
              </Tooltip>
              <Tooltip title="重试失败任务">
                <Button
                  disabled={!canRetry}
                  type="default"
                  shape="circle"
                  icon={<RetweetOutlined />}
                  onClick={handleRetry}
                />
              </Tooltip>
              <Tooltip title="删除任务">
                <Button
                  disabled={!canDelete}
                  type="default"
                  shape="circle"
                  icon={<DeleteOutlined />}
                  onClick={handleDelete}
                />
              </Tooltip>
              <Tooltip title="中止任务">
                <Button
                  disabled={!canStop}
                  type="default"
                  shape="circle"
                  icon={<StopOutlined />}
                  onClick={handleStop}
                />
              </Tooltip>
              <Input.Search
                placeholder="按任务标题搜索"
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
                  placeholder="搜索任务"
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
                }}>搜索</Button>
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
                  ? '取消全选'
                  : '全选'}
              </Button>
              <Button
                disabled={!canPause}
                icon={isPause ? <PauseOutlined /> : <StepBackwardOutlined />}
                onClick={handlePause}
                block
              >
                {isPause ? '继续' : '暂停'}
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
                重试
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
                删除
              </Button>
              <Button
                disabled={!canStop}
                danger
                icon={<StopOutlined />}
                onClick={handleStop}
                block
              >
                中止
              </Button>
            </div>

            {/* 选中任务提示 */}
            {selectList.length > 0 && (
              <div className="text-sm text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 p-2 rounded">
                已选择 {selectList.length} 个任务
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
                  showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`,
                  locale: {
                    items_per_page: '条/页',
                    jump_to: '跳至',
                    jump_to_confirm: '确定',
                    page: '页',
                    prev_page: '上一页',
                    next_page: '下一页',
                    prev_5: '向前 5 页',
                    next_5: '向后 5 页',
                    prev_3: '向前 3 页',
                    next_3: '向后 3 页',
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
                                ? '管理队列'
                                : item.queueType === 'fallback'
                                  ? '后备队列'
                                  : '下载队列'}
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
            <Empty description="没有符合条件的任务" />
          )}
        </div>
      </Card>
    </div>
  )
}
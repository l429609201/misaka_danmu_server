import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { List, Button, Tag, Space, Card, Empty, Tooltip, Input, Modal } from 'antd'
import { DeleteOutlined, CheckOutlined, MinusOutlined, PlayCircleOutlined, SearchOutlined, ClearOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useTranslation } from 'react-i18next'
import { getWebhookTasks, deleteWebhookTasks, runWebhookTasksNow, clearAllWebhookTasks } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useModal } from '../../../ModalContext'

const getStatusTagType = status => {
  if (status === 'pending') return 'processing'
  if (status === 'submitted') return 'success'
  if (status === 'failed') return 'error'
  return 'default'
}

const translateStatus = (status, t) => {
  const statusMap = {
    pending: t('webhookTasks.statusPending'),
    submitted: t('webhookTasks.statusSubmitted'),
    processing: t('webhookTasks.statusProcessing'),
    failed: t('webhookTasks.statusFailed'),
  }
  return statusMap[status] || status
}

export const WebhookTasks = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [taskList, setTaskList] = useState([])
  const [selectedTasks, setSelectedTasks] = useState([])
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: 20,
    total: 0,
  })
  const [searchTerm, setSearchTerm] = useState('')
  const [searchModalVisible, setSearchModalVisible] = useState(false)
  const [tempSearchTerm, setTempSearchTerm] = useState('')
  const [pollingInterval, setPollingInterval] = useState(30) // 默认30秒
  const [lastRefreshTime, setLastRefreshTime] = useState(null)
  const messageApi = useMessage()
  const modalApi = useModal()
  const pollingTimerRef = useRef(null)

  const fetchTasks = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await getWebhookTasks({
        search: searchTerm,
        page: pagination.current,
        pageSize: pagination.pageSize,
      })
      setTaskList(data.list || [])
      setPagination(prev => ({ ...prev, total: data.total || 0 }))
      setLastRefreshTime(new Date())
    } catch (error) {
      messageApi.error(t('webhookTasks.fetchFailed'))
    } finally {
      setLoading(false)
    }
  }, [messageApi, pagination.current, pagination.pageSize, searchTerm])

  useEffect(() => {
    fetchTasks()

    // 启动轮询
    const startPolling = () => {
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current)
      }
      pollingTimerRef.current = setInterval(() => {
        fetchTasks()
      }, pollingInterval * 1000)
    }

    startPolling()

    // 页面可见性变化处理
    const handleVisibilityChange = () => {
      if (document.hidden) {
        // 页面隐藏时停止轮询
        if (pollingTimerRef.current) {
          clearInterval(pollingTimerRef.current)
          pollingTimerRef.current = null
        }
      } else {
        // 页面恢复时重新启动轮询
        startPolling()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    // 清理函数
    return () => {
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current)
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [fetchTasks, pollingInterval])

  const handleSelectionChange = (task, checked) => {
    setSelectedTasks(prev =>
      checked ? [...prev, task] : prev.filter(t => t.id !== task.id)
    )
  }

  const handleSelectAll = () => {
    if (selectedTasks.length === taskList.length) {
      setSelectedTasks([])
    } else {
      setSelectedTasks(taskList)
    }
  }

  const handleBulkDelete = () => {
    modalApi.confirm({
      title: t('webhookTasks.bulkDeleteTitle'),
      content: t('webhookTasks.bulkDeleteContent', { count: selectedTasks.length }),
      onOk: async () => {
        try {
          const ids = selectedTasks.map(task => task.id)
          await deleteWebhookTasks({ ids })
          messageApi.success(t('webhookTasks.bulkDeleteSuccess'))
          setSelectedTasks([])
          fetchTasks()
        } catch (error) {
          messageApi.error(t('webhookTasks.bulkDeleteFailed'))
        }
      },
    })
  }

  const handleRunNow = () => {
    modalApi.confirm({
      title: t('webhookTasks.runNowTitle'),
      content: t('webhookTasks.runNowContent', { count: selectedTasks.length }),
      onOk: async () => {
        try {
          const ids = selectedTasks.map(task => task.id)
          await runWebhookTasksNow({ ids })
          messageApi.success(t('webhookTasks.runNowSuccess'))
          setSelectedTasks([])
          // 刷新列表以更新状态
          fetchTasks()
        } catch (error) {
          messageApi.error(t('webhookTasks.runNowFailed'))
        }
      },
    })
  }

  const handleClearAll = () => {
    modalApi.confirm({
      title: t('webhookTasks.clearAllTitle'),
      content: t('webhookTasks.clearAllContent', { count: pagination.total }),
      okType: 'danger',
      onOk: async () => {
        try {
          const { data } = await clearAllWebhookTasks()
          messageApi.success(data.message || t('webhookTasks.clearSuccess'))
          setSelectedTasks([])
          fetchTasks()
        } catch (error) {
          messageApi.error(t('webhookTasks.clearFailed'))
        }
      },
    })
  }

  const selectedTaskIds = useMemo(() => new Set(selectedTasks.map(t => t.id)), [
    selectedTasks,
  ])

  return (
    <div className="my-6">
      <Card
        loading={loading}
        extra={
          <Space>
            <Tooltip title={t('webhookTasks.selectAllTip')}>
              <Button
                type="default"
                shape="circle"
                icon={
                  selectedTasks.length === taskList.length &&
                  !!selectedTasks.length ? (
                    <CheckOutlined />
                  ) : (
                    <MinusOutlined />
                  )
                }
                onClick={handleSelectAll}
              />
            </Tooltip>
            <Tooltip title={t('webhookTasks.runSelectedTip')}>
              <Button
                type="primary"
                shape="circle"
                icon={<PlayCircleOutlined />}
                disabled={selectedTasks.length === 0}
                onClick={handleRunNow}
              />
            </Tooltip>
            <Tooltip title={t('webhookTasks.bulkDeleteTip')}>
              <Button
                danger
                type="primary"
                shape="circle"
                icon={<DeleteOutlined />}
                disabled={selectedTasks.length === 0}
                onClick={handleBulkDelete}
              />
            </Tooltip>
            <Tooltip title={t('webhookTasks.clearAllTip')}>
              <Button
                danger
                type="primary"
                shape="circle"
                icon={<ClearOutlined />}
                disabled={pagination.total === 0}
                onClick={handleClearAll}
              />
            </Tooltip>
            <Tooltip title={t('webhookTasks.searchTip')}>
              <Button
                type="default"
                shape="circle"
                icon={<SearchOutlined />}
                onClick={() => {
                  setTempSearchTerm(searchTerm)
                  setSearchModalVisible(true)
                }}
              />
            </Tooltip>
            <Tooltip title={t('webhookTasks.refreshTip')}>
              <Button
                type="default"
                shape="circle"
                icon={<ReloadOutlined />}
                loading={loading}
                onClick={() => fetchTasks()}
              />
            </Tooltip>
          </Space>
        }
        title={
          <div className="flex items-center justify-between">
            <span>{t('webhookTasks.title')}</span>
            {lastRefreshTime && (
              <span className="text-xs text-gray-400 font-normal ml-4">
                {t('webhookTasks.lastRefresh')}{dayjs(lastRefreshTime).format('HH:mm:ss')}
              </span>
            )}
          </div>
        }
      >
        <div>
          {taskList.length > 0 ? (
            <List
              itemLayout="vertical"
              size="small"
              dataSource={taskList}
              pagination={{
                ...pagination,
                align: 'center',
                showSizeChanger: true,
                pageSizeOptions: ['20', '50', '100'],
                onChange: (page, pageSize) => {
                  setPagination(prev => ({ ...prev, current: page, pageSize }))
                },
              }}
              renderItem={item => {
                const isSelected = selectedTaskIds.has(item.id)
                return (
                  <List.Item key={item.id} className="!p-0 !border-0 !mb-2">
                    {/* 圆角卡片条目，点击整体切换选中 */}
                    <div
                      onClick={() => handleSelectionChange(item, !isSelected)}
                      className={[
                        'relative w-full flex items-center justify-between px-4 py-3 rounded-xl cursor-pointer transition-all duration-150',
                        'border backdrop-blur-sm',
                        isSelected
                          ? 'border-blue-400 bg-blue-500/10 shadow-sm'
                          : 'border-white/40 bg-white/60 hover:border-blue-300/60 hover:bg-white/70',
                      ].join(' ')}
                    >
                      {/* 选中状态右上角打钩徽章 */}
                      {isSelected && (
                        <span className="absolute top-2 right-2 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center">
                          <CheckOutlined style={{ fontSize: 9, color: '#fff' }} />
                        </span>
                      )}
                      <div className="flex-1 min-w-0 pr-3">
                        <div className="text-base mb-1 truncate">{item.taskTitle}</div>
                        <div className="text-gray-500 text-sm flex flex-wrap gap-x-2">
                          <span>{t('webhookTasks.source')}{item.webhookSource}</span>
                          <span className="text-gray-300">|</span>
                          <span>
                            {t('webhookTasks.receivedAt')}{dayjs(item.receptionTime).format('YYYY-MM-DD HH:mm:ss')}
                          </span>
                          <span className="text-gray-300">|</span>
                          <span>
                            {t('webhookTasks.scheduledAt')}{dayjs(item.executeTime).format('YYYY-MM-DD HH:mm:ss')}
                          </span>
                        </div>
                      </div>
                      {/* 状态 tag 与右上角打钩不重叠，留出右侧空间 */}
                      <Tag color={getStatusTagType(item.status)} className={['shrink-0', isSelected ? 'mr-5' : ''].join(' ')}>
                        {translateStatus(item.status, t)}
                      </Tag>
                    </div>
                  </List.Item>
                )
              }}
            />
          ) : (
            <Empty description={t('webhookTasks.emptyDesc')} />
          )}
        </div>
      </Card>

      {/* 搜索模态框 */}
      <Modal
        title={t('webhookTasks.searchTitle')}
        open={searchModalVisible}
        onCancel={() => setSearchModalVisible(false)}
        onOk={() => {
          setSearchTerm(tempSearchTerm)
          setPagination(prev => ({ ...prev, current: 1 }))
          setSearchModalVisible(false)
        }}
        okText={t('webhookTasks.search')}
        cancelText={t('webhookTasks.cancel')}
      >
        <div className="py-4">
          <Input
            placeholder={t('webhookTasks.searchPlaceholder')}
            value={tempSearchTerm}
            onChange={(e) => setTempSearchTerm(e.target.value)}
            onPressEnter={() => {
              setSearchTerm(tempSearchTerm)
              setPagination(prev => ({ ...prev, current: 1 }))
              setSearchModalVisible(false)
            }}
            allowClear
            autoFocus
          />
          {searchTerm && (
            <div className="mt-2 text-sm text-gray-500">
              {t('webhookTasks.currentSearch')}"{searchTerm}"
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}
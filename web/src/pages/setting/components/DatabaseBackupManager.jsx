import { useState, useEffect } from 'react'
import { Button, Table, Space, Tag, Modal, Input, Alert, Spin, Popconfirm, Card, Empty, message } from 'antd'
import Cookies from 'js-cookie'
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  ReloadOutlined,
  CloudUploadOutlined,
  ExclamationCircleOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import {
  getBackupList,
  createBackup,
  downloadBackup,
  deleteBackup,
  deleteBackupBatch,
  restoreBackup,
  getBackupJobStatus,
  uploadBackup,
} from '../../../apis'

/**
 * 数据库备份管理组件
 * 用于在参数配置-数据库设置中显示
 */
export const DatabaseBackupManager = () => {
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const [backups, setBackups] = useState([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [jobStatus, setJobStatus] = useState(null)
  // 还原相关状态
  const [restoreModalVisible, setRestoreModalVisible] = useState(false)
  const [restoreTarget, setRestoreTarget] = useState(null)
  const [restoreConfirmText, setRestoreConfirmText] = useState('')
  const [restoring, setRestoring] = useState(false)
  // 上传相关状态
  const [uploadModalVisible, setUploadModalVisible] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    loadBackups()
    loadJobStatus()
  }, [])

  const loadBackups = async () => {
    try {
      setLoading(true)
      const res = await getBackupList()
      setBackups(res.data || [])
    } catch (err) {
      message.error('加载备份列表失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setLoading(false)
    }
  }

  const loadJobStatus = async () => {
    try {
      const res = await getBackupJobStatus()
      setJobStatus(res.data)
    } catch (err) {
      console.error('获取定时任务状态失败:', err)
    }
  }

  const handleCreate = async () => {
    try {
      setCreating(true)
      const res = await createBackup()
      message.success(res.data?.message || '备份创建成功')
      loadBackups()
    } catch (err) {
      message.error('创建备份失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (filename) => {
    try {
      await deleteBackup(filename)
      message.success('删除成功')
      loadBackups()
    } catch (err) {
      message.error('删除失败: ' + (err.response?.data?.detail || err.message))
    }
  }

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    try {
      const res = await deleteBackupBatch(selectedRowKeys)
      message.success(res.data?.message || '批量删除成功')
      setSelectedRowKeys([])
      loadBackups()
    } catch (err) {
      message.error('批量删除失败: ' + (err.response?.data?.detail || err.message))
    }
  }

  const handleDownload = (filename) => {
    const token = Cookies.get('danmu_token')
    const url = downloadBackup(filename)
    window.open(token ? `${url}?token=${encodeURIComponent(token)}` : url, '_blank')
  }

  const openRestoreModal = (record) => {
    setRestoreTarget(record)
    setRestoreConfirmText('')
    setRestoreModalVisible(true)
  }

  const handleRestore = async () => {
    if (restoreConfirmText !== '确认还原备份') {
      message.error('请输入「确认还原备份」以继续')
      return
    }
    try {
      setRestoring(true)
      const res = await restoreBackup({
        filename: restoreTarget.filename,
        confirm: 'RESTORE',
      })
      message.success(res.data?.message || '还原成功')
      setRestoreModalVisible(false)
      setRestoreConfirmText('')
    } catch (err) {
      message.error('还原失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setRestoring(false)
    }
  }

  // 打开上传弹窗
  const openUploadModal = () => {
    setUploadFile(null)
    setUploadModalVisible(true)
  }

  // 处理文件选择
  const handleFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.name.endsWith('.json.gz')) {
      message.error('请选择 .json.gz 格式的备份文件')
      e.target.value = ''
      return
    }
    setUploadFile(file)
  }

  // 执行上传
  const handleUpload = async () => {
    if (!uploadFile) {
      message.error('请先选择文件')
      return
    }
    try {
      setUploading(true)
      const res = await uploadBackup(uploadFile)
      message.success(res.data?.message || '上传成功')
      setUploadModalVisible(false)
      setUploadFile(null)
      loadBackups()
    } catch (err) {
      message.error('上传失败: ' + (err.response?.data?.detail || err.message))
    } finally {
      setUploading(false)
    }
  }

  const formatSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
  }

  const formatDate = (isoString) => {
    if (!isoString) return '-'
    const date = new Date(isoString)
    return date.toLocaleString('zh-CN')
  }

  const columns = [
    {
      title: '文件名',
      dataIndex: 'filename',
      key: 'filename',
      ellipsis: true,
    },
    {
      title: '数据库类型',
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => type ? <Tag color="blue">{type.toUpperCase()}</Tag> : '-',
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 100,
      render: (size) => formatSize(size),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time) => formatDate(time),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<CloudDownloadOutlined />}
            onClick={() => handleDownload(record.filename)}
            title="下载"
          />
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => openRestoreModal(record)}
            title="还原"
          />
          <Popconfirm
            title="确定删除此备份？"
            onConfirm={() => handleDelete(record.filename)}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />} title="删除" />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const rowSelection = {
    selectedRowKeys,
    onChange: setSelectedRowKeys,
  }

  const goToScheduledTasks = () => {
    navigate('/task?key=schedule')
  }

  return (
    <div className="mt-6 pt-6 border-t border-gray-200 dark:border-gray-700">
      <div className={`flex ${isMobile ? 'flex-col gap-3' : 'items-center justify-between'} mb-4`}>
        <h3 className="text-base font-medium">📦 数据库备份管理</h3>
        <Space wrap>
          <Button
            icon={<UploadOutlined />}
            onClick={openUploadModal}
          >
            上传备份
          </Button>
          <Button
            type="primary"
            icon={<CloudUploadOutlined />}
            onClick={handleCreate}
            loading={creating}
          >
            立即备份
          </Button>
        </Space>
      </div>

      {/* 定时任务状态 */}
      <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: 'var(--color-hover)' }}>
        {jobStatus?.exists ? (
          <div className={`flex ${isMobile ? 'flex-col gap-2' : 'items-center gap-2'}`}>
            <div className="flex items-center gap-2">
              <ClockCircleOutlined className="text-blue-500" />
              <span>定时备份:</span>
              {jobStatus.enabled ? (
                <Tag icon={<CheckCircleOutlined />} color="success">已启用</Tag>
              ) : (
                <Tag color="default">已暂停</Tag>
              )}
            </div>
            {jobStatus.enabled && (
              <span className="text-gray-500 dark:text-gray-400 text-sm">
                执行周期: {jobStatus.cron_expression}
                {jobStatus.next_run_time && ` | 下次执行: ${formatDate(jobStatus.next_run_time)}`}
              </span>
            )}
            <Button type="link" size="small" onClick={goToScheduledTasks}>
              前往配置 →
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
            <ClockCircleOutlined />
            <span>定时备份: 未配置</span>
            <Button type="link" size="small" onClick={goToScheduledTasks}>
              前往配置 →
            </Button>
          </div>
        )}
      </div>

      {/* 备份列表 */}
      <Spin spinning={loading}>
        {isMobile ? (
          backups.length === 0 ? (
            <Empty description="暂无备份文件" />
          ) : (
            <div className="space-y-3">
              {backups.map((record) => (
                <Card key={record.filename} size="small" className="shadow-sm">
                  <div className="space-y-2">
                    <div className="font-medium text-sm break-all">{record.filename}</div>
                    <div className="flex items-center gap-3 text-xs text-gray-500">
                      {record.db_type && <Tag color="blue" className="!text-xs">{record.db_type.toUpperCase()}</Tag>}
                      <span>{formatSize(record.size)}</span>
                      <span>{formatDate(record.created_at)}</span>
                    </div>
                    <div className="flex gap-2 pt-1 border-t border-gray-100 dark:border-gray-700">
                      <Button
                        type="link"
                        size="small"
                        icon={<CloudDownloadOutlined />}
                        onClick={() => handleDownload(record.filename)}
                      >
                        下载
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => openRestoreModal(record)}
                      >
                        还原
                      </Button>
                      <Popconfirm
                        title="确定删除此备份？"
                        onConfirm={() => handleDelete(record.filename)}
                      >
                        <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                          删除
                        </Button>
                      </Popconfirm>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )
        ) : (
          <Table
            rowKey="filename"
            columns={columns}
            dataSource={backups}
            rowSelection={rowSelection}
            size="small"
            pagination={false}
            locale={{ emptyText: '暂无备份文件' }}
          />
        )}
      </Spin>

      {/* 批量操作 */}
      {selectedRowKeys.length > 0 && (
        <div className="mt-3 flex items-center gap-4">
          <span className="text-gray-500 dark:text-gray-400">已选中 {selectedRowKeys.length} 项</span>
          <Popconfirm
            title={`确定删除选中的 ${selectedRowKeys.length} 个备份？`}
            onConfirm={handleBatchDelete}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>
              批量删除
            </Button>
          </Popconfirm>
          {selectedRowKeys.length === 1 && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => openRestoreModal(backups.find(b => b.filename === selectedRowKeys[0]))}
            >
              还原选中
            </Button>
          )}
        </div>
      )}

      {/* 还原确认弹窗 */}
      <Modal
        title={
          <span className="text-orange-500">
            <ReloadOutlined className="mr-2" />
            🔄 还原数据库备份
          </span>
        }
        open={restoreModalVisible}
        onCancel={() => {
          setRestoreModalVisible(false)
          setRestoreConfirmText('')
        }}
        footer={[
          <Button key="cancel" onClick={() => {
            setRestoreModalVisible(false)
            setRestoreConfirmText('')
          }}>
            取消
          </Button>,
          <Button
            key="confirm"
            type="primary"
            danger
            loading={restoring}
            disabled={restoreConfirmText !== '确认还原备份'}
            onClick={handleRestore}
          >
            确认还原
          </Button>,
        ]}
      >
        {restoreTarget && (
          <div>
            <p className="mb-3">您即将从备份文件还原数据库：</p>
            <div className="p-3 bg-gray-100 dark:bg-gray-800 rounded-lg mb-4 border border-gray-200 dark:border-gray-700">
              <div className="mb-1">📄 {restoreTarget.filename}</div>
              <div className="mb-1">📅 创建时间: {formatDate(restoreTarget.created_at)}</div>
              <div className="mb-1">📦 文件大小: {formatSize(restoreTarget.size)}</div>
              {restoreTarget.db_type && (
                <div>🗄️ 数据库类型: {restoreTarget.db_type.toUpperCase()}</div>
              )}
            </div>
            <Alert
              type="error"
              showIcon
              icon={<ExclamationCircleOutlined />}
              message="❌ 危险操作警告"
              description={
                <div>
                  <p>此操作将 <strong>完全覆盖</strong> 当前数据库中的所有数据！</p>
                  <p>还原后无法撤销，请确保您了解此操作的后果。</p>
                  <p className="mt-2 text-gray-500">建议：在还原前先创建一个当前数据库的备份。</p>
                </div>
              }
              className="mb-4"
            />
            <div>
              <p className="mb-2">请输入 「<strong>确认还原备份</strong>」 以继续：</p>
              <Input
                value={restoreConfirmText}
                onChange={(e) => setRestoreConfirmText(e.target.value)}
                placeholder="输入：确认还原备份"
                status={restoreConfirmText && restoreConfirmText !== '确认还原备份' ? 'error' : ''}
              />
            </div>
          </div>
        )}
      </Modal>

      {/* 上传备份弹窗 */}
      <Modal
        title={
          <span>
            <UploadOutlined className="mr-2" />
            上传备份文件
          </span>
        }
        open={uploadModalVisible}
        onCancel={() => {
          setUploadModalVisible(false)
          setUploadFile(null)
        }}
        footer={[
          <Button key="cancel" onClick={() => {
            setUploadModalVisible(false)
            setUploadFile(null)
          }}>
            取消
          </Button>,
          <Button
            key="confirm"
            type="primary"
            loading={uploading}
            disabled={!uploadFile}
            onClick={handleUpload}
          >
            确认上传
          </Button>,
        ]}
      >
        <div className="py-2">
          {/* 文件选择 */}
          <div className="mb-4">
            <p className="mb-2 font-medium">选择备份文件：</p>
            <input
              type="file"
              accept=".gz"
              onChange={handleFileSelect}
              className="block w-full text-sm text-gray-500
                file:mr-4 file:py-2 file:px-4
                file:rounded file:border-0
                file:text-sm file:font-semibold
                file:bg-blue-50 file:text-blue-700
                hover:file:bg-blue-100
                dark:file:bg-blue-900 dark:file:text-blue-200"
            />
          </div>

          {/* 选中文件信息 */}
          {uploadFile && (
            <div className="p-3 bg-gray-100 dark:bg-gray-800 rounded-lg mb-4 border border-gray-200 dark:border-gray-700">
              <div className="mb-1">📄 选中文件: {uploadFile.name}</div>
              <div>📦 文件大小: {formatSize(uploadFile.size)}</div>
            </div>
          )}

          <Alert
            type="info"
            showIcon
            message="提示"
            description="上传的备份文件将保存到服务器备份目录中，您可以随时使用该文件进行数据库还原。"
          />
        </div>
      </Modal>
    </div>
  )
}


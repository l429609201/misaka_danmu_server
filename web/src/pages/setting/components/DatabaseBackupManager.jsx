import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
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
      message.error(t('dbBackup.loadListFailed', { error: err.response?.data?.detail || err.message }))
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
      message.success(res.data?.message || t('dbBackup.createSuccess'))
      loadBackups()
    } catch (err) {
      message.error(t('dbBackup.createFailed', { error: err.response?.data?.detail || err.message }))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (filename) => {
    try {
      await deleteBackup(filename)
      message.success(t('dbBackup.deleteSuccess'))
      loadBackups()
    } catch (err) {
      message.error(t('dbBackup.deleteFailed', { error: err.response?.data?.detail || err.message }))
    }
  }

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    try {
      const res = await deleteBackupBatch(selectedRowKeys)
      message.success(res.data?.message || t('dbBackup.batchDeleteSuccess'))
      setSelectedRowKeys([])
      loadBackups()
    } catch (err) {
      message.error(t('dbBackup.batchDeleteFailed', { error: err.response?.data?.detail || err.message }))
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
    const confirmKeyword = t('dbBackup.restoreConfirmKeyword')
    if (restoreConfirmText !== confirmKeyword) {
      message.error(t('dbBackup.restoreConfirmRequired'))
      return
    }
    try {
      setRestoring(true)
      const res = await restoreBackup({
        filename: restoreTarget.filename,
        confirm: 'RESTORE',
      })
      message.success(res.data?.message || t('dbBackup.restoreSuccess'))
      setRestoreModalVisible(false)
      setRestoreConfirmText('')
    } catch (err) {
      message.error(t('dbBackup.restoreFailed', { error: err.response?.data?.detail || err.message }))
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
      message.error(t('dbBackup.fileFormatError'))
      e.target.value = ''
      return
    }
    setUploadFile(file)
  }

  // 执行上传
  const handleUpload = async () => {
    if (!uploadFile) {
      message.error(t('dbBackup.selectFileFirst'))
      return
    }
    try {
      setUploading(true)
      const res = await uploadBackup(uploadFile)
      message.success(res.data?.message || t('dbBackup.uploadSuccess'))
      setUploadModalVisible(false)
      setUploadFile(null)
      loadBackups()
    } catch (err) {
      message.error(t('dbBackup.uploadFailed', { error: err.response?.data?.detail || err.message }))
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
      title: t('dbBackup.colFilename'),
      dataIndex: 'filename',
      key: 'filename',
      ellipsis: true,
    },
    {
      title: t('dbBackup.colDbType'),
      dataIndex: 'db_type',
      key: 'db_type',
      width: 100,
      render: (type) => type ? <Tag color="blue">{type.toUpperCase()}</Tag> : '-',
    },
    {
      title: t('dbBackup.colSize'),
      dataIndex: 'size',
      key: 'size',
      width: 100,
      render: (size) => formatSize(size),
    },
    {
      title: t('dbBackup.colCreatedAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time) => formatDate(time),
    },
    {
      title: t('dbBackup.colAction'),
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<CloudDownloadOutlined />}
            onClick={() => handleDownload(record.filename)}
            title={t('dbBackup.btnDownload')}
          />
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => openRestoreModal(record)}
            title={t('dbBackup.btnRestore')}
          />
          <Popconfirm
            title={t('dbBackup.confirmDeleteOne')}
            onConfirm={() => handleDelete(record.filename)}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />} title={t('dbBackup.btnDelete')} />
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
        <h3 className="text-base font-medium">{t('dbBackup.title')}</h3>
        <Space wrap>
          <Button
            icon={<UploadOutlined />}
            onClick={openUploadModal}
          >
            {t('dbBackup.btnUpload')}
          </Button>
          <Button
            type="primary"
            icon={<CloudUploadOutlined />}
            onClick={handleCreate}
            loading={creating}
          >
            {t('dbBackup.btnCreateNow')}
          </Button>
        </Space>
      </div>

      {/* 定时任务状态 */}
      <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: 'var(--color-hover)' }}>
        {jobStatus?.exists ? (
          <div className={`flex ${isMobile ? 'flex-col gap-2' : 'items-center gap-2'}`}>
            <div className="flex items-center gap-2">
              <ClockCircleOutlined className="text-blue-500" />
              <span>{t('dbBackup.labelScheduledBackup')}</span>
              {jobStatus.enabled ? (
                <Tag icon={<CheckCircleOutlined />} color="success">{t('dbBackup.tagEnabled')}</Tag>
              ) : (
                <Tag color="default">{t('dbBackup.tagPaused')}</Tag>
              )}
            </div>
            {jobStatus.enabled && (
              <span className="text-gray-500 dark:text-gray-400 text-sm">
                {t('dbBackup.labelCron')}{jobStatus.cron_expression}
                {jobStatus.next_run_time && `${t('dbBackup.labelNextRun')}${formatDate(jobStatus.next_run_time)}`}
              </span>
            )}
            <Button type="link" size="small" onClick={goToScheduledTasks}>
              {t('dbBackup.btnGoConfig')}
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
            <ClockCircleOutlined />
            <span>{t('dbBackup.labelNotConfigured')}</span>
            <Button type="link" size="small" onClick={goToScheduledTasks}>
              {t('dbBackup.btnGoConfig')}
            </Button>
          </div>
        )}
      </div>

      {/* 备份列表 */}
      <Spin spinning={loading}>
        {isMobile ? (
          backups.length === 0 ? (
            <Empty description={t('dbBackup.emptyBackups')} />
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
                        {t('dbBackup.btnDownload')}
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => openRestoreModal(record)}
                      >
                        {t('dbBackup.btnRestore')}
                      </Button>
                      <Popconfirm
                        title={t('dbBackup.confirmDeleteOne')}
                        onConfirm={() => handleDelete(record.filename)}
                      >
                        <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                          {t('dbBackup.btnDelete')}
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
            locale={{ emptyText: t('dbBackup.emptyBackups') }}
          />
        )}
      </Spin>

      {/* 批量操作 */}
      {selectedRowKeys.length > 0 && (
        <div className="mt-3 flex items-center gap-4">
          <span className="text-gray-500 dark:text-gray-400">{t('dbBackup.selectedCount', { count: selectedRowKeys.length })}</span>
          <Popconfirm
            title={t('dbBackup.confirmDeleteBatch', { count: selectedRowKeys.length })}
            onConfirm={handleBatchDelete}
          >
            <Button danger size="small" icon={<DeleteOutlined />}>
              {t('dbBackup.btnBatchDelete')}
            </Button>
          </Popconfirm>
          {selectedRowKeys.length === 1 && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => openRestoreModal(backups.find(b => b.filename === selectedRowKeys[0]))}
            >
              {t('dbBackup.btnRestoreSelected')}
            </Button>
          )}
        </div>
      )}

      {/* 还原确认弹窗 */}
      <Modal
        title={
          <span className="text-orange-500">
            <ReloadOutlined className="mr-2" />
            {t('dbBackup.restoreModalTitle')}
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
            {t('dbBackup.btnCancel')}
          </Button>,
          <Button
            key="confirm"
            type="primary"
            danger
            loading={restoring}
            disabled={restoreConfirmText !== t('dbBackup.restoreConfirmKeyword')}
            onClick={handleRestore}
          >
            {t('dbBackup.btnConfirmRestore')}
          </Button>,
        ]}
      >
        {restoreTarget && (
          <div>
            <p className="mb-3">{t('dbBackup.restoreDesc')}</p>
            <div className="p-3 bg-gray-100 dark:bg-gray-800 rounded-lg mb-4 border border-gray-200 dark:border-gray-700">
              <div className="mb-1">📄 {restoreTarget.filename}</div>
              <div className="mb-1">{t('dbBackup.labelCreatedAt')}{formatDate(restoreTarget.created_at)}</div>
              <div className="mb-1">{t('dbBackup.labelFileSize')}{formatSize(restoreTarget.size)}</div>
              {restoreTarget.db_type && (
                <div>{t('dbBackup.labelDbType')}{restoreTarget.db_type.toUpperCase()}</div>
              )}
            </div>
            <Alert
              type="error"
              showIcon
              icon={<ExclamationCircleOutlined />}
              message={t('dbBackup.alertDangerTitle')}
              description={
                <div>
                  <p>{t('dbBackup.alertDangerDesc1')}</p>
                  <p>{t('dbBackup.alertDangerDesc2')}</p>
                  <p className="mt-2 text-gray-500">{t('dbBackup.alertDangerDesc3')}</p>
                </div>
              }
              className="mb-4"
            />
            <div>
              <p className="mb-2">{t('dbBackup.confirmInputHint')}</p>
              <Input
                value={restoreConfirmText}
                onChange={(e) => setRestoreConfirmText(e.target.value)}
                placeholder={t('dbBackup.confirmInputPlaceholder')}
                status={restoreConfirmText && restoreConfirmText !== t('dbBackup.restoreConfirmKeyword') ? 'error' : ''}
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
            {t('dbBackup.uploadModalTitle')}
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
            {t('dbBackup.btnCancel')}
          </Button>,
          <Button
            key="confirm"
            type="primary"
            loading={uploading}
            disabled={!uploadFile}
            onClick={handleUpload}
          >
            {t('dbBackup.btnConfirmUpload')}
          </Button>,
        ]}
      >
        <div className="py-2">
          {/* 文件选择 */}
          <div className="mb-4">
            <p className="mb-2 font-medium">{t('dbBackup.labelSelectFile')}</p>
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
              <div className="mb-1">{t('dbBackup.labelSelectedFile')}{uploadFile.name}</div>
              <div>{t('dbBackup.labelFileSize')}{formatSize(uploadFile.size)}</div>
            </div>
          )}

          <Alert
            type="info"
            showIcon
            message={t('dbBackup.alertUploadTitle')}
            description={t('dbBackup.alertUploadDesc')}
          />
        </div>
      </Modal>
    </div>
  )
}


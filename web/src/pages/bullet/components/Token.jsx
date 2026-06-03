import {
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Space,
  Progress,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import {
  addToken,
  deleteToken,
  editToken,
  getTokenList,
  getTokenLog,
  resetTokenCounter,
  toggleTokenStatus,
} from '../../../apis'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon.jsx'
import copy from 'copy-to-clipboard'
import { EyeInvisibleOutlined, EyeTwoTone } from '@ant-design/icons'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import { ResponsiveTable } from '@/components/ResponsiveTable'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { useTranslation } from 'react-i18next'

export const Token = ({ domain }) => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [tokenList, setTokenList] = useState([])
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editingRecord, setEditingRecord] = useState(null)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()
  const [tokenLogs, setTokenLogs] = useState([])
  const [logsOpen, setLogsOpen] = useState(false)
  const modalApi = useModal()
  const messageApi = useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  const getTokens = async () => {
    try {
      setLoading(true)
      const tokenRes = await getTokenList()
      setTokenList(tokenRes.data)
    } catch (error) {
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  const handleTokenLogs = async record => {
    try {
      const res = await getTokenLog({
        tokenId: record.id,
      })
      setTokenLogs(res.data)
      setLogsOpen(true)
    } catch (error) {
      messageApi.error(t('bullet.tokenGetLogFailed'))
    }
  }

  const handleToggleStatus = async record => {
    try {
      await toggleTokenStatus({
        tokenId: record.id,
      })
      getTokens()
    } catch (error) {
      messageApi.error(t('bullet.tokenOperationFailed'))
    }
  }

  const handleDelete = record => {
    modalApi.confirm({
      title: t('bullet.tokenDeleteTitle'),
      zIndex: 1002,
      content: <Typography.Text>{t('bullet.tokenDeleteConfirm', { name: record.name })}</Typography.Text>,
      okText: t('bullet.tokenConfirm'),
      cancelText: t('bullet.tokenCancel'),
      onOk: async () => {
        try {
          await deleteToken({
            tokenId: record.id,
          })
          getTokens()
          messageApi.success(t('bullet.tokenDeleteSuccess'))
        } catch (error) {
          console.error(error)
          messageApi.error(t('bullet.tokenDeleteFailed'))
        }
      },
    })
  }

  const handleOpenModal = (editing = false, record = null) => {
    setIsEditing(editing)
    setEditingRecord(record)
    if (editing && record) {
      form.setFieldsValue({
        name: record.name,
        dailyCallLimit: record.dailyCallLimit,
        validityPeriod: 'custom', // 默认不改变有效期
      })
    } else {
      form.resetFields()
      form.setFieldsValue({
        validityPeriod: 'permanent',
        dailyCallLimit: 500,
      })
    }
    setIsModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      // 空字符串的 customToken 转为 null，避免后端校验失败
      if (!values.customToken?.trim()) {
        values.customToken = null
      }
      setConfirmLoading(true)
      if (isEditing && editingRecord) {
        await editToken({ ...values, id: editingRecord.id })
        messageApi.success(t('bullet.tokenEditSuccess'))
      } else {
        await addToken(values)
        messageApi.success(t('bullet.tokenAddSuccess'))
      }
      setIsModalOpen(false)
      getTokens()
    } catch (error) {
      messageApi.error(error?.detail || t('bullet.tokenOperationFailed'))
    } finally {
      setConfirmLoading(false)
    }
  }

  const handleResetCounter = async () => {
    if (!editingRecord) return
    try {
      await resetTokenCounter({ id: editingRecord.id })
      messageApi.success(t('bullet.tokenResetSuccess'))
      setIsModalOpen(false)
      getTokens()
    } catch (error) {
      messageApi.error(t('bullet.tokenResetFailed'))
    }
  }

  useEffect(() => {
    getTokens()
  }, [])

  const columns = [
    {
      title: t('bullet.tokenColumnName'),
      dataIndex: 'name',
      key: 'name',
      width: 100,
    },
    {
      title: 'Token',
      dataIndex: 'token',
      key: 'token',
      width: 200,
      render: (_, record) => {
        return (
          <Input.Password
            value={record.token}
            readOnly
            iconRender={visible =>
              visible ? <EyeTwoTone /> : <EyeInvisibleOutlined />
            }
          />
        )
      },
    },
    {
      title: t('bullet.tokenColumnStatus'),
      width: 150,
      dataIndex: 'isEnabled',
      key: 'isEnabled',
      render: (_, record) => {
        if (!record.isEnabled) {
          return <Tag color="red">{t('bullet.tokenStatusDisabled')}</Tag>
        }

        const isInfinite = record.dailyCallLimit === -1
        const percent = isInfinite
          ? 0
          : Math.round(
              (record.dailyCallCount / record.dailyCallLimit) * 100
            )
        const limitText = isInfinite ? '∞' : record.dailyCallLimit

        return (
          <Space size="small" align="center">
            <Progress
              percent={percent}
              size="small"
              showInfo={false}
              status={isInfinite ? 'normal' : 'normal'}
              strokeColor={isInfinite ? '#1677ff' : undefined}
              className="!w-[60px]"
            />
            <span style={{ minWidth: '50px', display: 'inline-block' }}>
              {record.dailyCallCount} / {limitText}
            </span>
          </Space>
        )
      },
    },
    {
      title: t('bullet.tokenColumnCreated'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 180,
      render: (_, record) => {
        return (
          <Typography.Text>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</Typography.Text>
        )
      },
    },
    {
      title: t('bullet.tokenColumnValidity'),
      dataIndex: 'expiresAt',
      key: 'expiresAt',
      width: 180,
      render: (_, record) => {
        return (
          <Typography.Text>
            {!!record.expiresAt
              ? dayjs(record.expiresAt).format('YYYY-MM-DD HH:mm:ss')
              : t('bullet.tokenValidityPermanent')}
          </Typography.Text>
        )
      },
    },
    {
      title: t('bullet.tokenColumnAction'),
      width: 160,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title={t('bullet.tokenTipEdit')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => handleOpenModal(true, record)}
              >
                <MyIcon icon="edit" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title={t('bullet.tokenTipCopy')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  copy(
                    `${domain || window.location.origin}/api/v1/${record.token}`
                  )
                  messageApi.success(t('bullet.tokenCopySuccess'))
                }}
              >
                <MyIcon icon="copy" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title={t('bullet.tokenTipLog')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => handleTokenLogs(record)}
              >
                <MyIcon icon="rizhi" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title={t('bullet.tokenTipToggle')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  handleToggleStatus(record)
                }}
              >
                <div>
                  {record.isEnabled ? (
                    <MyIcon icon="pause" size={20}></MyIcon>
                  ) : (
                    <MyIcon icon="start" size={20}></MyIcon>
                  )}
                </div>
              </span>
            </Tooltip>
            <Tooltip title={t('bullet.tokenTipDelete')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => handleDelete(record)}
              >
                <MyIcon icon="delete" size={20}></MyIcon>
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  // JSON 格式化：尝试解析并美化，同时解码 Unicode 转义
  const formatContent = (raw) => {
    if (!raw) return raw
    try {
      return JSON.stringify(JSON.parse(raw), null, 2)
    } catch {
      return raw
    }
  }

  // 请求/响应详情展开面板（与外部控制日志一致）
  const DetailBlock = ({ label, content }) => {
    if (!content) return null
    return (
      <div className="mb-3">
        <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">{label}</div>
        <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto m-0">{formatContent(content)}</pre>
      </div>
    )
  }

  const TokenLogDetailPanel = ({ log }) => {
    const hasRequest = log.requestHeaders || log.requestBody
    const hasResponse = log.responseHeaders || log.responseBody
    if (!hasRequest && !hasResponse) {
      return <div className="text-xs text-gray-400 py-2">{t('bullet.tokenLogEmpty')}</div>
    }
    const items = []
    if (hasRequest) {
      items.push({
        key: 'request',
        label: t('bullet.tokenLogRequestInfo'),
        children: (
          <div>
            {log.method && <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">{t('bullet.tokenLogMethod')}: <Tag color="blue" size="small">{log.method}</Tag></div>}
            <DetailBlock label={t('bullet.tokenLogRequestHeaders')} content={log.requestHeaders} />
            <DetailBlock label={t('bullet.tokenLogRequestBody')} content={log.requestBody} />
          </div>
        ),
      })
    }
    if (hasResponse) {
      items.push({
        key: 'response',
        label: t('bullet.tokenLogResponseInfo'),
        children: (
          <div>
            {log.statusCode && <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">{t('bullet.tokenLogStatusCode')}: <Tag color={log.statusCode >= 400 ? 'red' : 'green'}>{log.statusCode}</Tag></div>}
            <DetailBlock label={t('bullet.tokenLogResponseHeaders')} content={log.responseHeaders} />
            <DetailBlock label={t('bullet.tokenLogResponseBody')} content={log.responseBody} />
          </div>
        ),
      })
    }
    return <Collapse size="small" items={items} />
  }

  const logsColumns = [
    {
      title: t('bullet.tokenLogColumnTime'),
      dataIndex: 'accessTime',
      key: 'accessTime',
      width: 180,
      render: (_, record) => dayjs(record.accessTime).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('bullet.tokenLogColumnIp'),
      dataIndex: 'ipAddress',
      key: 'ipAddress',
      width: 150,
    },
    {
      title: t('bullet.tokenLogColumnMethod'),
      dataIndex: 'method',
      key: 'method',
      width: 70,
      render: (_, record) => record.method ? <Tag color="blue">{record.method}</Tag> : '-',
    },
    {
      title: t('bullet.tokenLogColumnStatus'),
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (_, record) => {
        const isAllowed = record.status === 'allowed'
        return <Tag color={isAllowed ? 'success' : 'error'}>{record.status}</Tag>
      },
    },
    {
      title: t('bullet.tokenLogColumnPath'),
      dataIndex: 'path',
      key: 'path',
      width: 250,
      render: (_, record) => (
        <Typography.Text code className="text-xs break-all">
          {record.path}
        </Typography.Text>
      ),
    },
    {
      title: 'User-Agent',
      dataIndex: 'userAgent',
      key: 'userAgent',
      width: 200,
      ellipsis: true,
      render: (_, record) => (
        <span className="text-gray-600 dark:text-gray-400 text-xs">
          {record.userAgent}
        </span>
      ),
    },
  ]

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title={t('bullet.tokenCardTitle')}
        extra={
          <>
            <Button type="primary" onClick={() => handleOpenModal(false)}>
              {t('bullet.tokenAddBtn')}
            </Button>
          </>
        }
      >
        <ResponsiveTable
          pagination={false}
          size="small"
          dataSource={tokenList}
          columns={columns}
          rowKey={'id'}
          scroll={{ x: '100%' }}
          renderCard={(record) => {
            const isEnabled = record.isEnabled;
            const isInfinite = record.dailyCallLimit === -1;
            const percent = isInfinite
              ? 0
              : Math.round(
                  (record.dailyCallCount / record.dailyCallLimit) * 100
                );
            const limitText = isInfinite ? '∞' : record.dailyCallLimit;

            return (
              <div className="space-y-3">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="font-bold text-base mb-2">{record.name}</div>
                    <div className="text-sm space-y-1">
                      <div className="flex items-center gap-2">
                        {isEnabled ? (
                          <Tag color="green">{t('bullet.tokenStatusEnabled')}</Tag>
                        ) : (
                          <Tag color="red">{t('bullet.tokenStatusDisabled')}</Tag>
                        )}
                      </div>
                                            <div className="text-gray-600 dark:text-gray-400">
                        Token: <Input.Password
                          value={record.token}
                          readOnly
                          bordered={false}
                          style={{ padding: 0, background: 'transparent' }}
                          iconRender={visible =>
                            visible ? <EyeTwoTone /> : <EyeInvisibleOutlined />
                          }
                        />
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {t('bullet.tokenMobileCreated')}: {dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {t('bullet.tokenMobileValidity')}: {!!record.expiresAt
                          ? dayjs(record.expiresAt).format('YYYY-MM-DD HH:mm:ss')
                          : t('bullet.tokenValidityPermanent')}
                      </div>
                      {isEnabled && (
                        <div>
                          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">
                            {t('bullet.tokenMobileTodayCall')}: {record.dailyCallCount} / {limitText}
                          </div>
                          <Progress percent={percent} size="small" />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                  <Button
                    size="small"
                    icon={<MyIcon icon="edit" size={16} />}
                    onClick={() => handleOpenModal(true, record)}
                  >
                    {t('bullet.tokenEditBtn')}
                  </Button>
                  <Button
                    size="small"
                    icon={<MyIcon icon="copy" size={16} />}
                    onClick={() => {
                      copy(
                        `${domain || window.location.origin}/api/v1/${record.token}`
                      )
                      messageApi.success(t('bullet.tokenCopySuccess'))
                    }}
                  >
                    {t('bullet.tokenCopyBtn')}
                  </Button>
                  <Button
                    size="small"
                    icon={<MyIcon icon="rizhi" size={16} />}
                    onClick={() => handleTokenLogs(record)}
                  >
                    {t('bullet.tokenLogBtn')}
                  </Button>
                  <Button
                    size="small"
                    icon={isEnabled ? <MyIcon icon="pause" size={16} /> : <MyIcon icon="start" size={16} />}
                    onClick={() => handleToggleStatus(record)}
                  >
                    {isEnabled ? t('bullet.tokenStatusDisabled') : t('bullet.tokenStatusEnabled')}
                  </Button>
                  <Button
                    size="small"
                    danger
                    icon={<MyIcon icon="delete" size={16} />}
                    onClick={() => handleDelete(record)}
                  >
                    {t('bullet.tokenDeleteBtn')}
                  </Button>
                </div>
              </div>
            )
          }}
        />
      </Card>
      <Modal
        title={isEditing ? t('bullet.tokenModalEditTitle') : t('bullet.tokenModalAddTitle')}
        open={isModalOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText={t('bullet.tokenCancel')}
        okText={t('bullet.tokenConfirm')}
        onCancel={() => setIsModalOpen(false)}
        footer={
          <div className="flex justify-between">
            <div>
              {isEditing && (
                <Button danger onClick={handleResetCounter}>
                  {t('bullet.tokenResetCounter')}
                </Button>
              )}
            </div>
            <div>
              <Button onClick={() => setIsModalOpen(false)}>{t('bullet.tokenCancel')}</Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={confirmLoading}
              >
                {t('bullet.tokenConfirm')}
              </Button>
            </div>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t('bullet.tokenFieldName')}
            rules={[{ required: true, message: t('bullet.tokenNameRequired') }]}
            className="mb-4"
          >
            <Input placeholder={t('bullet.tokenNamePlaceholder')} />
          </Form.Item>
          <Form.Item
            name="validityPeriod"
            label={t('bullet.tokenFieldValidity')}
            rules={[{ required: true, message: t('bullet.tokenValidityRequired') }]}
            className="mb-4"
          >
            <Select
              options={[
                isEditing && { value: 'custom', label: t('bullet.tokenValidityCustom') },
                { value: 'permanent', label: t('bullet.tokenValidityPermanent') },
                { value: '1d', label: t('bullet.tokenValidity1d') },
                { value: '7d', label: t('bullet.tokenValidity7d') },
                { value: '30d', label: t('bullet.tokenValidity30d') },
                { value: '180d', label: t('bullet.tokenValidity180d') },
                { value: '365d', label: t('bullet.tokenValidity365d') },
              ].filter(Boolean)}
            />
          </Form.Item>
          <Form.Item
            name="dailyCallLimit"
            label={t('bullet.tokenFieldDailyLimit')}
            tooltip={t('bullet.tokenDailyLimitTip')}
            className="mb-4"
          >
            <InputNumber
              min={-1}
              style={{ width: '100%' }}
              placeholder={t('bullet.tokenDailyLimitPlaceholder')}
            />
          </Form.Item>
          <Form.Item
            name="customToken"
            label={t('bullet.tokenFieldCustomToken')}
            tooltip={t('bullet.tokenCustomTokenTip')}
            className="mb-4"
            rules={[
              {
                pattern: /^[a-zA-Z0-9_-]*$/,
                message: t('bullet.tokenCustomTokenPattern'),
              },
              {
                validator: (_, value) => {
                  if (value && value.length > 0 && value.length < 5) {
                    return Promise.reject(t('bullet.tokenCustomTokenMinLen'))
                  }
                  return Promise.resolve()
                },
              },
            ]}
          >
            <Input
              placeholder={isEditing ? t('bullet.tokenCustomTokenEditPlaceholder') : t('bullet.tokenCustomTokenAddPlaceholder')}
              maxLength={100}
              allowClear
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={
          <div className="flex items-center gap-3">
            <Typography.Text>{t('bullet.tokenLogModalTitle')}</Typography.Text>
            <Tag color="blue">{t('bullet.tokenLogCount', { count: tokenLogs.length })}</Tag>
          </div>
        }
        width={isMobile ? '100%' : '90vw'}
        style={isMobile ? {} : { maxWidth: 1400 }}
        open={logsOpen}
        cancelText={t('bullet.tokenCancel')}
        okText={t('bullet.tokenConfirm')}
        onCancel={() => setLogsOpen(false)}
        onOk={() => setLogsOpen(false)}
        styles={isMobile ? { body: { height: 'calc(100vh - 200px)' } } : { body: { maxHeight: '70vh', overflow: 'auto' } }}
        className="modern-modal"
      >
        {isMobile ? (
          <div className="space-y-4">
            {tokenLogs.map((log, index) => {
              const isAllowed = log.status?.toLowerCase().includes('allowed');
              return (
                <Card
                  key={index}
                  size="small"
                  className="hover:shadow-lg transition-shadow duration-300"
                  bodyStyle={{ padding: '12px' }}
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${isAllowed ? 'bg-green-500' : 'bg-red-500'}`} />
                        <span className="text-sm font-medium">
                          {dayjs(log.accessTime).format('MM-DD HH:mm:ss')}
                        </span>
                      </div>
                      <Tag color={isAllowed ? 'success' : 'error'}>
                        {log.status}
                      </Tag>
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center gap-3">
                        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0">IP:</span>
                        <Typography.Text code className="text-sm font-mono">
                          {log.ipAddress}
                        </Typography.Text>
                      </div>
                      <div className="flex items-start gap-3">
                        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0 mt-1">{t('bullet.tokenLogPath')}:</span>
                        <Typography.Text code className="text-xs break-all flex-1">
                          {log.path}
                        </Typography.Text>
                      </div>
                      {log.userAgent && (
                        <div className="flex items-start gap-3">
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0 mt-1">UA:</span>
                          <Typography.Text code className="text-xs break-all flex-1">
                            {log.userAgent}
                          </Typography.Text>
                        </div>
                      )}
                      {log.method && (
                        <div className="flex items-center gap-3">
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0">{t('bullet.tokenLogMethodLabel')}:</span>
                          <Tag color="blue" size="small">{log.method}</Tag>
                        </div>
                      )}
                      {log.requestBody && (
                        <div className="flex items-start gap-3">
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0 mt-1">{t('bullet.tokenLogRequest')}:</span>
                          <Typography.Text code className="text-xs break-all flex-1" style={{ maxHeight: 80, overflow: 'auto' }}>
                            {log.requestBody}
                          </Typography.Text>
                        </div>
                      )}
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <Table
            pagination={false}
            size="small"
            dataSource={tokenLogs}
            columns={logsColumns}
            rowKey={'accessTime'}
            expandable={{
              expandedRowRender: (record) => <TokenLogDetailPanel log={record} />,
              rowExpandable: (record) => !!(record.requestHeaders || record.requestBody || record.responseHeaders || record.responseBody),
            }}
            scroll={{
              x: '100%',
              y: 400,
            }}
            className="modern-table"
          />
        )}
      </Modal>
    </div>
  )
}

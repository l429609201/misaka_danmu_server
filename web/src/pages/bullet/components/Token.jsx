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

export const Token = ({ domain }) => {
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
      messageApi.error('获取日志失败')
    }
  }

  const handleToggleStatus = async record => {
    try {
      await toggleTokenStatus({
        tokenId: record.id,
      })
      getTokens()
    } catch (error) {
      messageApi.error('操作失败')
    }
  }

  const handleDelete = record => {
    modalApi.confirm({
      title: '删除',
      zIndex: 1002,
      content: <Typography.Text>您确定要删除{record.name}吗？</Typography.Text>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteToken({
            tokenId: record.id,
          })
          getTokens()
          messageApi.success('删除成功')
        } catch (error) {
          console.error(error)
          messageApi.error('删除失败')
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
        messageApi.success('编辑成功')
      } else {
        await addToken(values)
        messageApi.success('添加成功')
      }
      setIsModalOpen(false)
      getTokens()
    } catch (error) {
      messageApi.error(error?.detail || '操作失败')
    } finally {
      setConfirmLoading(false)
    }
  }

  const handleResetCounter = async () => {
    if (!editingRecord) return
    try {
      await resetTokenCounter({ id: editingRecord.id })
      messageApi.success('调用次数已重置为0')
      setIsModalOpen(false)
      getTokens()
    } catch (error) {
      messageApi.error('重置失败')
    }
  }

  useEffect(() => {
    getTokens()
  }, [])

  const columns = [
    {
      title: '名称',
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
      title: '状态',
      width: 150,
      dataIndex: 'isEnabled',
      key: 'isEnabled',
      render: (_, record) => {
        if (!record.isEnabled) {
          return <Tag color="red">禁用</Tag>
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
      title: '创建时间',
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
      title: '有效期',
      dataIndex: 'expiresAt',
      key: 'expiresAt',
      width: 180,
      render: (_, record) => {
        return (
          <Typography.Text>
            {!!record.expiresAt
              ? dayjs(record.expiresAt).format('YYYY-MM-DD HH:mm:ss')
              : '永久'}
          </Typography.Text>
        )
      },
    },
    {
      title: '操作',
      width: 160,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title="编辑">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => handleOpenModal(true, record)}
              >
                <MyIcon icon="edit" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="复制">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  copy(
                    `${domain || window.location.origin}/api/v1/${record.token}`
                  )
                  messageApi.success('复制成功')
                }}
              >
                <MyIcon icon="copy" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="Token访问日志">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => handleTokenLogs(record)}
              >
                <MyIcon icon="rizhi" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="切换启用状态">
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
            <Tooltip title="删除Token">
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

  // 请求详情展开面板（复用外部控制日志样式）
  const TokenLogDetailPanel = ({ log }) => {
    if (!log.requestBody && !log.responseBody) {
      return <div className="text-xs text-gray-400 py-2">暂无详细请求/响应记录</div>
    }
    const items = []
    if (log.requestBody) {
      items.push({
        key: 'request',
        label: '📤 请求信息',
        children: (
          <div>
            {log.method && <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">方法: <Tag color="blue" size="small">{log.method}</Tag></div>}
            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">请求内容</div>
            <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto m-0">
              {log.requestBody}
            </pre>
          </div>
        ),
      })
    }
    if (log.responseBody) {
      items.push({
        key: 'response',
        label: '📥 响应信息',
        children: (
          <div>
            {log.statusCode && <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">状态码: <Tag color={log.statusCode >= 400 ? 'red' : 'green'}>{log.statusCode}</Tag></div>}
            <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">响应内容</div>
            <pre className="text-xs bg-gray-50 dark:bg-gray-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-[200px] overflow-y-auto m-0">
              {log.responseBody}
            </pre>
          </div>
        ),
      })
    }
    return <Collapse size="small" items={items} />
  }

  const logsColumns = [
    {
      title: '访问时间',
      dataIndex: 'accessTime',
      key: 'accessTime',
      width: 180,
      render: (_, record) => dayjs(record.accessTime).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: 'IP地址',
      dataIndex: 'ipAddress',
      key: 'ipAddress',
      width: 150,
    },
    {
      title: '方法',
      dataIndex: 'method',
      key: 'method',
      width: 70,
      render: (_, record) => record.method ? <Tag color="blue">{record.method}</Tag> : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (_, record) => {
        const isAllowed = record.status === 'allowed'
        return <Tag color={isAllowed ? 'success' : 'error'}>{record.status}</Tag>
      },
    },
    {
      title: '路径',
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
        title="弹幕Token管理"
        extra={
          <>
            <Button type="primary" onClick={() => handleOpenModal(false)}>
              添加Token
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
                          <Tag color="green">启用</Tag>
                        ) : (
                          <Tag color="red">禁用</Tag>
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
                        创建时间: {dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        有效期: {!!record.expiresAt
                          ? dayjs(record.expiresAt).format('YYYY-MM-DD HH:mm:ss')
                          : '永久'}
                      </div>
                      {isEnabled && (
                        <div>
                          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">
                            今日调用: {record.dailyCallCount} / {limitText}
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
                    编辑
                  </Button>
                  <Button
                    size="small"
                    icon={<MyIcon icon="copy" size={16} />}
                    onClick={() => {
                      copy(
                        `${domain || window.location.origin}/api/v1/${record.token}`
                      )
                      messageApi.success('复制成功')
                    }}
                  >
                    复制
                  </Button>
                  <Button
                    size="small"
                    icon={<MyIcon icon="rizhi" size={16} />}
                    onClick={() => handleTokenLogs(record)}
                  >
                    日志
                  </Button>
                  <Button
                    size="small"
                    icon={isEnabled ? <MyIcon icon="pause" size={16} /> : <MyIcon icon="start" size={16} />}
                    onClick={() => handleToggleStatus(record)}
                  >
                    {isEnabled ? '禁用' : '启用'}
                  </Button>
                  <Button
                    size="small"
                    danger
                    icon={<MyIcon icon="delete" size={16} />}
                    onClick={() => handleDelete(record)}
                  >
                    删除
                  </Button>
                </div>
              </div>
            )
          }}
        />
      </Card>
      <Modal
        title={isEditing ? '编辑Token' : '添加新Token'}
        open={isModalOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setIsModalOpen(false)}
        footer={
          <div className="flex justify-between">
            <div>
              {isEditing && (
                <Button danger onClick={handleResetCounter}>
                  重置调用次数
                </Button>
              )}
            </div>
            <div>
              <Button onClick={() => setIsModalOpen(false)}>取消</Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={confirmLoading}
              >
                确认
              </Button>
            </div>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入名称' }]}
            className="mb-4"
          >
            <Input placeholder="例如：我的dandanplay客户端" />
          </Form.Item>
          <Form.Item
            name="validityPeriod"
            label="有效期"
            rules={[{ required: true, message: '请选择有效期' }]}
            className="mb-4"
          >
            <Select
              options={[
                isEditing && { value: 'custom', label: '不改变当前有效期' },
                { value: 'permanent', label: '永久' },
                { value: '1d', label: '1 天' },
                { value: '7d', label: '7 天' },
                { value: '30d', label: '30 天' },
                { value: '180d', label: '6 个月' },
                { value: '365d', label: '1 年' },
              ].filter(Boolean)}
            />
          </Form.Item>
          <Form.Item
            name="dailyCallLimit"
            label="每日调用上限"
            tooltip="设置此Token每日可调用的总次数。-1 代表无限次。"
            className="mb-4"
          >
            <InputNumber
              min={-1}
              style={{ width: '100%' }}
              placeholder="默认为500, -1为无限"
            />
          </Form.Item>
          <Form.Item
            name="customToken"
            label="自定义Token"
            tooltip="自定义Token字符串，仅允许字母、数字、下划线和短横线（5~100字符）。留空则自动生成。"
            className="mb-4"
            rules={[
              {
                pattern: /^[a-zA-Z0-9_-]*$/,
                message: '仅允许字母、数字、下划线和短横线',
              },
              {
                validator: (_, value) => {
                  if (value && value.length > 0 && value.length < 5) {
                    return Promise.reject('Token 长度至少 5 个字符')
                  }
                  return Promise.resolve()
                },
              },
            ]}
          >
            <Input
              placeholder={isEditing ? '留空保持当前Token不变' : '留空自动生成'}
              maxLength={100}
              allowClear
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={
          <div className="flex items-center gap-3">
            <Typography.Text>Token访问日志</Typography.Text>
            <Tag color="blue">{tokenLogs.length} 条</Tag>
          </div>
        }
        width={isMobile ? '100%' : '90vw'}
        style={isMobile ? {} : { maxWidth: 1400 }}
        open={logsOpen}
        cancelText="取消"
        okText="确认"
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
                        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0 mt-1">路径:</span>
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
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0">方法:</span>
                          <Tag color="blue" size="small">{log.method}</Tag>
                        </div>
                      )}
                      {log.requestBody && (
                        <div className="flex items-start gap-3">
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-8 shrink-0 mt-1">请求:</span>
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
              rowExpandable: (record) => !!(record.requestBody || record.responseBody),
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

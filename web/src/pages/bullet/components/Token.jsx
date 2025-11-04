import {
  Button,
  Card,
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

export const Token = ({ domain: propDomain }) => {
  const [loading, setLoading] = useState(false)
  const [tokenList, setTokenList] = useState([])
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [editingRecord, setEditingRecord] = useState(null)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()
  const [tokenLogs, setTokenLogs] = useState([])
  const [logsOpen, setLogsOpen] = useState(false)
  const [domain, setDomain] = useState(propDomain || '')
  const modalApi = useModal()
  const messageApi = useMessage()

  // 监听 prop 变化，更新本地状态
  useEffect(() => {
    setDomain(propDomain || '')
  }, [propDomain])

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
      content: <div>您确定要删除{record.name}吗？</div>,
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
          <div>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</div>
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
          <div>
            {!!record.expiresAt
              ? dayjs(record.expiresAt).format('YYYY-MM-DD HH:mm:ss')
              : '永久'}
          </div>
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
                className="cursor-pointer hover:text-primary"
                onClick={() => handleOpenModal(true, record)}
              >
                <MyIcon icon="edit" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="复制">
              <span
                className="cursor-pointer hover:text-primary"
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
                className="cursor-pointer hover:text-primary"
                onClick={() => handleTokenLogs(record)}
              >
                <MyIcon icon="rizhi" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="切换启用状态">
              <span
                className="cursor-pointer hover:text-primary"
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
                className="cursor-pointer hover:text-primary"
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

  const logsColumns = [
    {
      title: '访问时间',
      dataIndex: 'accessTime',
      key: 'accessTime',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.accessTime).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: 'IP地址',
      dataIndex: 'ipAddress',
      key: 'ipAddress',
      width: 150,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 200,
    },
    {
      title: '路径',
      width: 250,
      dataIndex: 'path',
      key: 'path',
    },
    {
      title: 'User-Agent',
      dataIndex: 'userAgent',
      key: 'userAgent',
      width: 400,
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
        <Table
          pagination={false}
          size="small"
          dataSource={tokenList}
          columns={columns}
          rowKey={'id'}
          scroll={{ x: '100%' }}
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
        </Form>
      </Modal>
      <Modal
        title="Token访问日志"
        open={logsOpen}
        cancelText="取消"
        okText="确认"
        onCancel={() => setLogsOpen(false)}
        onOk={() => setLogsOpen(false)}
      >
        <Table
          pagination={false}
          size="small"
          dataSource={tokenLogs}
          columns={logsColumns}
          rowKey={'accessTime'}
          scroll={{
            x: '100%',
            y: 400,
          }}
        />
      </Modal>
    </div>
  )
}

import {
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd'
import { useEffect, useState } from 'react'
import {
  addToken,
  deleteToken,
  getCustomDomain,
  getTokenList,
  getTokenLog,
  toggleTokenStatus,
} from '../../../apis'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon.jsx'
import copy from 'copy-to-clipboard'
import { EyeInvisibleOutlined, EyeTwoTone } from '@ant-design/icons'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

export const Token = () => {
  const [loading, setLoading] = useState(false)
  const [tokenList, setTokenList] = useState([])
  const [addTokenOpen, setAddTokenOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()
  const [tokenLogs, setTokenLogs] = useState([])
  const [logsOpen, setLogsOpen] = useState(false)
  const [domain, setDomain] = useState('')
  const modalApi = useModal()
  const messageApi = useMessage()

  const getTokens = async () => {
    try {
      const [tokenRes, domainRes] = await Promise.all([
        getTokenList(),
        getCustomDomain(),
      ])
      setTokenList(tokenRes.data)
      setDomain(domainRes.data?.value ?? '')
      setLoading(false)
    } catch (error) {
      console.error(error)
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
    } catch (error) {
      messageApi.error('操作失败')
    } finally {
      getTokens()
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

  const handleAddToken = async () => {
    const values = await form.validateFields()
    console.log(values, 'values')
    try {
      setConfirmLoading(true)
      await addToken(values)
    } catch (error) {
      messageApi.error('添加失败')
    } finally {
      setConfirmLoading(false)
      setAddTokenOpen(false)
      form.resetFields()
      getTokens()
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
      width: 100,
      dataIndex: 'isEnabled',
      key: 'isEnabled',
      render: (_, record) => {
        return (
          <div>
            {record.isEnabled ? (
              <Tag color="green">启用</Tag>
            ) : (
              <Tag color="red">禁用</Tag>
            )}
          </div>
        )
      },
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 200,
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
      width: 200,
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
      width: 120,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
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
            <Button type="primary" onClick={() => setAddTokenOpen(true)}>
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
        title="添加新Token"
        open={addTokenOpen}
        onOk={handleAddToken}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setAddTokenOpen(false)}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            validityPeriod: 'permanent',
          }}
        >
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
              onChange={value => {
                form.setFieldsValue({ validity: value })
              }}
              options={[
                { value: 'permanent', label: '永久' },
                { value: '1d', label: '1 天' },
                { value: '7d', label: '7 天' },
                { value: '30d', label: '30 天' },
                { value: '180d', label: '6 个月' },
                { value: '365d', label: '1 年' },
              ]}
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

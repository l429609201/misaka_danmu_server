import {
  Button,
  Card,
  Col,
  Input,
  message,
  Modal,
  Row,
  Select,
  Space,
  Table,
} from 'antd'
import { useEffect, useState } from 'react'
import {
  addUaRule,
  deleteUaRule,
  getUaMode,
  getUaRules,
  setUaMode,
} from '../../../apis'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon.jsx'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

export const Ua = () => {
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState('off')

  const [open, setOpen] = useState(false)
  const [uaRules, setUaRules] = useState([])
  const [uakeyword, setUakeyword] = useState('')
  const [addLoading, setAddLoading] = useState(false)
  const modalApi = useModal()
  const messageApi = useMessage()

  const columns = [
    {
      title: 'UA字符串',
      dataIndex: 'uaString',
      key: 'uaString',
      width: 150,
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
      title: '操作',
      width: 60,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => handleDelete(record)}
            >
              <MyIcon icon="delete" size={20}></MyIcon>
            </span>
          </Space>
        )
      },
    },
  ]

  useEffect(() => {
    setLoading(true)
    getUaMode()
      .then(res => {
        setMode(res.data?.value ?? 'off')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleEdit = async () => {
    try {
      await setUaMode({ value: mode })
      messageApi.success('保存成功')
    } catch (error) {
      messageApi.error('保存失败')
    }
  }

  const handleDelete = async record => {
    modalApi.confirm({
      title: '删除',
      zIndex: 1002,
      content: <div>您确定要删除{record.uaString}吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteUaRule({
            id: record.id,
          })
          handleList()
          messageApi.success('删除成功')
        } catch (error) {
          console.error(error)
          messageApi.error('删除失败')
        }
      },
    })
  }

  const handleAdd = async () => {
    try {
      if (addLoading) return
      if (!uakeyword) {
        messageApi.error('请输入UA关键词')
        return
      }
      setAddLoading(true)
      await addUaRule({
        uaString: uakeyword,
      })
    } catch (error) {
      messageApi.error('添加失败')
    } finally {
      setUakeyword('')
      handleList()
      setAddLoading(false)
    }
  }

  const handleList = async () => {
    try {
      const res = await getUaRules()
      setUaRules(res.data)
      setOpen(true)
    } catch (error) {
      messageApi.error('获取失败')
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="全局 User-Agent 过滤">
        <div className="mb-4">
          对所有通过Token的访问请求进行UA过滤。模式为 "off" 时不过滤。
        </div>
        <Row gutter={[12, 12]}>
          <Col md={2} xs={6}>
            <div className="leading-8">过滤模式</div>
          </Col>
          <Col md={10} xs={18}>
            <Select
              onChange={value => {
                setMode(value)
              }}
              style={{ width: '100%' }}
              value={mode}
              options={[
                { value: 'permanent', label: '关闭 (Off)' },
                { value: 'blacklist', label: '黑名单 (Blacklist)' },
                { value: 'whitelist', label: '白名单 (Whitelist)' },
              ]}
            />
          </Col>
          <Col md={6} xs={12}>
            <Button type="primary" block onClick={handleEdit}>
              保存模式
            </Button>
          </Col>
          <Col md={6} xs={12}>
            <Button type="primary" block onClick={handleList}>
              名单管理
            </Button>
          </Col>
        </Row>
      </Card>
      <Modal
        title="管理UA名单"
        open={open}
        cancelText="取消"
        okText="确认"
        footer={null}
        onCancel={() => setOpen(false)}
      >
        <div className="flex items-center justify-start my-4 gap-2">
          <div>添加UA字符串</div>
          <Input
            placeholder="请输入要匹配的UA关键字"
            value={uakeyword}
            onChange={e => setUakeyword(e.target.value)}
          />
          <Button type="primary" onClick={handleAdd} loading={addLoading}>
            添加
          </Button>
        </div>
        <Table
          pagination={false}
          size="small"
          dataSource={uaRules}
          columns={columns}
          rowKey={'id'}
          scroll={{
            x: '100%',
            y: 400,
          }}
        />
      </Modal>
    </div>
  )
}

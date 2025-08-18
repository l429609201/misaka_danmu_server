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
} from 'antd'
import { useEffect, useState } from 'react'
import {
  deleteScheduledTask,
  editScheduledTask,
  getScheduledTaskList,
  runTask,
} from '../../../apis'
import { MyIcon } from '@/components/MyIcon.jsx'
import dayjs from 'dayjs'
import { SCHEDULED_TYPE_MAPPING } from '../../../configs'

export const ScheduleTask = () => {
  const [loading, setLoading] = useState(true)
  const [scheduleTaskList, setScheduleTaskList] = useState([])
  const [addOpen, setAddOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)

  const [form] = Form.useForm()
  const editid = Form.useWatch('id', form)

  const refreshTasks = async () => {
    try {
      const res = await getScheduledTaskList()
      setScheduleTaskList(res.data)
      setLoading(false)
    } catch (error) {
      console.error(error)
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshTasks()
  }, [])

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 150,
    },
    {
      title: '类型',
      dataIndex: 'jobType',
      key: 'jobType',
      width: 200,
      render: (_, record) => {
        return <>{SCHEDULED_TYPE_MAPPING[record.jobType]}</>
      },
    },
    {
      title: 'Cron表达式',
      width: 100,
      dataIndex: 'cronExpression',
      key: 'cronExpression',
    },
    {
      title: '状态',
      dataIndex: 'isEnabled',
      key: 'isEnabled',
      width: 100,
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
      title: '上次运行时间',
      dataIndex: 'lastRunAt',
      key: 'lastRunAt',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.lastRunAt).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: '下次运行时间',
      dataIndex: 'nextRunAt',
      key: 'nextRunAt',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.nextRunAt).format('YYYY-MM-DD HH:mm:ss')}</div>
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
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => handleRun(record)}
            >
              <MyIcon icon="canshuzhihang" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                form.setFieldsValue({
                  ...record,
                })
                setAddOpen(true)
              }}
            >
              <MyIcon icon="edit" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                handleDelete(record)
              }}
            >
              <MyIcon icon="delete" size={20}></MyIcon>
            </span>
          </Space>
        )
      },
    },
  ]

  const handleRun = async record => {
    try {
      await runTask({ id: record.id })
      message.success('任务已触发运行，请稍后刷新查看运行时间。')
    } catch (error) {
      message.error('任务触发失败，请稍后重试。')
    }
  }

  const handleAdd = async () => {
    const values = await form.validateFields()
    if (!!values.id) {
      try {
        await editScheduledTask(values)
        message.success('任务编辑成功。')
        form.resetFields()
        refreshTasks()
        setAddOpen(false)
      } catch (error) {
        message.error('任务编辑失败，请稍后重试。')
      }
    } else {
      try {
        await addTask(values)
        message.success('任务添加成功。')
        form.resetFields()
        refreshTasks()
        setAddOpen(false)
      } catch (error) {
        message.error('任务添加失败，请稍后重试。')
      }
    }
  }

  const handleDelete = async record => {
    Modal.confirm({
      title: '删除任务',
      zIndex: 1002,
      content: <div>确定要删除这个定时任务吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteScheduledTask({ id: record.id })
          message.success('任务删除成功。')
          refreshTasks()
        } catch (error) {
          message.error('任务删除失败，请稍后重试。')
        }
      },
    })
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title="定时任务"
        extra={
          <Button
            type="primary"
            onClick={() => {
              setAddOpen(true)
            }}
          >
            添加定时任务
          </Button>
        }
      >
        <div className="mb-4">
          定时任务用于自动执行维护操作，例如自动更新和映射TMDB数据。使用标准的Cron表达式格式。
        </div>
        <Table
          pagination={false}
          size="small"
          dataSource={scheduleTaskList}
          columns={columns}
          rowKey={'id'}
          scroll={{ x: '100%' }}
        />
      </Card>
      <Modal
        title={!!editid ? '编辑定时任务' : '添加定时任务'}
        open={addOpen}
        onOk={handleAdd}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setAddOpen(false)}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            jobType: 'tmdbAutoMap',
            isEnabled: true,
          }}
        >
          <Form.Item
            name="name"
            label="任务名称"
            rules={[{ required: true, message: '请输入任务名称' }]}
            className="mb-4"
          >
            <Input placeholder="例如：我的每日TMDB更新" />
          </Form.Item>
          <Form.Item
            name="jobType"
            label="任务类型"
            rules={[{ required: true, message: '请选择有效期' }]}
            className="mb-4"
          >
            <Select
              options={[
                {
                  value: 'tmdbAutoMap',
                  label: SCHEDULED_TYPE_MAPPING['tmdbAutoMap'],
                },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="cronExpression"
            label="Corn表达式"
            rules={[{ required: true, message: '请输入Corn表达式' }]}
            className="mb-4"
          >
            <Input placeholder="例如：0 2 * * *（每天凌晨2点）" />
          </Form.Item>
          <Form.Item
            name="isEnabled"
            label="是否启用"
            rules={[{ required: true, message: '请选择启用状态' }]}
            className="mb-4"
          >
            <Select
              options={[
                { value: true, label: '启用' },
                { value: false, label: '禁用' },
              ]}
            />
          </Form.Item>
          <Form.Item name="id" label="id" hidden>
            <Input disabled />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

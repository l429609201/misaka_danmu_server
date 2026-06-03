import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import {
  deleteScheduledTask,
  editScheduledTask,
  addScheduledTask,
  runTask,
  getAvailableScheduledJobs,
  getScheduledTaskList,
} from '../../../apis'
import { MyIcon } from '@/components/MyIcon.jsx'
import dayjs from 'dayjs'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'
import { getLocalizedField, localizeItems } from '../../../utils/i18nDynamic'
import { Cron } from 'react-js-cron'
import 'react-js-cron/dist/styles.css'
import cronstrue from 'cronstrue/i18n'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'

export const ScheduleTask = () => {
  const { t, i18n } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [tasks, setTasks] = useState([])
  const [availableJobTypes, setAvailableJobTypes] = useState([])
  const [advancedMode, setAdvancedMode] = useState(false)

  // Cron 组件本地化配置（随语言切换）
  const cronLocale = t('cronLocale', { returnObjects: true })

  // cronstrue 语言映射
  const cronstrueLocale = i18n.language === 'en' ? 'en' : i18n.language === 'zh-TW' ? 'zh_TW' : 'zh_CN'

  const [form] = Form.useForm()
  const editid = Form.useWatch('taskId', form)
  const modalApi = useModal()
  const messageApi = useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  // 获取Cron表达式的人类可读描述
  const getCronDescription = (cronExpression) => {
    try {
      return cronstrue.toString(cronExpression, { locale: cronstrueLocale })
    } catch (error) {
      return t('scheduleTask.invalidCron')
    }
  }

  // 验证Cron表达式是否合法
  const validateCron = (cronExpression) => {
    if (!cronExpression) return false
    try {
      cronstrue.toString(cronExpression, { locale: cronstrueLocale })
      return true
    } catch (error) {
      return false
    }
  }

  // 根据 configSchema 中的配置项定义，渲染对应的表单控件
  const renderConfigFormItem = (item) => {
    const { key, type, min, max, rows, options } = item
    const label = getLocalizedField(item, 'label')
    const description = getLocalizedField(item, 'description')
    const placeholder = getLocalizedField(item, 'placeholder')
    const suffix = getLocalizedField(item, 'suffix')

    switch (type) {
      case 'boolean':
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            valuePropName="checked"
            className="mb-4"
            tooltip={description}
          >
            <Switch checkedChildren={t('common.enable')} unCheckedChildren={t('common.disable')} />
          </Form.Item>
        )

      case 'password':
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            className="mb-4"
            tooltip={description}
          >
            <Input.Password placeholder={placeholder} />
          </Form.Item>
        )

      case 'number':
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            className="mb-4"
            tooltip={description}
          >
            <InputNumber
              min={min}
              max={max}
              addonAfter={suffix}
              placeholder={placeholder}
              style={{ width: '100%' }}
            />
          </Form.Item>
        )

      case 'textarea':
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            className="mb-4"
            tooltip={description}
          >
            <Input.TextArea rows={rows || 3} placeholder={placeholder} />
          </Form.Item>
        )

      case 'select':
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            className="mb-4"
            tooltip={description}
          >
            <Select
              placeholder={placeholder}
              options={localizeItems(
                options?.map(opt =>
                  typeof opt === 'string' ? { value: opt, label: opt } : opt
                ),
                ['label']
              )}
            />
          </Form.Item>
        )

      default: // string
        return (
          <Form.Item
            key={key}
            name={['taskConfig', key]}
            label={label}
            className="mb-4"
            tooltip={description}
          >
            <Input placeholder={placeholder} />
          </Form.Item>
        )
    }
  }

  const fetchData = async () => {
    try {
      setLoading(true)
      const [tasksRes, jobsRes] = await Promise.all([
        getScheduledTaskList(),
        getAvailableScheduledJobs(),
      ])
      setTasks(tasksRes.data || [])
      setAvailableJobTypes(jobsRes.data || [])
    } catch (error) {
      messageApi.error(t('scheduleTask.fetchFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const columns = [
    {
      title: t('scheduleTask.colName'),
      dataIndex: 'name',
      key: 'name',
      width: 150,
      render: (name, record) => {
        // 如果任务名称等于对应 jobType 的默认中文名，则显示本地化名称
        const jobType = availableJobTypes.find(j => j.jobType === record.jobType)
        if (jobType && name === jobType.name) {
          return getLocalizedField(jobType, 'name')
        }
        return name
      },
    },
    {
      title: t('scheduleTask.colType'),
      dataIndex: 'jobType',
      key: 'jobType',
      width: 200,
      render: (_, record) => {
        const jobType = availableJobTypes.find(
          job => job.jobType === record.jobType
        )
        return (
          <div className="flex items-center gap-2">
            <span>{getLocalizedField(jobType, 'name') || record.jobType}</span>
            {record.isSystemTask && (
              <Tag color="blue" size="small">{t('scheduleTask.systemTask')}</Tag>
            )}
          </div>
        )
      },
    },
    {
      title: t('scheduleTask.colCron'),
      width: 150,
      dataIndex: 'cronExpression',
      key: 'cronExpression',
    },
    {
      title: t('scheduleTask.colStatus'),
      dataIndex: 'isEnabled',
      key: 'isEnabled',
      width: 100,
      render: (_, record) => {
        return (
          <div>
            {record.isEnabled ? (
              <Tag color="green">{t('scheduleTask.enabled')}</Tag>
            ) : (
              <Tag color="red">{t('scheduleTask.disabled')}</Tag>
            )}
          </div>
        )
      },
    },
    {
      title: t('scheduleTask.colLastRun'),
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
      title: t('scheduleTask.colNextRun'),
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
      title: t('scheduleTask.colAction'),
      width: 100,
      fixed: 'right',
      render: (_, record) => {
        const isSystemTask = record.isSystemTask || false

        // 系统任务不显示操作按钮
        if (isSystemTask) {
          return null
        }

        return (
          <Space>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => handleRun(record)}
              title={t('scheduleTask.runNow')}
            >
              <MyIcon icon="canshuzhihang" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                form.setFieldsValue({
                  ...record,
                  taskConfig: record.taskConfig || {},
                })
                setAddOpen(true)
              }}
              title={t('scheduleTask.editTask')}
            >
              <MyIcon icon="edit" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => handleDelete(record)}
              title={t('scheduleTask.deleteTask')}
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
      await runTask({ id: record.taskId })
      messageApi.success(t('scheduleTask.runTriggered'))
    } catch (error) {
      messageApi.error(t('scheduleTask.runFailed'))
    }
  }

  const handleAdd = async () => {
    const values = await form.validateFields()
    if (!!values.taskId) {
      try {
        setConfirmLoading(true)
        await editScheduledTask({ ...values, id: values.taskId })
        messageApi.success(t('scheduleTask.editSuccess'))
        form.resetFields()
        fetchData()
        setAddOpen(false)
        setAdvancedMode(false)
      } catch (error) {
        messageApi.error(error?.detail ?? t('scheduleTask.editFailed'))
      } finally {
        setConfirmLoading(false)
      }
    } else {
      try {
        await addScheduledTask(values)
        messageApi.success(t('scheduleTask.addSuccess'))
        form.resetFields()
        fetchData()
        setAddOpen(false)
        setAdvancedMode(false)
      } catch (error) {
        messageApi.error(error?.detail ?? t('scheduleTask.addFailed'))
      } finally {
        setConfirmLoading(false)
      }
    }
  }

  const handleDelete = async record => {
    modalApi.confirm({
      title: t('scheduleTask.deleteTitle'),
      zIndex: 1002,
      content: <div>{t('scheduleTask.deleteConfirm')}</div>,
      okText: t('scheduleTask.confirm'),
      cancelText: t('scheduleTask.cancel'),
      onOk: async () => {
        try {
          await deleteScheduledTask({ id: record.taskId })
          messageApi.success(t('scheduleTask.deleteSuccess'))
          fetchData()
        } catch (error) {
          messageApi.error(error?.detail ?? t('scheduleTask.deleteFailed'))
        }
      },
    })
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title={t('scheduleTask.title')}
        extra={
          <Button
            type="primary"
            onClick={() => {
              setAddOpen(true)
            }}
          >
            {t('scheduleTask.addTask')}
          </Button>
        }
      >
        <div className="mb-4">
          {t('scheduleTask.intro')}
        </div>
        {isMobile ? (
          <div className="grid grid-cols-1 gap-4">
            {tasks.map((task) => {
              const jobType = availableJobTypes.find(
                job => job.jobType === task.jobType
              )
              const isSystemTask = task.isSystemTask || false

              return (
                <Card
                  key={task.taskId}
                  className="shadow-sm hover:shadow-md transition-shadow"
                  title={
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-lg">{(jobType && task.name === jobType.name) ? getLocalizedField(jobType, 'name') : task.name}</span>
                      {isSystemTask && (
                        <Tag color="blue" size="small">{t('scheduleTask.systemTask')}</Tag>
                      )}
                    </div>
                  }
                  extra={
                    !isSystemTask && (
                      <Space size="small">
                        <Tooltip title={t('scheduleTask.runNow')}>
                          <Button
                            type="text"
                            icon={<MyIcon icon="canshuzhihang" size={16} />}
                            onClick={() => handleRun(task)}
                            size="small"
                          />
                        </Tooltip>
                        <Tooltip title={t('scheduleTask.editTask')}>
                          <Button
                            type="text"
                            icon={<MyIcon icon="edit" size={16} />}
                            onClick={() => {
                              form.setFieldsValue({
                                ...task,
                                taskConfig: task.taskConfig || {},
                              })
                              setAddOpen(true)
                            }}
                            size="small"
                          />
                        </Tooltip>
                        <Tooltip title={t('scheduleTask.deleteTask')}>
                          <Button
                            type="text"
                            icon={<MyIcon icon="delete" size={16} />}
                            onClick={() => handleDelete(task)}
                            size="small"
                            danger
                          />
                        </Tooltip>
                      </Space>
                    )
                  }
                >
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <span className="text-gray-600">{t('scheduleTask.typeLabel')}</span>
                      <span>{getLocalizedField(jobType, 'name') || task.jobType}</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-gray-600">{t('scheduleTask.cronLabel')}</span>
                      <Typography.Text code>{task.cronExpression}</Typography.Text>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-gray-600">{t('scheduleTask.statusLabel')}</span>
                      {task.isEnabled ? (
                        <Tag color="green">{t('scheduleTask.enabled')}</Tag>
                      ) : (
                        <Tag color="red">{t('scheduleTask.disabled')}</Tag>
                      )}
                    </div>

                    {task.jobType === 'tmdbAutoScrape' && (
                      <div className="flex items-center gap-2">
                        <span className="text-gray-600">{t('scheduleTask.forceScrape')}</span>
                        {task.taskConfig?.forceScrape ? (
                          <Tag color="orange">{t('scheduleTask.on')}</Tag>
                        ) : (
                          <Tag>{t('scheduleTask.off')}</Tag>
                        )}
                      </div>
                    )}

                    <div className="grid grid-cols-1 gap-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-gray-600">{t('scheduleTask.lastRun')}</span>
                        <span>{dayjs(task.lastRunAt).format('YYYY-MM-DD HH:mm:ss')}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-600">{t('scheduleTask.nextRun')}</span>
                        <span>{dayjs(task.nextRunAt).format('YYYY-MM-DD HH:mm:ss')}</span>
                      </div>
                    </div>
                  </div>
                </Card>
              )
            })}
          </div>
        ) : (
          <Table
            pagination={false}
            size="small"
            dataSource={tasks}
            columns={columns}
            rowKey={'taskId'}
            scroll={{ x: '100%' }}
          />
        )}
      </Card>
      <Modal
        title={!!editid ? t('scheduleTask.editModalTitle') : t('scheduleTask.addModalTitle')}
        open={addOpen}
        onOk={handleAdd}
        confirmLoading={confirmLoading}
        cancelText={t('scheduleTask.cancel')}
        okText={t('scheduleTask.confirm')}
        onCancel={() => {
          setAddOpen(false)
          setAdvancedMode(false)
        }}
        afterClose={() => {
          form.resetFields()
          setAdvancedMode(false)
        }}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            jobType: availableJobTypes.filter(job => !job.isSystemTask)[0]?.jobType || '',
            isEnabled: true,
            taskConfig: {},
            cronExpression: '0 2 * * *',
          }}
        >
          <Form.Item name="taskId" label="taskId" hidden>
            <Input disabled />
          </Form.Item>
          <Tabs
            defaultActiveKey="general"
            items={[
              {
                key: 'general',
                label: t('scheduleTask.tabGeneral'),
                forceRender: true,
                children: (
                  <>
                    <Form.Item
                      name="name"
                      label={t('scheduleTask.taskName')}
                      rules={[{ required: true, message: t('scheduleTask.taskNameRequired') }]}
                      className="mb-4"
                    >
                      <Input placeholder={t('scheduleTask.taskNamePlaceholder')} />
                    </Form.Item>
                    <Form.Item
                      name="jobType"
                      label={t('scheduleTask.taskType')}
                      rules={[{ required: true, message: t('scheduleTask.taskTypeRequired') }]}
                      className="mb-4"
                    >
                      <Select disabled={!!editid}>
                        {availableJobTypes
                          .filter(job => !job.isSystemTask)
                          .map(job => (
                            <Select.Option key={job.jobType} value={job.jobType}>
                              <Tooltip title={getLocalizedField(job, 'description')} placement="right">
                                <span>{getLocalizedField(job, 'name')}</span>
                              </Tooltip>
                            </Select.Option>
                          ))}
                      </Select>
                    </Form.Item>
                    <Form.Item
                      name="cronExpression"
                      label={
                        <div className="flex items-center justify-between w-full">
                          <span>{t('scheduleTask.colCron')}</span>
                          <Button
                            type="link"
                            size="small"
                            onClick={() => setAdvancedMode(!advancedMode)}
                            className="p-0"
                          >
                            {advancedMode ? t('scheduleTask.visualMode') : t('scheduleTask.advancedMode')}
                          </Button>
                        </div>
                      }
                      rules={[{ required: true, message: t('scheduleTask.cronRequired') }]}
                      className="mb-4"
                    >
                      {advancedMode ? (
                        <Input
                          placeholder={t('scheduleTask.cronPlaceholder')}
                          suffix={
                            form.getFieldValue('cronExpression') ? (
                              validateCron(form.getFieldValue('cronExpression')) ? (
                                <CheckCircleOutlined
                                  style={{ color: '#52c41a', fontSize: 16 }}
                                />
                              ) : (
                                <CloseCircleOutlined
                                  style={{ color: '#ff4d4f', fontSize: 16 }}
                                />
                              )
                            ) : null
                          }
                        />
                      ) : (
                        <Cron
                          value={form.getFieldValue('cronExpression') || '0 2 * * *'}
                          setValue={(newValue) => {
                            form.setFieldsValue({ cronExpression: newValue })
                          }}
                          clearButton={false}
                          locale={cronLocale}
                        />
                      )}
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate>
                      {() => {
                        const currentCron = form.getFieldValue('cronExpression')
                        if (currentCron && !advancedMode) {
                          return (
                            <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 rounded border border-blue-200 dark:border-blue-800">
                              <div className="text-sm text-gray-600 dark:text-gray-300">
                                <span className="font-medium">{t('scheduleTask.executeTime')}</span>
                                {getCronDescription(currentCron)}
                              </div>
                            </div>
                          )
                        }
                        return null
                      }}
                    </Form.Item>
                    <Form.Item
                      name="isEnabled"
                      label={t('scheduleTask.isEnabled')}
                      valuePropName="checked"
                      className="mb-4"
                    >
                      <Switch checkedChildren={t('scheduleTask.enabled')} unCheckedChildren={t('scheduleTask.disabled')} />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'config',
                label: t('scheduleTask.tabConfig'),
                forceRender: true,
                children: (
                  <Form.Item noStyle shouldUpdate={(prev, cur) => prev.jobType !== cur.jobType}>
                    {() => {
                      const currentJobType = form.getFieldValue('jobType')
                      const jobInfo = availableJobTypes.find(j => j.jobType === currentJobType)
                      const schema = jobInfo?.configSchema || []

                      if (schema.length === 0) {
                        return (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={t('scheduleTask.noConfig')}
                          />
                        )
                      }

                      return schema.map(item => renderConfigFormItem(item))
                    }}
                  </Form.Item>
                ),
              },
            ]}
          />
        </Form>
      </Modal>
    </div>
  )
}

import { useState, useEffect, useCallback } from 'react'
import {
  Card, Button, Tag, Switch, Space, Form, Input, Select, Slider,
  Popconfirm, Spin, Empty, message, Tooltip, Row, Col,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ApiOutlined,
  ReloadOutlined, CopyOutlined,
} from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store/index.js'
import { ResponsiveTable } from '../../../components/ResponsiveTable'
import { ResponsiveModal } from '../../../components/ResponsiveModal'
import {
  getNotificationChannelTypes, getNotificationChannels,
  createNotificationChannel, updateNotificationChannel,
  deleteNotificationChannel, testNotificationChannel,
} from '../../../apis'

// 事件分组定义（MoviePilot 风格）
const EVENT_GROUPS = [
  {
    label: '导入',
    events: [
      { label: '导入成功', value: 'import_success' },
      { label: '导入失败', value: 'import_failed' },
    ],
  },
  {
    label: '刷新',
    events: [
      { label: '刷新成功', value: 'refresh_success' },
      { label: '刷新失败', value: 'refresh_failed' },
    ],
  },
  {
    label: '自动导入',
    events: [
      { label: '自动导入成功', value: 'auto_import_success' },
      { label: '自动导入失败', value: 'auto_import_failed' },
    ],
  },
  {
    label: 'Webhook',
    events: [
      { label: '触发', value: 'webhook_triggered' },
      { label: '导入成功', value: 'webhook_import_success' },
      { label: '导入失败', value: 'webhook_import_failed' },
    ],
  },
  {
    label: '追更',
    events: [
      { label: '刷新成功', value: 'incremental_refresh_success' },
      { label: '刷新失败', value: 'incremental_refresh_failed' },
    ],
  },
  {
    label: '媒体库',
    events: [
      { label: '扫描完成', value: 'media_scan_complete' },
    ],
  },
  {
    label: '定时任务',
    events: [
      { label: '任务完成', value: 'scheduled_task_complete' },
      { label: '任务失败', value: 'scheduled_task_failed' },
    ],
  },
  {
    label: '系统',
    events: [
      { label: '系统启动', value: 'system_start' },
    ],
  },
]

// 扁平化所有事件（用于序列化）
const ALL_EVENTS = EVENT_GROUPS.flatMap(g => g.events)

export const Notification = () => {
  const isMobile = useAtomValue(isMobileAtom)
  const [channels, setChannels] = useState([])
  const [channelTypes, setChannelTypes] = useState([])
  const [loading, setLoading] = useState(true)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingChannel, setEditingChannel] = useState(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState({})
  const [form] = Form.useForm()

  // 监听 channelType 和 config 变化以实现 visibleWhen
  const selectedType = Form.useWatch('channelType', form)
  const configValues = Form.useWatch('config', form)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [typesRes, channelsRes] = await Promise.all([
        getNotificationChannelTypes(),
        getNotificationChannels(),
      ])
      setChannelTypes(typesRes.data || [])
      setChannels(channelsRes.data || [])
    } catch (e) {
      message.error('加载通知渠道数据失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const getSchemaForType = (type) => {
    const found = channelTypes.find(t => t.channelType === type)
    return found?.configSchema || []
  }

  // 根据渠道类型自动生成名称：第一个 "Telegram"，第二个 "Telegram 1"，以此类推
  const generateChannelName = (channelType) => {
    const typeInfo = channelTypes.find(t => t.channelType === channelType)
    const baseName = typeInfo?.displayName || channelType
    const existing = channels.filter(c => c.channelType === channelType)
    if (existing.length === 0) return baseName
    return `${baseName} ${existing.length}`
  }

  const handleAdd = () => {
    setEditingChannel(null)
    form.resetFields()
    const defaultType = channelTypes[0]?.channelType || ''
    form.setFieldsValue({
      isEnabled: true,
      useProxy: false,
      channelType: defaultType,
      name: generateChannelName(defaultType),
      config: {},
      eventsConfig: [],
    })
    setModalVisible(true)
  }

  const handleEdit = (record) => {
    setEditingChannel(record)
    const eventsArr = Object.entries(record.eventsConfig || {})
      .filter(([, v]) => v).map(([k]) => k)
    form.setFieldsValue({
      name: record.name,
      channelType: record.channelType,
      isEnabled: record.isEnabled,
      useProxy: record.useProxy ?? false,
      config: record.config || {},
      eventsConfig: eventsArr,
    })
    setModalVisible(true)
  }

  const handleDelete = async (id) => {
    try {
      await deleteNotificationChannel(id)
      message.success('已删除')
      loadData()
    } catch { message.error('删除失败') }
  }

  const handleTest = async (id) => {
    setTesting(prev => ({ ...prev, [id]: true }))
    try {
      const res = await testNotificationChannel(id)
      const data = res.data
      if (data.success) {
        message.success(data.message || '连接成功')
      } else {
        message.error(data.message || '连接失败')
      }
    } catch { message.error('测试请求失败') }
    finally { setTesting(prev => ({ ...prev, [id]: false })) }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const eventsObj = {}
      ALL_EVENTS.forEach(o => { eventsObj[o.value] = (values.eventsConfig || []).includes(o.value) })
      const payload = {
        name: values.name,
        channelType: values.channelType,
        isEnabled: values.isEnabled,
        useProxy: values.useProxy ?? false,
        config: values.config || {},
        eventsConfig: eventsObj,
      }
      if (editingChannel) {
        await updateNotificationChannel(editingChannel.id, payload)
        message.success('已更新')
      } else {
        await createNotificationChannel(payload)
        message.success('已创建')
      }
      setModalVisible(false)
      loadData()
    } catch (e) {
      if (e.errorFields) return // form validation
      message.error('保存失败')
    } finally { setSaving(false) }
  }

  // 根据 schema 的 visibleWhen 判断字段是否可见
  const isFieldVisible = (field) => {
    if (!field.visibleWhen) return true
    return Object.entries(field.visibleWhen).every(
      ([k, v]) => (configValues || {})[k] === v
    )
  }

  // 渲染单个配置字段
  const renderConfigField = (field) => {
    if (!isFieldVisible(field)) return null
    const name = ['config', field.key]
    if (field.type === 'switch') {
      return (
        <Form.Item key={field.key} label={field.label} name={name}
          tooltip={field.description} initialValue={field.default || field.switchValues?.unchecked}>
          <Select>
            <Select.Option value={field.switchValues?.unchecked || 'polling'}>
              {field.switchLabels?.unchecked || '选项A'}
            </Select.Option>
            <Select.Option value={field.switchValues?.checked || 'webhook'}>
              {field.switchLabels?.checked || '选项B'}
            </Select.Option>
          </Select>
        </Form.Item>
      )
    }
    if (field.type === 'slider') {
      return (
        <Form.Item key={field.key} label={field.label} name={name}
          tooltip={field.description} initialValue={field.default}>
          <Slider min={field.min || 0} max={field.max || 100} step={field.step || 1}
            marks={field.marks} tooltip={{ formatter: (v) => field.suffix ? `${v}${field.suffix}` : v }} />
        </Form.Item>
      )
    }
    if (field.type === 'boolean') {
      return (
        <Form.Item key={field.key} label={field.label} name={name}
          tooltip={field.description} valuePropName="checked" initialValue={field.default || false}>
          <Switch />
        </Form.Item>
      )
    }
    if (field.type === 'password') {
      return (
        <Form.Item key={field.key} label={field.label} name={name}
          tooltip={field.description} rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : []}>
          <Input.Password placeholder={field.placeholder} />
        </Form.Item>
      )
    }
    return (
      <Form.Item key={field.key} label={field.label} name={name}
        tooltip={field.description} rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : []}>
        <Input placeholder={field.placeholder} />
      </Form.Item>
    )
  }

  const currentSchema = getSchemaForType(selectedType)

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '类型', dataIndex: 'channelType', key: 'channelType',
      render: (v) => {
        const t = channelTypes.find(ct => ct.channelType === v)
        return <Tag>{t?.displayName || v}</Tag>
      },
    },
    {
      title: '状态', dataIndex: 'isEnabled', key: 'isEnabled',
      render: (v) => v ? <Tag color="green">启用</Tag> : <Tag color="default">禁用</Tag>,
    },
    {
      title: '模式', key: 'mode',
      render: (_, r) => {
        const mode = r.config?.mode
        return mode === 'webhook' ? <Tag color="blue">Webhook</Tag> : <Tag>轮询</Tag>
      },
    },
    {
      title: '操作', key: 'actions', width: 260,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title="测试连接">
            <Button size="small" icon={<ApiOutlined />}
              loading={testing[record.id]} onClick={() => handleTest(record.id)} />
          </Tooltip>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除此渠道？" onConfirm={() => handleDelete(record.id)} okText="确定" cancelText="取消">
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // 移动端卡片渲染
  const renderChannelCard = (record) => {
    const typeInfo = channelTypes.find(ct => ct.channelType === record.channelType)
    const mode = record.config?.mode
    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontWeight: 500, fontSize: 15 }}>{record.name}</span>
          {record.isEnabled ? <Tag color="green">启用</Tag> : <Tag color="default">禁用</Tag>}
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
          <Tag>{typeInfo?.displayName || record.channelType}</Tag>
          {mode === 'webhook' ? <Tag color="blue">Webhook</Tag> : <Tag>轮询</Tag>}
          {record.useProxy && <Tag color="orange">代理</Tag>}
        </div>
        <Space size="small" wrap>
          <Tooltip title="测试连接">
            <Button size="small" icon={<ApiOutlined />}
              loading={testing[record.id]} onClick={() => handleTest(record.id)}>测试</Button>
          </Tooltip>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm title="确定删除此渠道？" onConfirm={() => handleDelete(record.id)} okText="确定" cancelText="取消">
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      </div>
    )
  }

  return (
    <div>
      <Card
        title="通知渠道管理"
        extra={
          isMobile ? (
            <Space size="small">
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading} size="small" />
              <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd} size="small">添加</Button>
            </Space>
          ) : (
            <Space>
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading}>刷新</Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>添加渠道</Button>
            </Space>
          )
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : channels.length === 0 ? (
          <Empty description="暂无通知渠道，点击上方按钮添加" />
        ) : (
          <ResponsiveTable
            dataSource={channels}
            columns={columns}
            rowKey="id"
            renderCard={renderChannelCard}
            tableProps={{ pagination: false, size: 'middle' }}
          />
        )}
      </Card>

      <ResponsiveModal
        title={editingChannel ? '编辑通知渠道' : '添加通知渠道'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        width={560}
        height="85vh"
        footer={
          <div style={{ display: 'flex', gap: 8, justifyContent: isMobile ? 'stretch' : 'flex-end' }}>
            <Button onClick={() => setModalVisible(false)} block={isMobile}>取消</Button>
            <Button type="primary" onClick={handleSave} loading={saving} block={isMobile}>保存</Button>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item label="渠道名称" name="name" rules={[{ required: true, message: '请输入渠道名称' }]}>
            <Input placeholder="例如: 管理员通知Bot" />
          </Form.Item>
          <Form.Item label="渠道类型" name="channelType" rules={[{ required: true }]}>
            <Select disabled={!!editingChannel} onChange={(val) => {
              if (!editingChannel) {
                form.setFieldsValue({ name: generateChannelName(val), config: {} })
              }
            }}>
              {channelTypes.map(t => (
                <Select.Option key={t.channelType} value={t.channelType}>{t.displayName}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Row gutter={24}>
            <Col span={currentSchema.some(f => f.key === 'log_raw') ? 8 : 12}>
              <Form.Item label="启用" name="isEnabled" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={currentSchema.some(f => f.key === 'log_raw') ? 8 : 12}>
              <Form.Item label="使用代理" name="useProxy" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            {currentSchema.some(f => f.key === 'log_raw') && (
              <Col span={8}>
                <Form.Item label="记录交互" name={['config', 'log_raw']}
                  tooltip={currentSchema.find(f => f.key === 'log_raw')?.description}
                  valuePropName="checked" initialValue={false}>
                  <Switch />
                </Form.Item>
              </Col>
            )}
          </Row>

          {currentSchema.filter(f => f.key !== 'log_raw').map(field => renderConfigField(field))}

          {/* 编辑已有渠道 + webhook 模式时，展示完整回调地址 */}
          {editingChannel?.id && configValues?.mode === 'webhook' && (
            <Form.Item label="Webhook 回调地址">
              <Input.Search
                readOnly
                value={`${window.location.origin}/api/ui/notification/channels/${editingChannel.id}/webhook`}
                enterButton={<CopyOutlined />}
                onSearch={(value) => {
                  navigator.clipboard.writeText(value)
                  message.success('已复制到剪贴板')
                }}
              />
            </Form.Item>
          )}

          <Form.Item label="事件订阅" name="eventsConfig">
            <Select
              mode="multiple"
              placeholder="请选择订阅事件"
              maxTagCount="responsive"
              optionFilterProp="label"
            >
              {EVENT_GROUPS.map(group => (
                <Select.OptGroup key={group.label} label={group.label}>
                  {group.events.map(event => (
                    <Select.Option key={event.value} value={event.value} label={`${group.label}-${event.label}`}>
                      {event.label}
                    </Select.Option>
                  ))}
                </Select.OptGroup>
              ))}
            </Select>
          </Form.Item>
        </Form>
      </ResponsiveModal>
    </div>
  )
}
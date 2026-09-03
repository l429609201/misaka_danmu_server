import { useState, useEffect, useCallback } from 'react'
import {
  Card, Button, Tag, Switch, Space, Form, Input, Select, Slider, Segmented,
  Popconfirm, Spin, Empty, message, Tooltip, Row, Col, Typography,
} from 'antd'
import {
  PlusOutlined, EditOutlined, DeleteOutlined, ApiOutlined,
  ReloadOutlined, CopyOutlined, FormOutlined,
} from '@ant-design/icons'
import copy from 'copy-to-clipboard'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store/index.js'
import { ResponsiveTable } from '../../../components/ResponsiveTable'
import { ResponsiveModal } from '../../../components/ResponsiveModal'
import { NotificationTemplateEditor } from './NotificationTemplateEditor'
import {
  getNotificationChannelTypes, getNotificationChannels,
  createNotificationChannel, updateNotificationChannel,
  deleteNotificationChannel, testNotificationChannel,
  getWebhookApikey, validateNotificationPublicDomain,
  getNotificationTemplates,
} from '../../../apis'
import { useTranslation } from 'react-i18next'
import { getLocalizedField } from '../../../utils/i18nDynamic'

const { Text } = Typography

// 事件分组定义（使用 t 函数，支持国际化）
const getEventGroups = (t) => [
  {
    label: t('notification.groupImport'),
    events: [
      { label: t('notification.eventImportSuccess'), value: 'import_success' },
      { label: t('notification.eventImportFailed'), value: 'import_failed' },
    ],
  },
  {
    label: t('notification.groupRefresh'),
    events: [
      { label: t('notification.eventRefreshSuccess'), value: 'refresh_success' },
      { label: t('notification.eventRefreshFailed'), value: 'refresh_failed' },
    ],
  },
  {
    label: t('notification.groupAutoImport'),
    events: [
      { label: t('notification.eventAutoImportSuccess'), value: 'auto_import_success' },
      { label: t('notification.eventAutoImportFailed'), value: 'auto_import_failed' },
    ],
  },
  {
    label: 'Webhook',
    events: [
      { label: t('notification.eventWebhookTriggered'), value: 'webhook_triggered' },
      { label: t('notification.eventWebhookImportSuccess'), value: 'webhook_import_success' },
      { label: t('notification.eventWebhookImportFailed'), value: 'webhook_import_failed' },
    ],
  },
  {
    label: t('notification.groupIncremental'),
    events: [
      { label: t('notification.eventIncrementalSuccess'), value: 'incremental_refresh_success' },
      { label: t('notification.eventIncrementalFailed'), value: 'incremental_refresh_failed' },
    ],
  },
  {
    label: t('notification.groupMedia'),
    events: [
      { label: t('notification.eventMediaScanComplete'), value: 'media_scan_complete' },
    ],
  },
  {
    label: t('notification.groupScheduled'),
    events: [
      { label: t('notification.eventScheduledComplete'), value: 'scheduled_task_complete' },
      { label: t('notification.eventScheduledFailed'), value: 'scheduled_task_failed' },
    ],
  },
  {
    label: t('notification.groupSystem'),
    events: [
      { label: t('notification.eventSystemStart'), value: 'system_start' },
    ],
  },
  {
    label: t('notification.groupFallback'),
    events: [
      { label: t('notification.eventFallbackSearch'), value: 'fallback_search_complete' },
      { label: t('notification.eventPredownload'), value: 'predownload_complete' },
      { label: t('notification.eventMatchFallback'), value: 'match_fallback_complete' },
    ],
  },
  {
    label: t('notification.groupTaskProgress'),
    events: [
      { label: t('notification.eventTaskProgress'), value: 'task_progress' },
    ],
  },
]

// 扁平化所有事件（静态 value 列表，用于序列化，无需翻译）
const ALL_EVENT_VALUES = [
  'import_success', 'import_failed', 'refresh_success', 'refresh_failed',
  'auto_import_success', 'auto_import_failed', 'webhook_triggered',
  'webhook_import_success', 'webhook_import_failed', 'incremental_refresh_success',
  'incremental_refresh_failed', 'media_scan_complete', 'scheduled_task_complete',
  'scheduled_task_failed', 'system_start', 'fallback_search_complete',
  'predownload_complete', 'match_fallback_complete', 'task_progress',
]

export const Notification = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const EVENT_GROUPS = getEventGroups(t)
  const [channels, setChannels] = useState([])
  const [channelTypes, setChannelTypes] = useState([])
  const [loading, setLoading] = useState(true)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingChannel, setEditingChannel] = useState(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState({})
  const [webhookApiKey, setWebhookApiKey] = useState('')
  const [form] = Form.useForm()

  // 模板相关状态
  const [templates, setTemplates] = useState([])
  const [templatesLoading, setTemplatesLoading] = useState(false)
  const [templateEditorVisible, setTemplateEditorVisible] = useState(false)
  const [editingTemplateId, setEditingTemplateId] = useState(null)

  // 监听 channelType 和 config 变化以实现 visibleWhen
  const selectedType = Form.useWatch('channelType', form)
  const configValues = Form.useWatch('config', form)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [typesRes, channelsRes, apiKeyRes] = await Promise.all([
        getNotificationChannelTypes(),
        getNotificationChannels(),
        getWebhookApikey(),
      ])
      setChannelTypes(typesRes.data || [])
      setChannels(channelsRes.data || [])
      setWebhookApiKey(apiKeyRes.data?.value || '')
    } catch (e) {
      message.error(t('notification.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  // 加载模板列表
  const loadTemplates = useCallback(async () => {
    setTemplatesLoading(true)
    try {
      const res = await getNotificationTemplates()
      setTemplates(Array.isArray(res.data) ? res.data : [])
    } catch (e) {
      console.error('加载模板失败:', e)
      setTemplates([]) // 确保失败时也设置为空数组
    } finally {
      setTemplatesLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    loadTemplates()
  }, [loadData, loadTemplates])

  const getSchemaForType = (type) => {
    const found = channelTypes.find(t => t.channelType === type)
    return found?.configSchema || []
  }

  const getHideProxyForType = (type) => {
    const found = channelTypes.find(t => t.channelType === type)
    return found?.hideProxy || false
  }

  // 根据渠道类型自动生成名称：第一个 "Telegram"，第二个 "Telegram 1"，以此类推
  const generateChannelName = (channelType) => {
    const typeInfo = channelTypes.find(t => t.channelType === channelType)
    const baseName = getLocalizedField(typeInfo, 'displayName') || channelType
    const existing = channels.filter(c => c.channelType === channelType)
    if (existing.length === 0) return baseName
    return `${baseName} ${existing.length}`
  }

  const validatePublicDomain = async ({ silent = false } = {}) => {
    try {
      const response = await validateNotificationPublicDomain()
      if (!response.data?.ok) throw new Error(response.data?.detail || t('notification.publicDomainInvalid'))
      if (!silent) message.success(t('notification.publicDomainValid'))
      return true
    } catch (error) {
      const detail = error.response?.data?.detail || error.message || t('notification.publicDomainInvalid')
      message.error(t('notification.publicDomainInvalidDetail', { detail }))
      return false
    }
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

  const handleEdit = async (record) => {
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
    // why：已有渠道可能保存了外链模式，但全局域名已被修改或反代失效；
    // 打开编辑时立即复检并告警，避免用户误以为当前配置仍然可用。
    if (record.config?.image_mode === 'public_url') {
      await validatePublicDomain({ silent: true })
    }
  }

  const handleDelete = async (id) => {
    try {
      await deleteNotificationChannel(id)
      message.success(t('notification.deleteSuccess'))
      loadData()
    } catch { message.error(t('notification.deleteFailed')) }
  }

  const handleTest = async (id) => {
    setTesting(prev => ({ ...prev, [id]: true }))
    try {
      const res = await testNotificationChannel(id)
      const data = res.data
      if (data.success) {
        message.success(data.message || t('notification.testSuccess'))
      } else {
        message.error(data.message || t('notification.testFailed'))
      }
    } catch { message.error(t('notification.testRequestFailed')) }
    finally { setTesting(prev => ({ ...prev, [id]: false })) }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      // 保存时再次校验，防止初始化值、脚本赋值或切换请求竞态绕过 onChange 校验。
      if (values.config?.image_mode === 'public_url') {
        const valid = await validatePublicDomain({ silent: true })
        if (!valid) return
      }
      setSaving(true)
      const eventsObj = {}
      ALL_EVENT_VALUES.forEach(v => { eventsObj[v] = (values.eventsConfig || []).includes(v) })
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
        message.success(t('notification.updateSuccess'))
      } else {
        await createNotificationChannel(payload)
        message.success(t('notification.createSuccess'))
      }
      setModalVisible(false)
      loadData()
    } catch (e) {
      if (e.errorFields) return // form validation
      message.error(t('notification.saveFailed'))
    } finally { setSaving(false) }
  }

  // 打开模板编辑器
  const handleEditTemplate = (templateId) => {
    setEditingTemplateId(templateId)
    setTemplateEditorVisible(true)
  }

  // 模板保存后刷新列表
  const handleTemplateSaved = () => {
    loadTemplates()
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
              {field.switchLabels?.unchecked || t('notification.optionA')}
            </Select.Option>
            <Select.Option value={field.switchValues?.checked || 'webhook'}>
              {field.switchLabels?.checked || t('notification.optionB')}
            </Select.Option>
          </Select>
        </Form.Item>
      )
    }
    if (field.type === 'segmented') {
      // 三档拨动开关（如图片发送模式：纯文字 / 海报 / 图片模式）
      return (
        <Form.Item key={field.key} label={field.label} name={name}
          tooltip={field.description} initialValue={field.default}>
          <Segmented block options={(field.options || []).map(o => ({
            label: getLocalizedField(o, 'label') || o.label || o.value,
            value: o.value,
          }))} onChange={async (value) => {
            if (field.key !== 'image_mode' || value !== 'public_url') return
            const valid = await validatePublicDomain()
            if (!valid && form.getFieldValue(name) === 'public_url') {
              // why：切换失败立即回退，避免表单显示外链模式但保存后只能静默降级；
              // 请求返回前用户若已切到其他模式，则不覆盖用户的新选择。
              form.setFieldValue(name, 'poster')
            }
          }} />
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
          tooltip={field.description} rules={field.required ? [{ required: true, message: t('notification.requiredMsg', { label: field.label }) }] : []}>
          <Input.Password placeholder={field.placeholder} />
        </Form.Item>
      )
    }
    return (
      <Form.Item key={field.key} label={field.label} name={name}
        tooltip={field.description} rules={field.required ? [{ required: true, message: t('notification.requiredMsg', { label: field.label }) }] : []}>
        <Input placeholder={field.placeholder} />
      </Form.Item>
    )
  }

  const currentSchema = getSchemaForType(selectedType)
  const currentHideProxy = getHideProxyForType(selectedType)

  const columns = [
    { title: t('notification.colName'), dataIndex: 'name', key: 'name' },
    {
      title: t('notification.colType'), dataIndex: 'channelType', key: 'channelType',
      render: (v) => {
        const ct = channelTypes.find(ct => ct.channelType === v)
        return <Tag>{getLocalizedField(ct, 'displayName') || v}</Tag>
      },
    },
    {
      title: t('notification.colStatus'), dataIndex: 'isEnabled', key: 'isEnabled',
      render: (v) => v ? <Tag color="green">{t('notification.statusEnabled')}</Tag> : <Tag color="default">{t('notification.statusDisabled')}</Tag>,
    },
    {
      title: t('notification.colMode'), key: 'mode',
      render: (_, r) => {
        const mode = r.config?.mode
        // QQ Bot 使用 WebSocket Gateway 模式
        if (r.channelType === 'qq') {
          return <Tag color="cyan">WebSocket</Tag>
        }
        // 企业微信使用 Webhook 模式
        const isWebhook = mode === 'webhook' || r.channelType === 'wechat'
        return isWebhook ? <Tag color="blue">{t('notification.modeWebhook')}</Tag> : <Tag>{t('notification.modePoll')}</Tag>
      },
    },
    {
      title: t('notification.colActions'), key: 'actions', width: 260,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title={t('notification.tooltipTest')}>
            <Button size="small" icon={<ApiOutlined />}
              loading={testing[record.id]} onClick={() => handleTest(record.id)} />
          </Tooltip>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>{t('notification.btnEdit')}</Button>
          <Popconfirm title={t('notification.confirmDelete')} onConfirm={() => handleDelete(record.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
            <Button size="small" danger icon={<DeleteOutlined />}>{t('notification.btnDelete')}</Button>
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
          {record.isEnabled ? <Tag color="green">{t('notification.statusEnabled')}</Tag> : <Tag color="default">{t('notification.statusDisabled')}</Tag>}
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
          <Tag>{getLocalizedField(typeInfo, 'displayName') || record.channelType}</Tag>
          {/* QQ Bot 使用 WebSocket Gateway 模式 */}
          {record.channelType === 'qq' ? (
            <Tag color="cyan">WebSocket</Tag>
          ) : (
            (mode === 'webhook' || record.channelType === 'wechat') ? <Tag color="blue">{t('notification.modeWebhook')}</Tag> : <Tag>{t('notification.modePoll')}</Tag>
          )}
          {record.useProxy && <Tag color="orange">{t('notification.modeProxy')}</Tag>}
        </div>
        <Space size="small" wrap>
          <Tooltip title={t('notification.tooltipTest')}>
            <Button size="small" icon={<ApiOutlined />}
              loading={testing[record.id]} onClick={() => handleTest(record.id)}>{t('notification.btnTest')}</Button>
          </Tooltip>
          <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>{t('notification.btnEdit')}</Button>
          <Popconfirm title={t('notification.confirmDelete')} onConfirm={() => handleDelete(record.id)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
            <Button size="small" danger icon={<DeleteOutlined />}>{t('notification.btnDelete')}</Button>
          </Popconfirm>
        </Space>
      </div>
    )
  }

  return (
    <div>
      {/* 通知模板卡片区域 */}
      <Card
        title={t('notificationTemplate.cardTitle')}
        style={{ marginBottom: 16 }}
        loading={templatesLoading}
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
          {t('notificationTemplate.cardDescription')}
        </Text>
        <Row gutter={[16, 16]}>
          {(templates || []).map(template => (
            <Col xs={24} sm={12} md={8} lg={6} key={template.templateId}>
              <Card
                size="small"
                hoverable
                onClick={() => handleEditTemplate(template.templateId)}
                style={{ cursor: 'pointer', height: '100%' }}
              >
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Text strong>{template.displayName}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {template.description}
                  </Text>
                  <Button
                    type="link"
                    size="small"
                    icon={<FormOutlined />}
                    style={{ padding: 0 }}
                  >
                    {t('notificationTemplate.editButton')}
                  </Button>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 通知渠道卡片 */}
      <Card
        title={t('notification.channelTitle')}
        extra={
          isMobile ? (
            <Space size="small">
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading} size="small" />
              <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd} size="small">{t('notification.btnAdd')}</Button>
            </Space>
          ) : (
            <Space>
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={loading}>{t('notification.btnRefresh')}</Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>{t('notification.btnAddChannel')}</Button>
            </Space>
          )
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : channels.length === 0 ? (
          <Empty description={t('notification.emptyDesc')} />
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
        title={editingChannel ? t('notification.editTitle') : t('notification.addTitle')}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        width={560}
        height="85vh"
        footer={
          <div style={{ display: 'flex', gap: 8, justifyContent: isMobile ? 'stretch' : 'flex-end' }}>
            <Button onClick={() => setModalVisible(false)} block={isMobile}>{t('notification.btnCancel')}</Button>
            <Button type="primary" onClick={handleSave} loading={saving} block={isMobile}>{t('notification.btnSave')}</Button>
          </div>
        }
      >
        <Form form={form} layout="vertical">
          {/* 渠道名称 + 渠道类型并排在同一行（移动端退化为单列） */}
          <Row gutter={16}>
            <Col span={isMobile ? 24 : 12}>
              <Form.Item label={t('notification.fieldName')} name="name" rules={[{ required: true, message: t('notification.fieldNameRequired') }]}>
                <Input placeholder={t('notification.fieldNamePlaceholder')} />
              </Form.Item>
            </Col>
            <Col span={isMobile ? 24 : 12}>
              <Form.Item label={t('notification.fieldChannelType')} name="channelType" rules={[{ required: true }]}>
                <Select disabled={!!editingChannel} onChange={(val) => {
                  if (!editingChannel) {
                    form.setFieldsValue({ name: generateChannelName(val), config: {} })
                  }
                }}>
                  {channelTypes.map(ct => (
                    <Select.Option key={ct.channelType} value={ct.channelType}>{getLocalizedField(ct, 'displayName')}</Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={24}>
            <Col span={currentSchema.some(f => f.key === 'log_raw') ? 8 : (currentHideProxy ? 24 : 12)}>
              <Form.Item label={t('notification.fieldEnabled')} name="isEnabled" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            {!currentHideProxy && (
              <Col span={currentSchema.some(f => f.key === 'log_raw') ? 8 : 12}>
                <Form.Item label={t('notification.fieldUseProxy')} name="useProxy" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </Col>
            )}
            {currentSchema.some(f => f.key === 'log_raw') && (
              <Col span={8}>
                <Form.Item label={t('notification.fieldLogRaw')} name={['config', 'log_raw']}
                  tooltip={currentSchema.find(f => f.key === 'log_raw')?.description}
                  valuePropName="checked" initialValue={false}>
                  <Switch />
                </Form.Item>
              </Col>
            )}
          </Row>

          {/* 渲染 schema 字段，布局规则（优先级从高到低）：
              1. schema 声明 rowGroup 的字段 → 同组字段收集后并排渲染（无论在 schema 中是否相邻）
              2. 相邻两个 boolean 开关 → 两列并排
              3. 其余字段（switch/segmented/slider 及普通输入框）→ 独占整行
              why：halfWidth 依赖相邻顺序，当两个需要并排的字段中间夹着 switch/slider 等
              其他字段时（如 allowed_ids 和 telegram_api_proxy 之间有 mode 字段）会配对失败；
              rowGroup 按分组名称收集，不受字段顺序影响，可精确指定任意字段并排。 */}
          {(() => {
            const visibleFields = currentSchema.filter(f => f.key !== 'log_raw' && isFieldVisible(f))

            // 第一步：按 schema 顺序收集 rowGroup，记录每组第一次出现的位置
            // 结构：{ groupName: { firstIndex: number, fields: field[] } }
            const rowGroupMap = {}
            visibleFields.forEach((f, idx) => {
              if (!f.rowGroup) return
              if (!rowGroupMap[f.rowGroup]) {
                rowGroupMap[f.rowGroup] = { firstIndex: idx, fields: [] }
              }
              rowGroupMap[f.rowGroup].fields.push(f)
            })
            // 已被分组消费的字段 key 集合，渲染时跳过
            const consumedKeys = new Set(
              Object.values(rowGroupMap).flatMap(g => g.fields.map(f => f.key))
            )

            // 第二步：按 schema 顺序遍历，在分组第一个字段的位置整体渲染该组，其余跳过
            const nodes = []
            for (let i = 0; i < visibleFields.length; i++) {
              const field = visibleFields[i]
              const nextField = visibleFields[i + 1]

              // 优先级 1：rowGroup 分组 — 在组内第一个字段的位置渲染整组
              if (field.rowGroup && rowGroupMap[field.rowGroup]?.firstIndex === i) {
                const groupFields = rowGroupMap[field.rowGroup].fields
                if (!isMobile && groupFields.length > 1) {
                  // 桌面端：并排渲染，列宽按组内字段数均分
                  const colSpan = Math.floor(24 / groupFields.length)
                  nodes.push(
                    <Row gutter={16} key={`rowgroup-${field.rowGroup}`}>
                      {groupFields.map(gf => (
                        <Col span={colSpan} key={gf.key}>{renderConfigField(gf)}</Col>
                      ))}
                    </Row>
                  )
                } else {
                  // 移动端：组内所有字段逐一垂直渲染，不能只渲染首字段
                  groupFields.forEach(gf => nodes.push(renderConfigField(gf)))
                }
                continue
              }

              // 已被分组消费的非首字段：跳过（已在首字段位置整组渲染）
              if (consumedKeys.has(field.key)) continue

              // 优先级 2：相邻两个 boolean 开关自动配对
              if (!isMobile && field.type === 'boolean' && nextField?.type === 'boolean'
                  && !nextField.rowGroup && !field.rowGroup) {
                nodes.push(
                  <Row gutter={16} key={`pair-bool-${field.key}`}>
                    <Col span={12}>{renderConfigField(field)}</Col>
                    <Col span={12}>{renderConfigField(nextField)}</Col>
                  </Row>
                )
                i++
                continue
              }

              // 其余字段独占整行
              nodes.push(renderConfigField(field))
            }
            return nodes
          })()}

          {/* 编辑已有渠道 + webhook 模式时，展示完整回调地址 */}
          {editingChannel?.id && (configValues?.mode === 'webhook' || selectedType === 'wechat') && (() => {
            const webhookUrl = `${(configValues?.server_url || configValues?.webhook_base_url || window.location.origin).replace(/\/$/, '')}/api/notification/channels/${editingChannel.id}/webhook?api_key=${webhookApiKey}`
            return (
              <Form.Item label={t('notification.fieldWebhookUrl')}>
                <Space.Compact style={{ width: '100%' }}>
                  <Input readOnly value={webhookUrl} />
                  <Button
                    type="primary"
                    icon={<CopyOutlined />}
                    onClick={() => { copy(webhookUrl); message.success(t('notification.copyWebhookUrl')) }}
                  />
                </Space.Compact>
              </Form.Item>
            )
          })()}

          <Form.Item label={t('notification.fieldEvents')} name="eventsConfig">
            <Select
              mode="multiple"
              placeholder={t('notification.eventPlaceholder')}
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

      {/* 模板编辑器弹窗 */}
      <NotificationTemplateEditor
        visible={templateEditorVisible}
        templateId={editingTemplateId}
        onClose={() => setTemplateEditorVisible(false)}
        onSaved={handleTemplateSaved}
      />
    </div>
  )
}
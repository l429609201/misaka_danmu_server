import {
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tooltip,
} from 'antd'
import { useEffect, useState } from 'react'
import {
  getWebhookApikey,
  getWebhookServices,
  refreshWebhookApikey,
  getWebhookSettings,
  setWebhookSettings,
  generateRegex,
} from '../../../apis'
import {
  CopyOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import copy from 'copy-to-clipboard'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

export const Webhook = () => {
  const { t } = useTranslation()
  const [isLoading, setLoading] = useState(true)
  const [isSaving, setSaving] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [services, setServices] = useState([])
  const [aiRegexModalOpen, setAiRegexModalOpen] = useState(false)
  const [aiRegexDesc, setAiRegexDesc] = useState('')
  const [aiRegexLoading, setAiRegexLoading] = useState(false)
  const [aiRegexResult, setAiRegexResult] = useState('')
  const messageApi = useMessage()
  const [form] = Form.useForm()

  // 动态监听表单中的值，以便实时更新UI
  const webhookEnabled = Form.useWatch('webhookEnabled', form)
  const isDelayedImportEnabled = Form.useWatch(
    'webhookDelayedImportEnabled',
    form
  )
  const domain = Form.useWatch('webhookCustomDomain', form)

  const getApiKey = async () => {
    const res = await getWebhookApikey()
    return res.data?.value || ''
  }
  const getServices = async () => {
    const res = await getWebhookServices()
    return res.data
  }

  const getInfo = async () => {
    setLoading(true)
    try {
      const [apiKeyRes, servicesRes, settingsRes] = await Promise.all([
        getApiKey(),
        getWebhookServices(),
        getWebhookSettings(),
      ])
      setApiKey(apiKeyRes)
      setServices(servicesRes.data)
      // 使用 setFieldsValue 将从后端获取的设置填充到表单中
      form.setFieldsValue(settingsRes.data)
    } catch (error) {
      messageApi.error(t('webhook.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const onRefresh = async () => {
    try {
      const res = await refreshWebhookApikey()
      setApiKey(res.data.value)
      messageApi.success(t('webhook.apiKeyRefreshed'))
    } catch (error) {
      messageApi.error(t('webhook.apiKeyRefreshFailed'))
    }
  }

  const onSave = async values => {
    try {
      setSaving(true)
      // 修正：确保所有字段都存在，即使它们的值是 undefined 或 null。
      // 为这些字段提供合理的默认值，以确保发送到后端的对象结构始终完整且有效。
      const payload = {
        webhookEnabled: values.webhookEnabled ?? false,
        webhookDelayedImportEnabled:
          values.webhookDelayedImportEnabled ?? false,
        webhookDelayedImportHours: values.webhookDelayedImportHours ?? 24,
        webhookCustomDomain: values.webhookCustomDomain ?? '',
        webhookFilterMode: values.webhookFilterMode ?? 'blacklist',
        webhookFilterRegex: values.webhookFilterRegex ?? '',
        webhookLogRawRequest: values.webhookLogRawRequest ?? false,
        webhookFallbackEnabled: values.webhookFallbackEnabled ?? false,
        webhookEnableTmdbSeasonMapping: values.webhookEnableTmdbSeasonMapping ?? false,
        webhookDeleteSyncEnabled: values.webhookDeleteSyncEnabled ?? false,
      }
      await setWebhookSettings(payload)
      messageApi.success(t('webhook.saveSuccess'))
    } catch (error) {
      messageApi.error(t('webhook.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    getInfo()
  }, []) // eslint-disable-line

  const handleAiGenerate = async () => {
    if (!aiRegexDesc.trim()) {
      messageApi.warning(t('webhook.inputDescription'))
      return
    }
    setAiRegexLoading(true)
    setAiRegexResult('')
    try {
      const existing = form.getFieldValue('webhookFilterRegex') || ''
      const res = await generateRegex(aiRegexDesc.trim(), existing, 'webhook_filter')
      if (res.data?.regex) {
        setAiRegexResult(res.data.regex)
      } else {
        messageApi.error(t('webhook.aiNoValidRegex'))
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || t('webhook.aiRegexGenFailed'))
    } finally {
      setAiRegexLoading(false)
    }
  }

  const handleApplyAiRegex = () => {
    if (!aiRegexResult) return
    form.setFieldValue('webhookFilterRegex', aiRegexResult)
    setAiRegexModalOpen(false)
    setAiRegexDesc('')
    setAiRegexResult('')
    messageApi.success(t('webhook.aiRuleApplied'))
  }

  return (
    <div className="my-6">
      <Card loading={isLoading} title={t('webhook.title')}>
        <div>
          <div className="mb-3">
            {t('webhook.desc')}
          </div>
          <div className="mb-4">{t('webhook.urlFormat')}</div>
          <div className="flex items-center justify-start gap-3 mb-4">
            <div className="shrink-0 w-auto md:w-[120px]">API Key:</div>
            <div className="w-full">
              <Space.Compact style={{ width: '100%' }}>
                <Input readOnly value={apiKey} />
                <Button
                  type="primary"
                  icon={<ReloadOutlined />}
                  onClick={onRefresh}
                />
              </Space.Compact>
            </div>
          </div>
        </div>
        <Divider />
        <Form form={form} layout="vertical" onFinish={onSave}>
          <Form.Item
            label={<div className="text-base font-medium">{t('webhook.webhookControl')}</div>}
          >
            <Row gutter={[16, 12]} align={'stretch'}>
              <Col md={5} xs={12}>
                <div className="h-full flex items-center gap-2">
                  <span>{t('webhook.enableWebhook')}</span>
                  <Form.Item
                    name="webhookEnabled"
                    valuePropName="checked"
                    noStyle
                  >
                    <Switch />
                  </Form.Item>
                </div>
              </Col>
              <Col md={5} xs={12}>
                <div className="h-full flex items-center gap-2">
                  <span>{t('webhook.enableDelayImport')}</span>
                  <Tooltip
                    title={t('webhook.delayImportTip')}
                    placement="top"
                  >
                    <InfoCircleOutlined />
                  </Tooltip>
                  <Form.Item
                    name="webhookDelayedImportEnabled"
                    valuePropName="checked"
                    noStyle
                  >
                    <Switch disabled={!webhookEnabled} />
                  </Form.Item>
                </div>
              </Col>
              <Col md={6} xs={12}>
                <div className="h-full flex items-center gap-2">
                  <span>{t('webhook.customDelayHours')}</span>
                  <Form.Item name="webhookDelayedImportHours" noStyle>
                    <InputNumber
                      min={1}
                      disabled={!webhookEnabled || !isDelayedImportEnabled}
                    />
                  </Form.Item>
                </div>
              </Col>
              <Col md={6} xs={12}>
                <div className="h-full flex items-center gap-2">
                  <span>{t('webhook.recordRawRequest')}</span>
                  <Form.Item
                    name="webhookLogRawRequest"
                    valuePropName="checked"
                    noStyle
                  >
                    <Switch disabled={!webhookEnabled} />
                  </Form.Item>
                </div>
              </Col>
            </Row>
            <div className="text-gray-400 text-xs mt-2">
              {t('webhook.webhookControlDesc')}
            </div>
          </Form.Item>

          <Form.Item label={t('webhook.filterRule')}>
            <Form.Item name="webhookFilterRegex" noStyle>
              <Input
                addonBefore={
                  <Form.Item name="webhookFilterMode" noStyle>
                    <Select
                      defaultValue="blacklist"
                      style={{ width: 100 }}
                      options={[
                        { value: 'blacklist', label: t('webhook.blacklist') },
                        { value: 'whitelist', label: t('webhook.whitelist') },
                      ]}
                    />
                  </Form.Item>
                }
                addonAfter={
                  <Tooltip title={t('webhook.aiGenRegex')}>
                    <RobotOutlined
                      style={{ cursor: 'pointer' }}
                      onClick={() => setAiRegexModalOpen(true)}
                    />
                  </Tooltip>
                }
                placeholder={t('webhook.filterPlaceholder')}
              />
            </Form.Item>
            <div className="text-gray-400 text-xs mt-1">
              {t('webhook.filterDesc')}
            </div>
          </Form.Item>

          <Form.Item name="webhookCustomDomain" label={t('webhook.customDomain')}>
            <Input placeholder={t('webhook.customDomainPlaceholder')} />
          </Form.Item>

          <Form.Item
            label={
              <div className="flex items-center gap-2">
                <span className="text-base font-medium">{t('webhook.enableFallback')}</span>
                <Tooltip
                  title={t('webhook.enableFallbackTip')}
                  placement="top"
                >
                  <InfoCircleOutlined />
                </Tooltip>
              </div>
            }
          >
            <div className="flex items-center gap-2">
              <Form.Item
                name="webhookFallbackEnabled"
                valuePropName="checked"
                noStyle
              >
                <Switch disabled={!webhookEnabled} />
              </Form.Item>
              <span className="text-gray-400 text-sm">
                {t('webhook.fallbackDesc')}
              </span>
            </div>
          </Form.Item>

          <Form.Item
            label={t('webhook.deleteSync')}
            className="mb-3"
          >
            <div className="flex items-center gap-2">
              <Form.Item
                name="webhookDeleteSyncEnabled"
                valuePropName="checked"
                noStyle
              >
                <Switch disabled={!webhookEnabled} />
              </Form.Item>
              <span className="text-gray-400 text-sm">
                {t('webhook.deleteSyncDesc')}
              </span>
            </div>
          </Form.Item>

          {webhookEnabled &&
            services?.map(it => (
              <Form.Item key={it} label={t('webhook.webhookAddress', { service: it })}>
                <Space.Compact style={{ width: '100%' }}>
                  <Input
                    readOnly
                    value={`${domain || window.location.origin}/api/webhook/${it}?api_key=${apiKey}`}
                  />
                  <Button
                    type="primary"
                    icon={<CopyOutlined />}
                    onClick={() => {
                      copy(
                        `${domain || window.location.origin}/api/webhook/${it}?api_key=${apiKey}`
                      )
                      messageApi.success(t('webhook.copySuccess'))
                    }}
                  />
                </Space.Compact>
              </Form.Item>
            ))}

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={isSaving}>
              {t('webhook.saveSettings')}
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Modal
        title={<><RobotOutlined /> {t('webhook.aiRegexAssistant')}</>}
        open={aiRegexModalOpen}
        onCancel={() => { setAiRegexModalOpen(false); setAiRegexResult('') }}
        footer={null}
        destroyOnClose
      >
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-600 mb-2">
              {t('webhook.aiRegexDesc')}
            </div>
            <Input.TextArea
              value={aiRegexDesc}
              onChange={e => setAiRegexDesc(e.target.value)}
              placeholder={t('webhook.aiRegexPlaceholder')}
              rows={3}
              onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleAiGenerate() } }}
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={aiRegexLoading}
              onClick={handleAiGenerate}
            >
              {t('webhook.generate')}
            </Button>
          </div>
          {aiRegexResult && (
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('webhook.generateResult')}</div>
              <div className="bg-gray-50 border rounded p-3 font-mono text-sm break-all">
                {aiRegexResult}
              </div>
              <div className="flex justify-end mt-3">
                <Space>
                  <Button onClick={() => setAiRegexResult('')}>{t('webhook.clear')}</Button>
                  <Button type="primary" onClick={handleApplyAiRegex}>
                    {t('webhook.applyRule')}
                  </Button>
                </Space>
              </div>
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}

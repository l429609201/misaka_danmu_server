import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Form, Input, Button, message, Select, Space, Alert, Card, Typography,
} from 'antd'
import { ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { ResponsiveModal } from '../../../components/ResponsiveModal'
import { NotificationPreview } from './NotificationPreview'
import { TemplateVariablePanel } from './TemplateVariablePanel'
import {
  getNotificationTemplate,
  updateNotificationTemplate,
  previewNotificationTemplate,
} from '../../../apis'

const { TextArea } = Input
const { Text } = Typography

/**
 * 通知模板编辑器 - 通用编辑组件
 * 支持 5 个模板场景的编辑、变量插入、实时预览
 */
export const NotificationTemplateEditor = ({ visible, templateId, onClose, onSaved }) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validationError, setValidationError] = useState(null)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [templateData, setTemplateData] = useState(null)
  const [previewData, setPreviewData] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  // 光标位置跟踪
  const [activeField, setActiveField] = useState('body') // 'title' | 'body'
  const [cursorPosition, setCursorPosition] = useState({ start: 0, end: 0 })
  const titleInputRef = useRef(null)
  const bodyTextAreaRef = useRef(null)

  // 防抖预览
  const previewTimerRef = useRef(null)

  // 加载模板数据
  const loadTemplate = useCallback(async () => {
    if (!templateId) return
    setLoading(true)
    try {
      const res = await getNotificationTemplate(templateId)
      const data = res.data
      setTemplateData(data)
      form.setFieldsValue({
        title: data.title || '',
        body: data.body || '',
      })
      setValidationError(null)
      setHasUnsavedChanges(false)
      // 初始预览
      triggerPreview(data.title || '', data.body || '', 'telegram', 'success')
    } catch (e) {
      message.error(t('notificationTemplate.loadFailed'))
      console.error('加载模板失败:', e)
    } finally {
      setLoading(false)
    }
  }, [templateId, form, t])

  useEffect(() => {
    if (visible && templateId) {
      loadTemplate()
    }
  }, [visible, templateId, loadTemplate])

  // 防抖预览请求
  const triggerPreview = useCallback((title, body, channel, status) => {
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current)
    previewTimerRef.current = setTimeout(async () => {
      if (!templateId) return
      setPreviewLoading(true)
      try {
        const res = await previewNotificationTemplate({
          templateId,
          title,
          body,
          channel: channel || 'telegram',
          exampleStatus: status || 'success',
        })
        setPreviewData(res.data)
        setValidationError(res.data.error || null)
      } catch (e) {
        console.error('预览失败:', e)
        setValidationError(t('notificationTemplate.previewFailed'))
      } finally {
        setPreviewLoading(false)
      }
    }, 300)
  }, [templateId, t])

  // 表单值变化时触发预览
  const handleValuesChange = useCallback((_, allValues) => {
    setHasUnsavedChanges(true)
    triggerPreview(allValues.title, allValues.body, 'telegram', 'success')
  }, [triggerPreview])

  // 跟踪光标位置
  const handleFocus = useCallback((field) => {
    setActiveField(field)
  }, [])

  const handleSelect = useCallback((field, e) => {
    setActiveField(field)
    const { selectionStart, selectionEnd } = e.target
    setCursorPosition({ start: selectionStart, end: selectionEnd })
  }, [])

  const handleClick = useCallback((field, e) => {
    setActiveField(field)
    const { selectionStart, selectionEnd } = e.target
    setCursorPosition({ start: selectionStart, end: selectionEnd })
  }, [])

  const handleKeyUp = useCallback((field, e) => {
    const { selectionStart, selectionEnd } = e.target
    setCursorPosition({ start: selectionStart, end: selectionEnd })
  }, [])

  // 插入变量到光标位置
  const insertVariable = useCallback((variableName) => {
    const values = form.getFieldsValue()
    const text = activeField === 'title' ? values.title : values.body
    const variable = `{{ ${variableName} }}`
    const newText = text.slice(0, cursorPosition.start) + variable + text.slice(cursorPosition.end)

    form.setFieldsValue({ [activeField]: newText })
    setHasUnsavedChanges(true)

    // 更新光标位置到变量后面
    const newCursorPos = cursorPosition.start + variable.length
    setCursorPosition({ start: newCursorPos, end: newCursorPos })

    // 下一帧重新聚焦并设置光标
    setTimeout(() => {
      const ref = activeField === 'title' ? titleInputRef : bodyTextAreaRef
      const inputElement = activeField === 'title'
        ? ref.current?.input
        : ref.current?.resizableTextArea?.textArea

      if (inputElement) {
        inputElement.focus()
        inputElement.setSelectionRange(newCursorPos, newCursorPos)
      }
    }, 0)

    // 触发预览
    triggerPreview(
      activeField === 'title' ? newText : values.title,
      activeField === 'body' ? newText : values.body,
      'telegram',
      'success'
    )
  }, [activeField, cursorPosition, form, triggerPreview])

  // 恢复默认
  const handleReset = useCallback(() => {
    if (!templateData?.defaultTitle || !templateData?.defaultBody) {
      message.warning(t('notificationTemplate.noDefault'))
      return
    }
    form.setFieldsValue({
      title: templateData.defaultTitle,
      body: templateData.defaultBody,
    })
    setHasUnsavedChanges(true)
    triggerPreview(templateData.defaultTitle, templateData.defaultBody, 'telegram', 'success')
    message.info(t('notificationTemplate.resetSuccess'))
  }, [templateData, form, triggerPreview, t])

  // 保存模板
  const handleSave = useCallback(async () => {
    if (validationError) {
      message.error(t('notificationTemplate.fixErrorsFirst'))
      return
    }

    try {
      const values = await form.validateFields()
      setSaving(true)

      await updateNotificationTemplate(templateId, {
        title: values.title,
        body: values.body,
      })

      message.success(t('notificationTemplate.saveSuccess'))
      setHasUnsavedChanges(false)
      onSaved?.()
    } catch (e) {
      if (e.errorFields) return // 表单验证错误
      message.error(t('notificationTemplate.saveFailed'))
      console.error('保存模板失败:', e)
    } finally {
      setSaving(false)
    }
  }, [validationError, form, templateId, onSaved, t])

  // 关闭前确认
  const handleClose = useCallback(() => {
    if (hasUnsavedChanges) {
      if (!window.confirm(t('notificationTemplate.unsavedWarning'))) {
        return
      }
    }
    onClose()
  }, [hasUnsavedChanges, onClose, t])

  // 预览渠道/状态变化
  const handlePreviewChange = useCallback((channel, status) => {
    const values = form.getFieldsValue()
    triggerPreview(values.title, values.body, channel, status)
  }, [form, triggerPreview])

  return (
    <ResponsiveModal
      visible={visible}
      title={templateData?.displayName || t('notificationTemplate.editorTitle')}
      onCancel={handleClose}
      width={1200}
      footer={null}
      destroyOnClose
    >
      <div style={{ display: 'flex', gap: 16, minHeight: 600 }}>
        {/* 左栏：编辑区 60% */}
        <div style={{ flex: '0 0 60%', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 场景说明 */}
          {templateData?.description && (
            <Alert message={templateData.description} type="info" showIcon />
          )}

          {/* 编辑表单 */}
          <Card>
            <Form form={form} layout="vertical" onValuesChange={handleValuesChange}>
              {/* 标题模板 */}
              <Form.Item
                label={
                  <Space>
                    <Text>{t('notificationTemplate.titleLabel')}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {form.getFieldValue('title')?.length || 0} / 200
                    </Text>
                    {activeField === 'title' && (
                      <Text type="warning" style={{ fontSize: 12 }}>●</Text>
                    )}
                  </Space>
                }
                name="title"
                rules={[
                  { required: true, message: t('notificationTemplate.titleRequired') },
                  { max: 200, message: t('notificationTemplate.titleTooLong') },
                ]}
              >
                <Input
                  ref={titleInputRef}
                  placeholder={t('notificationTemplate.titlePlaceholder')}
                  onFocus={() => handleFocus('title')}
                  onSelect={(e) => handleSelect('title', e)}
                  onClick={(e) => handleClick('title', e)}
                  onKeyUp={(e) => handleKeyUp('title', e)}
                />
              </Form.Item>

              {/* 正文模板 */}
              <Form.Item
                label={
                  <Space>
                    <Text>{t('notificationTemplate.bodyLabel')}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {form.getFieldValue('body')?.length || 0} / 4000
                    </Text>
                    {activeField === 'body' && (
                      <Text type="warning" style={{ fontSize: 12 }}>●</Text>
                    )}
                  </Space>
                }
                name="body"
                rules={[
                  { required: true, message: t('notificationTemplate.bodyRequired') },
                  { max: 4000, message: t('notificationTemplate.bodyTooLong') },
                ]}
              >
                <TextArea
                  ref={bodyTextAreaRef}
                  placeholder={t('notificationTemplate.bodyPlaceholder')}
                  rows={10}
                  style={{ fontFamily: 'monospace' }}
                  onFocus={() => handleFocus('body')}
                  onSelect={(e) => handleSelect('body', e)}
                  onClick={(e) => handleClick('body', e)}
                  onKeyUp={(e) => handleKeyUp('body', e)}
                />
              </Form.Item>

              {/* 校验状态 */}
              {validationError && (
                <Alert message={validationError} type="error" showIcon style={{ marginBottom: 16 }} />
              )}
              {!validationError && previewData && (
                <Alert message={t('notificationTemplate.templateValid')} type="success" showIcon style={{ marginBottom: 16 }} />
              )}
            </Form>
          </Card>

          {/* 变量面板 */}
          <TemplateVariablePanel
            variables={templateData?.variables || []}
            onInsert={insertVariable}
          />

          {/* 底部操作 */}
          <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
            <Button onClick={handleReset} icon={<ReloadOutlined />}>
              {t('notificationTemplate.resetButton')}
            </Button>
            <Button onClick={handleClose}>
              {t('common.cancel')}
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={saving}
              disabled={!!validationError}
            >
              {t('notificationTemplate.saveButton')}
            </Button>
          </Space>
        </div>

        {/* 右栏：预览区 40% */}
        <div style={{ flex: '0 0 40%' }}>
          <NotificationPreview
            previewData={previewData}
            loading={previewLoading}
            onChannelChange={(channel) => handlePreviewChange(channel, 'success')}
            onStatusChange={(status) => handlePreviewChange('telegram', status)}
          />
        </div>
      </div>
    </ResponsiveModal>
  )
}


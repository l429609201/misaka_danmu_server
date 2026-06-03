import { Form, Input, Modal, Select } from 'antd'
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { addSourceToAnime, getScrapers } from '../apis'
import { useMessage } from '../MessageContext'
import { MyIcon } from '@/components/MyIcon'
import { generateRandomStr } from '../utils/data'

export const AddSourceModal = ({ open, animeId, onCancel, onSuccess }) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [providerOptions, setProviderOptions] = useState([
    { value: 'custom', label: t('addSource.custom') },
  ])
  const messageApi = useMessage()

  // 弹窗打开时动态加载已注册的弹幕源列表
  useEffect(() => {
    if (!open) return
    const loadProviders = async () => {
      try {
        const res = await getScrapers()
        const scraperList = res.data || []
        const dynamicOptions = scraperList.map(s => ({
          value: s.providerName,
          label: s.displayName || s.providerName,
        }))
        // "自定义" 始终在最前
        setProviderOptions([
          { value: 'custom', label: t('addSource.custom') },
          ...dynamicOptions,
        ])
      } catch {
        // 加载失败时保留默认的 custom 选项
      }
    }
    loadProviders()
  }, [open])

  const handleOk = async () => {
    if (!animeId) return
    try {
      const values = await form.validateFields()
      setLoading(true)
      // 修正：将 animeId 和表单值合并成一个对象再传递
      const res = await addSourceToAnime({ ...values, animeId })
      if (res.data) {
        messageApi.success(t('addSource.addSuccess'))
        onSuccess(res.data) // 将新创建的数据源信息传递回去
        form.resetFields()
      }
    } catch (error) {
      console.error('添加数据源失败:', error)
      messageApi.error(error.detail || t('addSource.addFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={t('addSource.title')}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        name="add_source_form"
        className="!px-4 !pt-6"
      >
        <Form.Item
          name="providerName"
          label={t('addSource.platform')}
          rules={[{ required: true, message: t('addSource.selectPlatform') }]}
          initialValue="custom"
        >
          <Select
            showSearch
            options={providerOptions}
            placeholder={t('addSource.selectPlatformPlaceholder')}
          />
        </Form.Item>
        <Form.Item
          name="mediaId"
          label={t('addSource.mediaId')}
          rules={[{ required: true, message: t('addSource.inputMediaId') }]}
          help={t('addSource.mediaIdHelp')}
        >
          <Input
            placeholder={t('addSource.mediaIdPlaceholder')}
            addonAfter={
              <div
                className="cursor-pointer"
                onClick={() => {
                  const value = generateRandomStr()
                  form.setFieldsValue({
                    mediaId: value,
                  })
                }}
              >
                <MyIcon icon="refresh" size={20} />
              </div>
            }
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

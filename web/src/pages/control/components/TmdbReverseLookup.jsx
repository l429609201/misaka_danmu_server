import {
  Button,
  Card,
  Checkbox,
  Form,
  Space,
  Spin,
  Switch,
  Typography,
  Alert,
} from 'antd'
import { useEffect, useState } from 'react'
import { useMessage } from '../../../MessageContext'
import {
  getTmdbReverseLookupConfig,
  saveTmdbReverseLookupConfig,
} from '../../../apis'
import { useTranslation } from 'react-i18next'

const { Text } = Typography

export const TmdbReverseLookup = () => {
  const { t } = useTranslation()
  const [isLoading, setLoading] = useState(true)
  const [isSaving, setSaving] = useState(false)
  const messageApi = useMessage()
  const [form] = Form.useForm()

  // 动态监听表单中的值
  const enabled = Form.useWatch('enabled', form)

  // 可用的元数据源
  const availableSources = [
    { value: 'imdb', label: 'IMDB' },
    { value: 'tvdb', label: 'TVDB' },
    { value: 'douban', label: t('control.sourceDouban') },
    { value: 'bangumi', label: 'Bangumi' },
  ]

  const getConfig = async () => {
    try {
      const response = await getTmdbReverseLookupConfig()
      return response.data
    } catch (error) {
      messageApi.error(t('control.tmdbGetConfigFailed'))
      return { enabled: false, sources: ['imdb', 'tvdb'] }
    }
  }

  const saveConfig = async values => {
    try {
      const response = await saveTmdbReverseLookupConfig(values)
      return response.data
    } catch (error) {
      throw error
    }
  }

  const loadConfig = async () => {
    setLoading(true)
    try {
      const config = await getConfig()
      form.setFieldsValue(config)
    } catch (error) {
      messageApi.error(t('control.tmdbLoadConfigFailed'))
    } finally {
      setLoading(false)
    }
  }

  const onSave = async () => {
    try {
      setSaving(true)
      const values = await form.validateFields()
      await saveConfig(values)
      messageApi.success(t('control.tmdbSaveSuccess'))
    } catch (error) {
      messageApi.error(t('control.tmdbSaveFailed'))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    loadConfig()
  }, [])

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div className="my-6">
      <Card title={t('control.tmdbCardTitle')}>
        <Alert
          message={t('control.tmdbFuncDesc')}
          description={t('control.tmdbFuncDescContent')}
          type="info"
          showIcon
          className="!mb-4"
        />

        <Form
          form={form}
          layout="vertical"
          onFinish={onSave}
          className="px-6 pb-6"
        >
          <Form.Item
            name="enabled"
            label={t('control.tmdbEnable')}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>

          {enabled && (
            <Form.Item
              name="sources"
              label={t('control.tmdbEnableSources')}
              tooltip={t('control.tmdbSourcesTip')}
            >
              <Checkbox.Group
                options={availableSources}
                className="flex flex-col gap-2"
              />
            </Form.Item>
          )}

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={isSaving}>
                {t('control.settingsSaveConfig')}
              </Button>
              <Button onClick={loadConfig}>{t('control.settingsReset')}</Button>
            </Space>
          </Form.Item>
        </Form>

        {enabled && (
          <div className="mt-4 p-4 bg-base-bg rounded">
            <Text strong className="te">
              {t('control.tmdbWorkflow')}
            </Text>
            <ol className="p-0 mt-2 text-sm">
              <li>{t('control.tmdbWorkflow1')}</li>
              <li>{t('control.tmdbWorkflow2')}</li>
              <li>{t('control.tmdbWorkflow3')}</li>
              <li>{t('control.tmdbWorkflow4')}</li>
            </ol>
          </div>
        )}
      </Card>
    </div>
  )
}

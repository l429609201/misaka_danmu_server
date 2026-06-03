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
  Tooltip,
  Divider,
} from 'antd'
import { useEffect, useState } from 'react'
import { useMessage } from '../../../MessageContext'
import {
  getTmdbReverseLookupConfig,
  saveTmdbReverseLookupConfig,
  getConfig,
  setConfig,
} from '../../../apis'
import { InfoCircleOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

const { Text } = Typography

export const Settings = () => {
  const { t } = useTranslation()
  const [isLoading, setLoading] = useState(true)
  const [isSaving, setSaving] = useState(false)
  const messageApi = useMessage()
  const [form] = Form.useForm()

  // 动态监听表单中的值
  const tmdbEnabled = Form.useWatch('tmdbEnabled', form)
  const fallbackEnabled = Form.useWatch('externalApiFallbackEnabled', form)

  // 可用的元数据源
  const availableSources = [
    { value: 'imdb', label: 'IMDB' },
    { value: 'tvdb', label: 'TVDB' },
    { value: 'douban', label: t('control.sourceDouban') },
    { value: 'bangumi', label: 'Bangumi' },
  ]

  const getTmdbConfig = async () => {
    try {
      const response = await getTmdbReverseLookupConfig()
      return response.data
    } catch (error) {
      messageApi.error(t('control.settingsGetTmdbFailed'))
      return { enabled: false, sources: ['imdb', 'tvdb'] }
    }
  }

  const getFallbackConfig = async () => {
    try {
      const response = await getConfig('externalApiFallbackEnabled')
      return response.data?.value === 'true'
    } catch (error) {
      return false // 默认关闭
    }
  }

  const saveTmdbConfig = async values => {
    try {
      const response = await saveTmdbReverseLookupConfig({
        enabled: values.tmdbEnabled,
        // 当禁用TMDB反查时,sources可能为undefined,使用空数组作为默认值
        sources: values.tmdbSources || [],
      })
      return response.data
    } catch (error) {
      throw error
    }
  }

  const saveFallbackConfig = async enabled => {
    try {
      await setConfig('externalApiFallbackEnabled', enabled ? 'true' : 'false')
    } catch (error) {
      throw error
    }
  }

  const loadConfig = async () => {
    setLoading(true)
    try {
      const [tmdbConfig, fallbackConfig] = await Promise.all([
        getTmdbConfig(),
        getFallbackConfig(),
      ])

      form.setFieldsValue({
        tmdbEnabled: tmdbConfig.enabled,
        tmdbSources: tmdbConfig.sources,
        externalApiFallbackEnabled: fallbackConfig,
      })
    } catch (error) {
      messageApi.error(t('control.settingsLoadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const onSave = async () => {
    try {
      setSaving(true)
      const values = await form.validateFields()

      // 保存 TMDB 反查配置
      await saveTmdbConfig(values)

      // 保存顺延机制配置
      await saveFallbackConfig(values.externalApiFallbackEnabled)

      messageApi.success(t('control.settingsSaveSuccess'))
    } catch (error) {
      messageApi.error(t('control.settingsSaveFailed'))
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
      <Card title={t('control.settingsCardTitle')}>
        <Form
          form={form}
          layout="vertical"
          onFinish={onSave}
          className="px-6 pb-6"
        >
          {/* TMDB 反查配置 */}
          <div className="mb-6">
            <Text strong className="text-lg">
              {t('control.tmdbConfigTitle')}
            </Text>
            <Alert
              message={t('control.tmdbFuncDesc')}
              description={t('control.tmdbFuncDescContent')}
              type="info"
              showIcon
              className="!mt-2 !mb-4"
            />

            <Form.Item
              name="tmdbEnabled"
              label={t('control.tmdbEnable')}
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>

            {tmdbEnabled && (
              <Form.Item
                name="tmdbSources"
                label={t('control.tmdbEnableSources')}
                tooltip={t('control.tmdbSourcesTip')}
              >
                <Checkbox.Group
                  options={availableSources}
                  className="flex flex-col gap-2"
                />
              </Form.Item>
            )}

            {tmdbEnabled && (
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
          </div>

          <Divider />

          {/* 顺延机制配置 */}
          <div className="mb-6">
            <Text strong className="text-lg">
              {t('control.settingsCascadeTitle')}
            </Text>
            <Alert
              message={t('control.tmdbFuncDesc')}
              description={t('control.settingsCascadeDesc')}
              type="info"
              showIcon
              className="!mt-2 !mb-4"
            />

            <Form.Item
              name="externalApiFallbackEnabled"
              label={
                <div className="flex items-center gap-2">
                  <span>{t('control.settingsCascadeEnable')}</span>
                  <Tooltip
                    title={t('control.settingsCascadeTip')}
                    placement="top"
                  >
                    <InfoCircleOutlined />
                  </Tooltip>
                </div>
              }
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>

            {fallbackEnabled && (
              <div className="mt-4 p-4 bg-base-bg rounded">
                <Text strong className="te">
                  {t('control.tmdbWorkflow')}
                </Text>
                <ol className="p-0 mt-2 text-sm">
                  <li>{t('control.settingsCascadeWorkflow1')}</li>
                  <li>{t('control.settingsCascadeWorkflow2')}</li>
                  <li>{t('control.settingsCascadeWorkflow3')}</li>
                  <li>{t('control.settingsCascadeWorkflow4')}</li>
                </ol>
              </div>
            )}
          </div>

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={isSaving}>
                {t('control.settingsSaveConfig')}
              </Button>
              <Button onClick={loadConfig}>{t('control.settingsReset')}</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

import {
  CopyOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { Button, Card, Input, message, Modal, Space, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { getControlApiKey, refreshControlApiKey } from '../../../apis'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import copy from 'copy-to-clipboard'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { useTranslation } from 'react-i18next'

export const ApiKey = () => {
  const { t } = useTranslation()
  const [apikey, setApikey] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [showKey, setShowkey] = useState(false)
  const isMobile = useAtomValue(isMobileAtom)

  const modalApi = useModal()
  const messageApi = useMessage()

  useEffect(() => {
    setLoading(true)
    getControlApiKey()
      .then(res => {
        setApikey(res.data.value ?? '')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const onRefresh = () => {
    modalApi.confirm({
      title: t('control.apiKeyRefreshTitle'),
      zIndex: 1002,
      content: <div>{t('control.apiKeyRefreshConfirm')}</div>,
      okText: t('control.apiKeyConfirm'),
      cancelText: t('control.apiKeyCancel'),
      onOk: async () => {
        try {
          setRefreshing(true)
          const res = await refreshControlApiKey()
          setApikey(res.data.value ?? '')
          messageApi.success(t('control.apiKeyGenerated'))
        } catch (error) {
          messageApi.error(t('control.apiKeyGenerateFailed', { msg: error.message }))
        } finally {
          setRefreshing(false)
        }
      },
    })
  }

  return (
    <div className="my-6">
      <Card title={t('control.apiKeyCardTitle')} loading={loading}>
        <div className="mb-4">
          {t('control.apiKeyDescPrefix')} <Typography.Text code>/api/control/*</Typography.Text> {t('control.apiKeyDescMid')} <Typography.Text code>/api/mcp</Typography.Text> {t('control.apiKeyDescSuffix')}
          <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
            {t('control.apiKeyAuthPrefix')} <Typography.Text code>?api_key=key</Typography.Text> {t('control.apiKeyAuthMid')} <Typography.Text code>X-API-KEY: key</Typography.Text> {t('control.apiKeyAuthSuffix')}
          </div>
        </div>
        {isMobile ? (
          <div className="space-y-3">
            <div>
              <div className="text-sm mb-2 font-medium">API Key:</div>
              <Input.Password
                prefix={<LockOutlined className="text-gray-400" />}
                placeholder={t('control.apiKeyPlaceholderEmpty')}
                visibilityToggle={{
                  visible: showKey,
                  onVisibleChange: setShowkey,
                }}
                iconRender={visible =>
                  visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
                }
                readOnly
                value={apikey}
                size="large"
              />
            </div>
            <div className="flex gap-2">
              <Button
                loading={refreshing}
                type="primary"
                icon={<CopyOutlined />}
                onClick={() => {
                  copy(apikey)
                  messageApi.success(t('control.apiKeyCopySuccess'))
                }}
                block
              >
                {t('control.apiKeyCopy')}
              </Button>
              <Button
                loading={refreshing}
                type="primary"
                icon={<ReloadOutlined />}
                onClick={onRefresh}
                block
              >
                {t('control.apiKeyRefresh')}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-start gap-3 mb-4">
            <div className="shrink-0 w-auto md:w-[120px]">API Key:</div>
            <div className="w-full">
              <Space.Compact style={{ width: '100%' }}>
                <Input.Password
                  prefix={<LockOutlined className="text-gray-400" />}
                  placeholder={t('control.apiKeyPlaceholderEmptyRight')}
                  visibilityToggle={{
                    visible: showKey,
                    onVisibleChange: setShowkey,
                  }}
                  iconRender={visible =>
                    visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
                  }
                  readOnly
                  block
                  value={apikey}
                />

                <Button
                  loading={refreshing}
                  type="primary"
                  icon={<CopyOutlined />}
                  onClick={() => {
                    copy(apikey)
                    messageApi.success(t('control.apiKeyCopySuccess'))
                  }}
                >
                  {t('control.apiKeyCopy')}
                </Button>
                <Button
                  loading={refreshing}
                  type="primary"
                  icon={<ReloadOutlined />}
                  onClick={onRefresh}
                >
                  {t('control.apiKeyRefresh')}
                </Button>
              </Space.Compact>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}

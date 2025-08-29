import {
  CopyOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  LockOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { Button, Card, Input, message, Modal, Space } from 'antd'
import { useEffect, useState } from 'react'
import { getControlApiKey, refreshControlApiKey } from '../../../apis'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import copy from 'copy-to-clipboard'

export const ApiKey = () => {
  const [apikey, setApikey] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [showKey, setShowkey] = useState(false)

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
      title: '刷新API key',
      zIndex: 1002,
      content: <div>您确定要重新生成外部API密钥吗？旧的密钥将立即失效。</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          setRefreshing(true)
          const res = await refreshControlApiKey()
          setApikey(res.data.value ?? '')
          messageApi.success('新的API密钥已生成！')
        } catch (error) {
          messageApi.error(`生成失败: ${error.message}`)
        } finally {
          setRefreshing(false)
        }
      },
    })
  }

  return (
    <div className="my-6">
      <Card title="外部API密钥" loading={loading}>
        <div className="mb-4">
          此密钥用于所有 /api/control/* 接口的鉴权。请妥善保管，不要泄露。
        </div>
        <div className="flex items-center justify-start gap-3 mb-4">
          <div className="shrink-0 w-auto md:w-[120px]">API Key:</div>
          <div className="w-full">
            <Space.Compact style={{ width: '100%' }}>
              <Input.Password
                prefix={<LockOutlined className="text-gray-400" />}
                placeholder="未生成，请点击右侧按钮生成。"
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
                  messageApi.success('复制成功')
                }}
              />
              <Button
                loading={refreshing}
                type="primary"
                icon={<ReloadOutlined />}
                onClick={onRefresh}
              />
            </Space.Compact>
          </div>
        </div>
      </Card>
    </div>
  )
}

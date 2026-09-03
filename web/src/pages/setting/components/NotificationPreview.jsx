import { useState } from 'react'
import { Card, Segmented, Empty, Spin, Alert, Image, Typography, Space, Collapse } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

const { Text, Paragraph } = Typography
const { Panel } = Collapse

/**
 * 通知预览组件 - 实时显示模板渲染结果
 * 支持切换渠道和示例状态
 */
export const NotificationPreview = ({ previewData, loading, onChannelChange, onStatusChange }) => {
  const { t } = useTranslation()
  const [selectedChannel, setSelectedChannel] = useState('telegram')
  const [selectedStatus, setSelectedStatus] = useState('success')

  const channelOptions = [
    { label: 'Telegram', value: 'telegram' },
    { label: 'QQ', value: 'qq' },
    { label: t('notificationTemplate.channelWechat'), value: 'wechat' },
    { label: 'Server酱', value: 'serverchan' },
  ]

  const statusOptions = [
    { label: t('notificationTemplate.statusSuccess'), value: 'success' },
    { label: t('notificationTemplate.statusFailed'), value: 'failed' },
    { label: t('notificationTemplate.statusNoChange'), value: 'no_change' },
  ]

  const handleChannelChange = (value) => {
    setSelectedChannel(value)
    onChannelChange?.(value)
  }

  const handleStatusChange = (value) => {
    setSelectedStatus(value)
    onStatusChange?.(value)
  }

  return (
    <Card
      title={t('notificationTemplate.previewTitle')}
      extra={
        <ReloadOutlined
          spin={loading}
          onClick={() => onChannelChange?.(selectedChannel)}
          style={{ cursor: 'pointer' }}
        />
      }
      style={{ height: '100%', position: 'sticky', top: 0 }}
    >
      {/* 工具栏 */}
      <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }} size="middle">
        <div>
          <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
            {t('notificationTemplate.channelLabel')}
          </Text>
          <Segmented
            options={channelOptions}
            value={selectedChannel}
            onChange={handleChannelChange}
            block
          />
        </div>

        <div>
          <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
            {t('notificationTemplate.statusLabel')}
          </Text>
          <Segmented
            options={statusOptions}
            value={selectedStatus}
            onChange={handleStatusChange}
            block
          />
        </div>
      </Space>

      {/* 预览内容 */}
      <Spin spinning={loading}>
        {!previewData ? (
          <Empty description={t('notificationTemplate.noPreview')} />
        ) : previewData.error ? (
          <Alert message={previewData.error} type="error" showIcon />
        ) : (
          <div style={{ marginTop: 16 }}>
            {/* 渠道警告 */}
            {previewData.warning && (
              <Alert
                message={previewData.warning}
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            {/* 图片预览 */}
            {previewData.imageUrl && (
              <div style={{ marginBottom: 16 }}>
                <Image
                  src={previewData.imageUrl}
                  alt="Preview"
                  style={{ maxWidth: '100%', borderRadius: 4 }}
                  fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                />
              </div>
            )}

            {/* 标题 */}
            {previewData.title && (
              <Card size="small" style={{ marginBottom: 8, backgroundColor: '#f0f0f0' }}>
                <Text strong>{previewData.title}</Text>
              </Card>
            )}

            {/* 正文 */}
            {previewData.body && (
              <Card size="small" style={{ backgroundColor: '#fafafa' }}>
                <Paragraph
                  style={{
                    whiteSpace: 'pre-wrap',
                    marginBottom: 0,
                    fontFamily: selectedChannel === 'telegram' ? 'inherit' : 'monospace',
                  }}
                >
                  {previewData.body}
                </Paragraph>
              </Card>
            )}

            {/* 示例数据折叠面板 */}
            {previewData.exampleData && (
              <Collapse
                ghost
                size="small"
                style={{ marginTop: 16 }}
                items={[
                  {
                    key: '1',
                    label: t('notificationTemplate.viewVariables'),
                    children: (
                      <pre style={{
                        fontSize: 12,
                        backgroundColor: '#f5f5f5',
                        padding: 8,
                        borderRadius: 4,
                        overflow: 'auto',
                      }}>
                        {JSON.stringify(previewData.exampleData, null, 2)}
                      </pre>
                    ),
                  },
                ]}
              />
            )}
          </div>
        )}
      </Spin>
    </Card>
  )
}

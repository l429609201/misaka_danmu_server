import { useRateLimitSSE } from '../../../hooks/useRateLimitSSE'
import { MyIcon } from '@/components/MyIcon'
import { useTranslation } from 'react-i18next'
import {
  Card,
  Table,
  Typography,
  Progress,
  Row,
  Col,
  Statistic,
  Alert,
} from 'antd'

const { Title, Paragraph } = Typography

export const RateLimitPanel = () => {
  const { t } = useTranslation()
  const { data: status, loading } = useRateLimitSSE()

  return (
    <div className="my-6">
      <Card loading={loading}>
        <Typography>
          <Title level={4}>{t('rateLimitPanel.title')}</Title>
          <Paragraph>
            {t('rateLimitPanel.desc')}
          </Paragraph>
        </Typography>
        {status && (
          <>
            {status.verificationFailed && (
              <Alert
                message={t('rateLimitPanel.securityWarning')}
                description={t('rateLimitPanel.securityWarningDesc')}
                type="error"
                showIcon
                className="!mb-4"
              />
            )}

            {/* 顶部状态卡片 */}
            <Card type="inner" className="!mb-4">
              <Row gutter={16}>
                <Col xs={24} sm={12}>
                  <Statistic
                    title={t('rateLimitPanel.statusLabel')}
                    value={
                      status.verificationFailed
                        ? t('rateLimitPanel.verifyFailed')
                        : status.enabled
                          ? t('rateLimitPanel.enabled')
                          : t('rateLimitPanel.disabled')
                    }
                    valueStyle={{
                      color: status.verificationFailed
                        ? '#cf1322'
                        : status.enabled
                          ? '#3f8600'
                          : '#cf1322'
                    }}
                  />
                </Col>
                <Col xs={24} sm={12}>
                  <Statistic.Countdown
                    title={t('rateLimitPanel.resetCountdown')}
                    value={Date.now() + status.secondsUntilReset * 1000}
                    format="HH:mm:ss"
                  />
                </Col>
              </Row>
            </Card>

            {/* 中间卡片区 - 左右分栏 */}
            <Row gutter={[16, 16]} className="!mb-6">
              {/* 左侧卡片 - 弹幕下载流控 */}
              <Col xs={24} lg={12}>
                <Card type="inner" title={<span><MyIcon icon="celve-cebiandaohang-liukongcelve" size={16} style={{ marginRight: 6 }} />{t('rateLimitPanel.danmakuRateLimit')}</span>} className={status.verificationFailed ? 'opacity-50' : ''} style={{ height: '100%' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span><strong>{t('rateLimitPanel.danmakuDetail')}</strong></span>
                        <span>{status.globalRequestCount} {t('rateLimitPanel.timesUnit')} / {status.globalLimit} {t('rateLimitPanel.timesUnit')}</span>
                      </div>
                      <Progress
                        percent={status.globalLimit > 0 ? (status.globalRequestCount / status.globalLimit) * 100 : 0}
                        status={
                          status.globalLimit > 0 && (status.globalRequestCount / status.globalLimit) * 100 >= 100
                            ? 'exception'
                            : status.globalLimit > 0 && (status.globalRequestCount / status.globalLimit) * 100 >= 80
                              ? 'normal'
                              : 'success'
                        }
                        strokeColor={
                          status.globalLimit > 0 && (status.globalRequestCount / status.globalLimit) * 100 >= 100
                            ? '#ff4d4f'
                            : status.globalLimit > 0 && (status.globalRequestCount / status.globalLimit) * 100 >= 80
                              ? '#faad14'
                              : '#52c41a'
                        }
                      />
                    </div>
                    {/* 占位元素,保持与右侧卡片高度一致 */}
                    <div style={{ height: '32px' }}></div>
                  </div>
                </Card>
              </Col>

              {/* 右侧卡片 - 后备调用流控 */}
              <Col xs={24} lg={12}>
                <Card type="inner" title={<span><MyIcon icon="liukongcelvefuwubeifen" size={16} style={{ marginRight: 6 }} />{t('rateLimitPanel.fallbackRateLimit')}</span>} className={status.verificationFailed ? 'opacity-50' : ''} style={{ height: '100%' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span><strong>{t('rateLimitPanel.fallbackDetail')}</strong></span>
                        <span>{status.fallback?.totalCount || 0} {t('rateLimitPanel.timesUnit')} / {status.fallback?.totalLimit || 0} {t('rateLimitPanel.timesUnit')}</span>
                      </div>
                      <Progress
                        percent={status.fallback?.totalLimit > 0 ? (status.fallback.totalCount / status.fallback.totalLimit) * 100 : 0}
                        status={
                          status.fallback?.totalLimit > 0 && (status.fallback.totalCount / status.fallback.totalLimit) * 100 >= 100
                            ? 'exception'
                            : status.fallback?.totalLimit > 0 && (status.fallback.totalCount / status.fallback.totalLimit) * 100 >= 80
                              ? 'normal'
                              : 'success'
                        }
                        strokeColor={
                          status.fallback?.totalLimit > 0 && (status.fallback.totalCount / status.fallback.totalLimit) * 100 >= 100
                            ? '#ff4d4f'
                            : status.fallback?.totalLimit > 0 && (status.fallback.totalCount / status.fallback.totalLimit) * 100 >= 80
                              ? '#faad14'
                              : '#52c41a'
                        }
                      />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginTop: '12px', height: '32px' }}>
                      <strong><MyIcon icon="liukongcelve" size={15} style={{ marginRight: 4 }} />{t('rateLimitPanel.callStats')}</strong>
                      <span>{t('rateLimitPanel.matchCount', { count: status.fallback?.matchCount || 0 })}</span>
                      <span>{t('rateLimitPanel.searchCount', { count: status.fallback?.searchCount || 0 })}</span>
                    </div>
                  </div>
                </Card>
              </Col>
            </Row>

            {/* 底部表格区 - 各源流控详情 */}
            <Card type="inner" title={t('rateLimitPanel.sourceRateLimit')} className={status.verificationFailed ? 'opacity-50' : ''}>
              <Table
                columns={[
                  {
                    title: t('rateLimitPanel.colSourceName'),
                    dataIndex: 'providerName',
                    key: 'providerName',
                    width: 100,
                    render: (_, record) => record.displayName || record.providerName,
                  },
                  {
                    title: t('rateLimitPanel.colUsage'),
                    key: 'progress',
                    render: (_, record) => {
                      const isUnlimited = record.quota === '∞' || record.quota === Infinity
                      const label = isUnlimited
                        ? `${record.requestCount} / ∞`
                        : `${record.requestCount} / ${record.quota}`
                      // 无限额：以全局配额为参考基准显示实际用量进度
                      // 有配额：正常比例
                      let percent
                      if (isUnlimited) {
                        const refLimit = status.globalLimit || 100
                        percent = record.requestCount > 0
                          ? Math.max(3, Math.min(95, Math.round((record.requestCount / refLimit) * 100)))
                          : 0
                      } else {
                        percent = Math.min(100, Math.round((record.requestCount / record.quota) * 100))
                      }
                      // 颜色：有配额且接近/超限用警告色，其余用主题色
                      const isWarning = !isUnlimited && percent >= 80
                      const isDanger = !isUnlimited && percent >= 100
                      const barBg = isUnlimited
                        ? 'color-mix(in srgb, var(--color-primary) 18%, var(--ant-color-bg-container, #fff))'
                        : isDanger
                          ? 'rgba(255, 77, 79, 0.15)'
                          : isWarning
                            ? 'rgba(250, 173, 20, 0.15)'
                            : 'color-mix(in srgb, var(--color-primary) 18%, var(--ant-color-bg-container, #fff))'
                      const barFill = isUnlimited
                        ? 'color-mix(in srgb, var(--color-primary) 45%, var(--ant-color-bg-container, #fff))'
                        : isDanger ? '#ff4d4f' : isWarning ? '#faad14' : 'var(--color-primary)'
                      const textColor = isUnlimited
                        ? 'color-mix(in srgb, var(--color-primary) 70%, transparent)'
                        : isDanger ? '#ff4d4f' : isWarning ? '#faad14' : 'var(--color-primary)'
                      return (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{ flex: 1, height: 8, borderRadius: 4, overflow: 'hidden', background: barBg }}>
                            <div style={{
                              width: `${percent}%`, height: '100%', background: barFill,
                              borderRadius: 4, transition: 'width 0.5s ease',
                            }} />
                          </div>
                          <span style={{ fontSize: 12, color: textColor, fontWeight: 500, whiteSpace: 'nowrap', minWidth: 50, textAlign: 'right' }}>
                            {label}
                          </span>
                        </div>
                      )
                    },
                  },
                  {
                    title: t('rateLimitPanel.colStatus'),
                    key: 'status',
                    width: 80,
                    align: 'center',
                    render: (_, record) => {
                      const isUnlimited = record.quota === '∞' || record.quota === Infinity
                      if (isUnlimited) {
                        return (
                          <span style={{ color: 'var(--color-primary)', fontSize: 12, opacity: 0.7 }}>
                            ● {t('rateLimitPanel.statusNormal')}
                          </span>
                        )
                      }
                      const percent = (record.requestCount / record.quota) * 100
                      const isDanger = percent >= 100
                      const isWarning = percent >= 80
                      const color = isDanger ? '#ff4d4f' : isWarning ? '#faad14' : 'var(--color-primary)'
                      const label = isDanger
                        ? t('rateLimitPanel.statusFull')
                        : isWarning
                          ? t('rateLimitPanel.statusNear')
                          : t('rateLimitPanel.statusOk')
                      return (
                        <span style={{ color, fontSize: 12 }}>
                          ● {label}
                        </span>
                      )
                    },
                  },
                ]}
                dataSource={status.providers}
                rowKey="providerName"
                pagination={false}
                size="small"
              />
            </Card>
          </>
        )}
      </Card>
    </div>
  )
}

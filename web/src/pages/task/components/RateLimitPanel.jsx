import { useRateLimitSSE } from '../../../hooks/useRateLimitSSE'
import { MyIcon } from '@/components/MyIcon'
import { useTranslation } from 'react-i18next'
import {
  Card,
  Tooltip,
  Typography,
  Progress,
  Row,
  Col,
  Statistic,
  Alert,
} from 'antd'

const { Title, Paragraph } = Typography

// why：根据用量百分比返回热力等级类名，无配额单独处理
function getHeatLevel(count, quota) {
  if (!isFinite(quota)) return 'heat-inf'
  const p = count / quota * 100
  if (p >= 100) return 'heat-lv3'
  if (p >= 80)  return 'heat-lv2'
  if (p >= 50)  return 'heat-lv1'
  return 'heat-lv0'
}

export const RateLimitPanel = () => {
  const { t } = useTranslation()
  const { data: status, loading } = useRateLimitSSE()

  return (
    <div className="my-6">
      {/* why：加 rate-limit-card 类名，让壁纸模式 CSS 能单独针对流控面板做毛玻璃处理 */}
      <Card loading={loading} className="rate-limit-card">
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

            {/* why：各源流控详情改为热力瓦片网格（方案F）
                每源一块瓦片，颜色深浅直接映射压力等级，无需逐行读表格
                0-49%=淡紫 50-79%=中紫 80-99%=橙 100%+=红 无限额=细描边紫 */}
            <Card type="inner" title={t('rateLimitPanel.sourceRateLimit')} className={status.verificationFailed ? 'opacity-50' : ''}>
              <div className="rate-limit-heatmap">
                {(status.providers || []).map(record => {
                  const isUnlimited = record.quota === '∞' || record.quota === Infinity
                  const quotaLabel = isUnlimited ? '∞' : record.quota
                  let pct = 0
                  if (!isUnlimited && record.quota > 0) {
                    pct = Math.min(100, Math.round((record.requestCount / record.quota) * 100))
                  }
                  const heatLevel = getHeatLevel(record.requestCount, record.quota)
                  const statusLabel = isUnlimited
                    ? t('rateLimitPanel.statusNormal')
                    : pct >= 100 ? t('rateLimitPanel.statusFull')
                    : pct >= 80  ? t('rateLimitPanel.statusNear')
                    : t('rateLimitPanel.statusOk')
                  return (
                    <Tooltip
                      key={record.providerName}
                      title={`${record.displayName || record.providerName}: ${record.requestCount} / ${quotaLabel} · ${statusLabel}`}
                    >
                      <div className={`rl-tile ${heatLevel}`}>
                        <span className="rl-tile-name">{record.displayName || record.providerName}</span>
                        <span className="rl-tile-pct">{isUnlimited ? '∞' : `${pct}%`}</span>
                        <span className="rl-tile-sub">{record.requestCount} / {quotaLabel}</span>
                      </div>
                    </Tooltip>
                  )
                })}
              </div>
            </Card>
          </>
        )}
      </Card>
    </div>
  )
}

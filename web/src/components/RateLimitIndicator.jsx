import { useState, useRef, useCallback } from 'react'
import { Tooltip, Popover } from 'antd'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store/index.js'
import { useRateLimitSSE } from '@/hooks/useRateLimitSSE'

/**
 * 导航栏流控状态 Tag 指示器
 * Tag 形状 + 双进度条：上行=下载流控，下行=后备流控
 * 颜色逻辑：<80% 绿色 / 80-99% 橙色 / 100% 红色
 * PC端：hover/click 弹出详情，双击跳转流控页
 * 移动端：单击跳转流控页，长按弹出详情
 * 流控未启用时不显示
 */
const getColor = (percent) => {
  if (percent >= 100) return '#ff4d4f'
  if (percent >= 80) return '#faad14'
  return '#52c41a'
}

/** 单行进度条 */
const BarRow = ({ label, percent, color }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 4, width: '100%' }}>
    <span style={{ fontSize: 9, width: 18, flexShrink: 0, fontWeight: 500 }} className="text-gray-400 dark:text-gray-500">{label}</span>
    <div style={{ flex: 1, height: 4, borderRadius: 2, overflow: 'hidden' }} className="bg-black/5 dark:bg-white/10">
      <div style={{ width: `${percent}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.5s ease' }} />
    </div>
    <span style={{ fontSize: 9, width: 26, textAlign: 'right', flexShrink: 0, color }}>{percent}%</span>
  </div>
)

export const RateLimitIndicator = () => {
  const { t } = useTranslation()
  const { data } = useRateLimitSSE()
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const [popoverOpen, setPopoverOpen] = useState(false)
  const longPressTimer = useRef(null)
  const isLongPress = useRef(false)

  // 所有 hooks 必须在 early return 之前调用
  const handleTouchStart = useCallback(() => {
    isLongPress.current = false
    longPressTimer.current = setTimeout(() => {
      isLongPress.current = true
      setPopoverOpen(true)
    }, 400)
  }, [])

  const handleTouchEnd = useCallback(() => {
    clearTimeout(longPressTimer.current)
    if (!isLongPress.current) {
      navigate('/task?key=ratelimit')
    }
  }, [navigate])

  const handleTouchMove = useCallback(() => {
    clearTimeout(longPressTimer.current)
  }, [])

  if (!data || !data.enabled) return null

  const globalPercent = data.globalLimit > 0
    ? Math.min(100, Math.round((data.globalRequestCount / data.globalLimit) * 100))
    : 0
  const fallbackPercent = data.fallback?.totalLimit > 0
    ? Math.min(100, Math.round((data.fallback.totalCount / data.fallback.totalLimit) * 100))
    : 0

  const globalColor = getColor(globalPercent)
  const fallbackColor = getColor(fallbackPercent)
  const isWarning = globalPercent >= 80 || fallbackPercent >= 80
  const warningColor = globalPercent >= 100 || fallbackPercent >= 100 ? '#ff4d4f' : '#faad14'

  const detailContent = (
    <div style={{ fontSize: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t('rateLimitIndicator.title')}</div>
      <div style={{ color: globalColor }}>
        {t('rateLimitIndicator.download')}: {data.globalRequestCount}/{data.globalLimit} ({globalPercent}%)
      </div>
      <div style={{ color: fallbackColor }}>
        {t('rateLimitIndicator.fallback')}: {data.fallback?.totalCount ?? 0}/{data.fallback?.totalLimit ?? 0} ({fallbackPercent}%)
      </div>
      {data.secondsUntilReset > 0 && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.15)', margin: '4px 0' }} />
      )}
      {data.secondsUntilReset > 0 && (
        <div style={{ color: '#aaa' }}>{t('rateLimitIndicator.resetIn', { minutes: Math.ceil(data.secondsUntilReset / 60) })}</div>
      )}
      <div style={{ color: '#aaa', marginTop: 2 }}>
        {isMobile ? t('rateLimitIndicator.tapForDetail') : t('rateLimitIndicator.doubleClickForDetail')}
      </div>
    </div>
  )

  const barContent = (
    <>
      <BarRow label={t('rateLimitIndicator.download')} percent={globalPercent} color={globalColor} />
      <BarRow label={t('rateLimitIndicator.fallback')} percent={fallbackPercent} color={fallbackColor} />
      {isWarning && (
        <div
          className="animate-pulse"
          style={{
            position: 'absolute', top: -2, right: -2,
            width: 6, height: 6, borderRadius: '50%',
            backgroundColor: warningColor,
          }}
        />
      )}
    </>
  )

  const boxStyle = {
    display: 'inline-flex', flexDirection: 'column', gap: 3,
    padding: '4px 8px', borderRadius: 6, cursor: 'pointer',
    minWidth: 90, position: 'relative', transition: 'box-shadow 0.2s',
    border: '1px solid color-mix(in srgb, var(--color-primary) 30%, transparent)',
    userSelect: 'none',
    WebkitTouchCallout: 'none',
  }

  // 移动端：单击跳转，长按弹 Popover
  if (isMobile) {
    return (
      <Popover
        content={detailContent}
        placement="bottom"
        trigger="click"
        open={popoverOpen}
        onOpenChange={setPopoverOpen}
      >
        <div
          className="bg-black/[0.03] dark:bg-white/[0.06]"
          style={boxStyle}
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
          onTouchMove={handleTouchMove}
        >
          {barContent}
        </div>
      </Popover>
    )
  }

  // PC端：hover/click 弹 Tooltip，双击跳转
  return (
    <Tooltip title={detailContent} placement="bottom" trigger={['hover', 'click']}>
      <div
        onDoubleClick={() => navigate('/task?key=ratelimit')}
        className="bg-black/[0.03] dark:bg-white/[0.06]"
        style={boxStyle}
        onMouseEnter={e => e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.12)'}
        onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
      >
        {barContent}
      </div>
    </Tooltip>
  )
}

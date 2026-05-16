import { useEffect, useRef, useState } from 'react'
import { Tooltip } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getRateLimitStatus } from '@/apis'

/**
 * 导航栏流控状态 Tag 指示器
 * Tag 形状 + 双进度条：上行=下载流控，下行=后备流控
 * 颜色逻辑：<80% 绿色 / 80-99% 橙色 / 100% 红色
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
  const [data, setData] = useState(null)
  const navigate = useNavigate()
  const timerRef = useRef(null)

  const fetchStatus = async () => {
    try {
      const res = await getRateLimitStatus()
      setData(res.data)
    } catch {
      // 静默失败
    }
  }

  useEffect(() => {
    fetchStatus()
    timerRef.current = setInterval(fetchStatus, 30000)
    return () => clearInterval(timerRef.current)
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

  const tooltipContent = (
    <div style={{ fontSize: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>流控状态</div>
      <div style={{ color: globalColor }}>
        下载: {data.globalRequestCount}/{data.globalLimit} ({globalPercent}%)
      </div>
      <div style={{ color: fallbackColor }}>
        后备: {data.fallback?.totalCount ?? 0}/{data.fallback?.totalLimit ?? 0} ({fallbackPercent}%)
      </div>
      {data.secondsUntilReset > 0 && (
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.15)', margin: '4px 0' }} />
      )}
      {data.secondsUntilReset > 0 && (
        <div style={{ color: '#aaa' }}>{Math.ceil(data.secondsUntilReset / 60)} 分钟后重置</div>
      )}
      <div style={{ color: '#aaa', marginTop: 2 }}>点击查看详情</div>
    </div>
  )

  return (
    <Tooltip title={tooltipContent} placement="bottom">
      <div
        onClick={() => navigate('/task?key=ratelimit')}
        className="border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800"
        style={{
          display: 'inline-flex', flexDirection: 'column', gap: 3,
          padding: '4px 8px', borderRadius: 4, cursor: 'pointer',
          minWidth: 90, position: 'relative', transition: 'box-shadow 0.2s',
        }}
        onMouseEnter={e => e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.12)'}
        onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
      >
        <BarRow label="下载" percent={globalPercent} color={globalColor} />
        <BarRow label="后备" percent={fallbackPercent} color={fallbackColor} />
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
      </div>
    </Tooltip>
  )
}

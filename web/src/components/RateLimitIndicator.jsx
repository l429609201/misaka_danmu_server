import { useEffect, useRef, useState } from 'react'
import { Progress, Tooltip } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getRateLimitStatus } from '@/apis'

/**
 * 导航栏流控状态环形指示器
 * 双层环形：外圈=全局流控，内圈=后备流控
 * 颜色逻辑：<80% 绿色 / 80-99% 橙色 / 100% 红色
 * 流控未启用时不显示
 */
const getColor = (percent) => {
  if (percent >= 100) return '#ff4d4f'
  if (percent >= 80) return '#faad14'
  return '#52c41a'
}

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
    timerRef.current = setInterval(fetchStatus, 30000) // 30秒轮询
    return () => clearInterval(timerRef.current)
  }, [])

  if (!data || !data.globalEnabled) return null

  const globalPercent = data.globalLimit > 0
    ? Math.min(100, Math.round((data.globalRequestCount / data.globalLimit) * 100))
    : 0
  const fallbackPercent = data.fallbackTotalLimit > 0
    ? Math.min(100, Math.round((data.fallbackTotalCount / data.fallbackTotalLimit) * 100))
    : 0

  const globalColor = getColor(globalPercent)
  const fallbackColor = getColor(fallbackPercent)

  const isWarning = globalPercent >= 80 || fallbackPercent >= 80

  const tooltipContent = (
    <div className="text-xs">
      <div className="font-medium mb-1">流控状态</div>
      <div style={{ color: globalColor }}>
        全局: {data.globalRequestCount}/{data.globalLimit} ({globalPercent}%)
      </div>
      <div style={{ color: fallbackColor }}>
        后备: {data.fallbackTotalCount}/{data.fallbackTotalLimit} ({fallbackPercent}%)
      </div>
      {data.secondsUntilReset > 0 && (
        <div className="mt-1 text-gray-400">
          {Math.ceil(data.secondsUntilReset / 60)} 分钟后重置
        </div>
      )}
      <div className="mt-1 text-gray-400">点击查看详情</div>
    </div>
  )

  return (
    <Tooltip title={tooltipContent} placement="bottom">
      <div
        className="cursor-pointer relative flex items-center justify-center"
        style={{ width: 28, height: 28 }}
        onClick={() => navigate('/task?key=ratelimit')}
      >
        {/* 外圈：全局流控 */}
        <Progress
          type="circle"
          percent={globalPercent}
          size={28}
          strokeWidth={12}
          strokeColor={globalColor}
          trailColor="rgba(0,0,0,0.06)"
          format={() => null}
        />
        {/* 内圈：后备流控 */}
        <div className="absolute inset-0 flex items-center justify-center">
          <Progress
            type="circle"
            percent={fallbackPercent}
            size={16}
            strokeWidth={14}
            strokeColor={fallbackColor}
            trailColor="rgba(0,0,0,0.06)"
            format={() => null}
          />
        </div>
        {/* 达限红点 */}
        {isWarning && (
          <div
            className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: globalPercent >= 100 || fallbackPercent >= 100 ? '#ff4d4f' : '#faad14' }}
          />
        )}
      </div>
    </Tooltip>
  )
}

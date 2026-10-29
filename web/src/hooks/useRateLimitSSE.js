import { useEffect, useRef, useState } from 'react'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'

/**
 * 流控状态 SSE 推送 hook
 * 连接 /api/ui/rate-limit/status?stream=true，每秒接收最新流控数据
 * @returns {{ data: object|null, loading: boolean }}
 */
export const useRateLimitSSE = () => {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const abortRef = useRef(null)

  useEffect(() => {
    const token = Cookies.get('danmu_token')
    const abortController = new AbortController()
    abortRef.current = abortController

    const headers = {
      Accept: 'text/event-stream',
    }
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }

    fetchEventSource('/api/ui/rate-limit/status?stream=true', {
      signal: abortController.signal,
      credentials: 'include',
      headers,
      onopen: async response => {
        if (!response.ok) {
          throw new Error(`流控 SSE 连接失败: HTTP ${response.status}`)
        }
        // 防止代理返回 HTML 或 JSON 时被误认为已成功建立事件流。
        const contentType = response.headers.get('content-type') || ''
        if (contentType.split(';')[0].trim().toLowerCase() !== 'text/event-stream') {
          throw new Error(`流控接口响应类型异常: ${contentType || '未提供 Content-Type'}`)
        }
      },
      onmessage: event => {
        const raw = event.data?.trim()
        if (!raw) return
        try {
          const parsed = JSON.parse(raw)
          if (parsed.error) {
            setData({ error: parsed.error })
            setLoading(false)
            return
          }
          setData(parsed)
          // 收到实际状态才结束加载，连接注释不等于状态已读取。
          setLoading(false)
        } catch {
          setData({ error: '流控接口返回了无法解析的状态数据' })
          setLoading(false)
        }
      },
      onclose: () => {
        // 长连接意外结束时显示原因，不再留下无提示的空面板。
        throw new Error('流控状态连接已断开，请刷新页面重试')
      },
      onerror: error => {
        console.error('流控 SSE 连接错误:', error)
        throw error // 保持原有停止自动重连策略，由统一出口显示错误。
      },
    }).catch(error => {
      // 组件卸载时的主动取消不是连接故障，避免更新已卸载组件。
      if (!abortController.signal.aborted) {
        console.error('流控 SSE 流错误:', error)
        setData({ error: error?.message || '流控状态连接失败，请刷新页面重试' })
        setLoading(false)
      }
    })

    return () => {
      if (abortRef.current) {
        abortRef.current.abort()
      }
    }
  }, [])

  return { data, loading }
}

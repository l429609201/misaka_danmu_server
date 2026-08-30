/**
 * 御坂助手流式对话 Hook（P1）
 * ------------------------------------------------------------
 * 用 fetchEventSource 以 POST 方式调用 /api/ui/assistant/chat/stream，
 * 逐 delta 回调增量文本，done/error 回调结束。带 Bearer token。
 */
import { useCallback, useRef } from 'react'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'

/**
 * @returns {{ send, abort }}
 *   send(messages, persona, handlers)：发起流式对话
 *     messages: [{role, content}]；persona: 人设key
 *     handlers: { onDelta(text), onDone(), onError(msg) }
 *   abort()：中断当前流
 */
export function useAssistantChat() {
  const abortRef = useRef(null)

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
  }, [])

  const send = useCallback(async (messages, persona, handlers = {}, sessionId) => {
    const { onDelta, onDone, onError, onTool, onConfirm } = handlers
    const token = Cookies.get('danmu_token')
    if (!token) {
      onError?.('未登录，请重新登录后再试。')
      return
    }

    // 中断上一次未结束的流
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      await fetchEventSource('/api/ui/assistant/chat/stream', {
        method: 'POST',
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ messages, persona, sessionId }),
        // 避免页面切到后台时自动关闭连接
        openWhenHidden: true,
        onopen: async response => {
          if (!response.ok) {
            throw new Error(`连接失败: ${response.status}`)
          }
        },
        onmessage: event => {
          const raw = event.data?.trim()
          if (!raw) return
          let data
          try {
            data = JSON.parse(raw)
          } catch {
            return
          }
          if (data.type === 'delta') onDelta?.(data.content || '')
          else if (data.type === 'tool') onTool?.(data)
          else if (data.type === 'confirm') onConfirm?.(data)
          else if (data.type === 'done') onDone?.()
          else if (data.type === 'error') onError?.(data.content || '对话出错了')
        },
        onerror: err => {
          // 抛出以停止自动重连，交给外层 catch
          throw err
        },
      })
    } catch (err) {
      if (err?.name !== 'AbortError') {
        onError?.(err?.message || '对话连接中断')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [])

  return { send, abort }
}

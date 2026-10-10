/**
 * 御坂助手流式对话 Hook（P1）
 * ------------------------------------------------------------
 * 用 fetchEventSource 以 POST 方式调用 /api/ui/assistant/chat/stream，
 * 逐 delta 回调增量文本，done/error 回调结束。带 Bearer token。
 */
import { useCallback, useRef } from 'react'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useTranslation } from 'react-i18next'
import { AssistantDisplaySanitizer, safeMessageContent, sanitizeAssistantText } from './assistantDisplaySanitizer'

/**
 * @returns {{ send, abort }}
 *   send(messages, persona, handlers, sessionId, options)：发起流式对话
 *     options: { codeRepair }；仅显式 true 表示本次请求的代码修复授权
 *     messages: [{role, content}]；persona: 人设key
 *     handlers: { onDelta(text), onDone(), onError(msg) }
 *   abort()：中断当前流
 */
export function useAssistantChat() {
  const { t } = useTranslation()
  const abortRef = useRef(null)

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
  }, [])

  const send = useCallback(async (messages, persona, handlers = {}, sessionId, options = {}) => {
    const { onDelta, onDone, onError, onServerError, onTool, onThinking, onRound, onChoice, onConfirm } = handlers
    const token = Cookies.get('danmu_token')
    if (!token) {
      onError?.(t('assistant.errNotLoggedIn'))
      return
    }

    // 中断上一次未结束的流
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller
    let terminated = false
    // 每条 SSE 流独占状态，仅处理 delta；真实工具事件不经过正文解析器。
    let sanitizer = new AssistantDisplaySanitizer()
    let deltaMetadata
    const flush = (final = true) => {
      const tail = sanitizer.finish()
      if (tail) onDelta?.(tail, deltaMetadata)
      if (!final) sanitizer = new AssistantDisplaySanitizer()
    }

    try {
      await fetchEventSource('/api/ui/assistant/chat/stream', {
        method: 'POST',
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        // 授权仅来自界面显式开关，不从消息或模型输出推断。
        body: JSON.stringify({ messages: messages.map(message => ({ ...message, content: safeMessageContent(message) })), persona, sessionId, codeRepair: options.codeRepair === true }),
        // 避免页面切到后台时自动关闭连接
        openWhenHidden: true,
        onopen: async response => {
          if (!response.ok || !response.headers.get('content-type')?.toLowerCase().includes('text/event-stream')) {
            throw new Error(t('assistant.errConnFailed', { status: response.status }))
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
          if (terminated) return
          if (data.type === 'delta') {
            deltaMetadata = data
            const safe = sanitizer.feed(data.content || '')
            if (safe) onDelta?.(safe, data)
          }
          else if (data.type === 'round') {
            if (data.status === 'running') { flush(false); deltaMetadata = { round_id: data.round_id } }
            onRound?.(data)
          }
          else if (data.type === 'tool') onTool?.(data)
          else if (data.type === 'thinking') onThinking?.(data)
          else if (data.type === 'choice') { terminated = true; flush(); onChoice?.(data) }
          else if (data.type === 'confirm') { terminated = true; flush(); onConfirm?.(data) }
          else if (data.type === 'done') { terminated = true; flush(); onDone?.() }
          else if (data.type === 'error') { terminated = true; flush(); (onServerError || onError)?.(sanitizeAssistantText(data.content) || t('assistant.replyError')) }
        },
        onclose: () => {
          if (!terminated) throw new Error(t('assistant.errConnInterrupted'))
        },
        onerror: err => {
          // 抛出以停止自动重连，交给外层 catch
          throw err
        },
      })
    } catch (err) {
      if (err?.name !== 'AbortError') {
        flush()
        onError?.(sanitizeAssistantText(err?.message) || t('assistant.errConnInterrupted'))
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [t])

  return { send, abort }
}

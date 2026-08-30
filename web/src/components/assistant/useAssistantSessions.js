/**
 * 御坂助手会话历史 Hook（P4）
 * ------------------------------------------------------------
 * 封装会话列表/详情/保存/删除的 API 调用（带 Bearer）。
 */
import { useCallback } from 'react'
import Cookies from 'js-cookie'

const BASE = '/api/ui/assistant/sessions'

function authHeaders() {
  const token = Cookies.get('danmu_token')
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  }
}

export function useAssistantSessions() {
  // 会话列表（摘要）
  const listSessions = useCallback(async () => {
    try {
      const res = await fetch(`${BASE}?limit=50`, { headers: authHeaders() })
      if (!res.ok) return []
      return await res.json()
    } catch {
      return []
    }
  }, [])

  // 会话详情（含消息）
  const loadSession = useCallback(async sid => {
    const res = await fetch(`${BASE}/${encodeURIComponent(sid)}`, { headers: authHeaders() })
    if (!res.ok) throw new Error('加载会话失败')
    return await res.json()
  }, [])

  // 保存/更新会话展示快照
  const saveSession = useCallback(async (sid, messages, persona) => {
    try {
      await fetch(`${BASE}/${encodeURIComponent(sid)}`, {
        method: 'PUT',
        headers: authHeaders(),
        body: JSON.stringify({ messages, persona }),
      })
    } catch {
      // 保存失败不影响对话，忽略
    }
  }, [])

  // 删除会话
  const deleteSession = useCallback(async sid => {
    try {
      await fetch(`${BASE}/${encodeURIComponent(sid)}`, {
        method: 'DELETE',
        headers: authHeaders(),
      })
    } catch {
      // 忽略
    }
  }, [])

  return { listSessions, loadSession, saveSession, deleteSession }
}

// 生成新会话 ID
export function createSessionId() {
  return `web-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

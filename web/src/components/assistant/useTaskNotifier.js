/**
 * 任务气泡播报 Hook（useTaskNotifier）
 * ------------------------------------------------------------
 * 定时轮询后台任务列表，检测状态变化（完成/失败/新开始），
 * 让御坂看板娘弹气泡播报。配置从 AI 辅助设置页读取（后端 config）。
 *
 * @param {object} opts
 *   enabled     总开关（面板关闭时才播报，避免打扰）
 *   onNotify    (text, kind) => void  播报回调（kind: done|failed|start）
 */
import { useEffect, useRef, useCallback } from 'react'
import Cookies from 'js-cookie'

const RUNNING_STATES = ['排队中', '运行中', '已暂停']
const DONE_STATES = ['已完成', '成功']
const FAIL_STATES = ['失败', '已中止']

function authHeaders() {
  return { Authorization: `Bearer ${Cookies.get('danmu_token')}` }
}

// 桌面通知推送（浏览器 Notification API）。仅在已授权时推送，不打扰。
function pushDesktop(title, body) {
  try {
    if (typeof Notification === 'undefined') return
    if (Notification.permission === 'granted') {
      new Notification(title, { body, tag: 'misaka-task', silent: false })
    }
  } catch {
    /* 忽略：部分环境(非 HTTPS/无权限)不支持 */
  }
}

// 首次启用播报时请求一次桌面通知权限（用户可拒绝）
function ensureNotifyPermission() {
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {})
    }
  } catch { /* 忽略 */ }
}

// 读取御坂播报相关配置（后端 config）
async function loadNotifyConfig() {
  try {
    const res = await fetch('/api/ui/config/assistantNotifyEnabled', { headers: authHeaders() })
    if (!res.ok) return null
    const keys = ['assistantNotifyEnabled', 'assistantNotifyOnComplete', 'assistantNotifyOnFailed',
                  'assistantNotifyOnStart', 'assistantNotifyInterval']
    const results = await Promise.all(
      keys.map(k => fetch(`/api/ui/config/${k}`, { headers: authHeaders() })
        .then(r => (r.ok ? r.json() : null)).catch(() => null))
    )
    const cfg = {}
    keys.forEach((k, i) => { cfg[k] = results[i]?.value })
    return cfg
  } catch {
    return null
  }
}

export function useTaskNotifier({ enabled, onNotify, t }) {
  // 无 t 时回退：直接返回 key（不至于崩），正常都会传入
  const tr = t || ((k) => k)
  const timerRef = useRef(null)
  const snapshotRef = useRef(null) // 上次任务状态快照 {taskId: status}
  const cfgRef = useRef(null)

  const poll = useCallback(async () => {
    try {
      const res = await fetch('/api/ui/tasks?status=all&pageSize=30', { headers: authHeaders() })
      if (!res.ok) return
      const data = await res.json()
      const list = data.list || []
      const cfg = cfgRef.current || {}

      const prev = snapshotRef.current
      const curr = {}
      list.forEach(t => { curr[t.taskId] = { status: t.status, title: t.title, description: t.description } })

      // 首次轮询只记录快照，不播报（避免刷屏历史任务）
      if (prev !== null) {
        for (const [id, info] of Object.entries(curr)) {
          const old = prev[id]
          const shortTitle = (info.title || '任务').replace(/^(外部API|御坂助手|Webhook)/, '').trim().slice(0, 24)
          // 新任务开始
          if (!old && RUNNING_STATES.includes(info.status)) {
            if (cfg.assistantNotifyOnStart === 'true') {
              onNotify?.(tr('assistant.notifyStart', { title: shortTitle }), 'start')
            }
          } else if (old && old.status !== info.status) {
            // 状态变化 → 完成/失败
            if (DONE_STATES.includes(info.status) && cfg.assistantNotifyOnComplete === 'true') {
              const text = tr('assistant.notifyDone', { title: shortTitle })
              onNotify?.(text, 'done')
              pushDesktop(tr('assistant.notifyDoneTitle'), text)
            } else if (FAIL_STATES.includes(info.status) && cfg.assistantNotifyOnFailed === 'true') {
              // 失败：附带原因摘要 + 简单建议
              const reason = (info.description || '').replace(/\s+/g, ' ').trim().slice(0, 60)
              const tip = reason ? tr('assistant.notifyFailReason', { reason }) : tr('assistant.notifyFailNoReason')
              const text = tr('assistant.notifyFailed', { title: shortTitle, tip })
              onNotify?.(text, 'failed')
              pushDesktop(tr('assistant.notifyFailTitle'), text)
            }
          }
        }
      }
      snapshotRef.current = curr
    } catch {
      // 轮询失败忽略，下次再试
    }
  }, [onNotify, tr])

  useEffect(() => {
    let alive = true
    async function boot() {
      const cfg = await loadNotifyConfig()
      if (!alive) return
      cfgRef.current = cfg
      const master = cfg?.assistantNotifyEnabled !== 'false'
      if (!enabled || !master) {
        // 未启用：清理计时器
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
        return
      }
      ensureNotifyPermission() // 启用播报时请求一次桌面通知权限
      const sec = Math.min(60, Math.max(10, parseInt(cfg?.assistantNotifyInterval || '15', 10) || 15))
      await poll() // 立即建立首次快照
      timerRef.current = setInterval(poll, sec * 1000)
    }
    boot()
    return () => {
      alive = false
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    }
  }, [enabled, poll])
}

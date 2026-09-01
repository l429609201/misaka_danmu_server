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

// 状态值必须与后端 TaskStatus 枚举一致（src/services/task_manager.py）：
// 排队中 / 运行中 / 已完成 / 失败 / 已暂停。
// 另有中止场景产出「已取消」（见 src/api/control/models.py 字段描述）。
// 原代码中的「成功」「已中止」后端从不产出，属死值，已移除。
const RUNNING_STATES = ['排队中', '运行中', '已暂停']
const DONE_STATES = ['已完成']
const FAIL_STATES = ['失败', '已取消']

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
  const timerRef = useRef(null)
  const snapshotRef = useRef(null) // 上次任务状态快照 {taskId: status}
  const cfgRef = useRef(null)

  // why：t / onNotify 都用 ref 持有，不进 poll 的依赖数组。
  // i18n 的 t 函数每次渲染都是新引用，若直接依赖会让 poll 反复重建，
  // 继而触发 useEffect 清理并重建 setInterval，轮询永远跑不到第二次，
  // 快照 diff 因此永远不成立，气泡永不触发。
  const trRef = useRef(t)
  trRef.current = t
  const notifyRef = useRef(onNotify)
  notifyRef.current = onNotify

  // 无 t 时回退：直接返回 key（不至于崩），正常都会传入
  const tr = useCallback((k, opts) => {
    const fn = trRef.current
    return fn ? fn(k, opts) : k
  }, [])

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
              notifyRef.current?.(tr('assistant.notifyStart', { title: shortTitle }), 'start')
            }
          } else if (old && old.status !== info.status) {
            // 状态变化 → 完成/失败
            if (DONE_STATES.includes(info.status) && cfg.assistantNotifyOnComplete === 'true') {
              const text = tr('assistant.notifyDone', { title: shortTitle })
              notifyRef.current?.(text, 'done')
              pushDesktop(tr('assistant.notifyDoneTitle'), text)
            } else if (FAIL_STATES.includes(info.status) && cfg.assistantNotifyOnFailed === 'true') {
              // 失败：附带原因摘要 + 简单建议
              const reason = (info.description || '').replace(/\s+/g, ' ').trim().slice(0, 60)
              const tip = reason ? tr('assistant.notifyFailReason', { reason }) : tr('assistant.notifyFailNoReason')
              const text = tr('assistant.notifyFailed', { title: shortTitle, tip })
              notifyRef.current?.(text, 'failed')
              pushDesktop(tr('assistant.notifyFailTitle'), text)
            }
          }
        }
      }
      snapshotRef.current = curr
    } catch {
      // 轮询失败忽略，下次再试
    }
    // 依赖仅 tr（已 useCallback 空依赖，引用永久稳定），
    // onNotify 走 notifyRef，确保 poll 引用稳定、定时器不被重建
  }, [tr])

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

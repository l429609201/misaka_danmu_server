/**
 * 任务气泡播报 Hook（useTaskNotifier）
 * ------------------------------------------------------------
 * 定时轮询后台任务列表，检测状态变化（完成/失败/新开始），
 * 让御坂看板娘弹气泡播报。配置从 AI 辅助设置页读取（后端 config）。
 *
 * @param {object} opts
 *   enabled     总开关（面板关闭时才播报，避免打扰）
 *   onNotify    (text, kind) => void  播报回调（kind: done|failed|start）
 *   onActivity  (phase) => void       持续任务姿态（idle|queued|working|paused）
 */
import { useEffect, useRef, useCallback, useState } from 'react'
import Cookies from 'js-cookie'
import { serverTimestamp, taskEvents, mergeTaskSnapshot } from './taskNotifierState'

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

export function useTaskNotifier({ enabled, onNotify, onActivity, t }) {
  const timerRef = useRef(null)
  const inFlightRef = useRef(null)
  const requestRef = useRef(null)
  const generationRef = useRef(0)
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const activityRef = useRef(onActivity)
  activityRef.current = onActivity
  const activityStateRef = useRef(null)
  const snapshotRef = useRef(null) // 上次任务状态快照 {taskId: status}
  const snapshotAtRef = useRef(NaN)
  const pendingRef = useRef(new Map())
  const cfgRef = useRef(null)
  const [configVersion, setConfigVersion] = useState(0)

  useEffect(() => {
    const reload = () => setConfigVersion(version => version + 1)
    window.addEventListener('assistant-notify-config-changed', reload)
    return () => window.removeEventListener('assistant-notify-config-changed', reload)
  }, [])

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

  const flushPending = useCallback(() => {
    if (!enabledRef.current) return
    const cfg = cfgRef.current || {}
    if (cfg.assistantNotifyEnabled === 'false') return
    for (const { task, kind } of pendingRef.current.values()) {
      const title = (task.title || '任务').replace(/^(外部API|御坂助手|Webhook)/, '').trim().slice(0, 24)
      const statusKey = kind === 'done' ? 'Done' : kind === 'failed' ? 'Failed' : 'Start'
      const detail = {
        title: `【${tr(`assistant.notifyStatus${statusKey}`)}】${title}`,
        body: (task.description || '').replace(/\s+/g, ' ').trim().slice(0, 240),
      }
      if (kind === 'start' && cfg.assistantNotifyOnStart === 'true') {
        notifyRef.current?.(tr('assistant.notifyStart', { title }), kind, detail)
      } else if (kind === 'done' && cfg.assistantNotifyOnComplete !== 'false') {
        const text = tr('assistant.notifyDone', { title })
        notifyRef.current?.(text, kind, detail)
        pushDesktop(tr('assistant.notifyDoneTitle'), text)
      } else if (kind === 'failed' && cfg.assistantNotifyOnFailed !== 'false') {
        const reason = (task.description || '').replace(/\s+/g, ' ').trim().slice(0, 60)
        const tip = reason ? tr('assistant.notifyFailReason', { reason }) : tr('assistant.notifyFailNoReason')
        const text = tr('assistant.notifyFailed', { title, tip })
        notifyRef.current?.(text, kind, detail)
        pushDesktop(tr('assistant.notifyFailTitle'), text)
      }
    }
    pendingRef.current.clear()
  }, [tr])

  const poll = useCallback(async (generation = generationRef.current) => {
    if (generation !== generationRef.current || inFlightRef.current
      || !cfgRef.current || cfgRef.current.assistantNotifyEnabled === 'false') return
    const controller = new AbortController()
    inFlightRef.current = controller
    requestRef.current = controller
    try {
      const tasks = new Map()
      let page = 1
      let serverTime = NaN
      while (generation === generationRef.current) {
        const res = await fetch(`/api/ui/tasks?status=all&pageSize=100&sortBy=updatedAt&page=${page}`, {
          headers: authHeaders(), signal: controller.signal,
        })
        if (!res.ok || generation !== generationRef.current) return
        const data = await res.json()
        if (generation !== generationRef.current) return
        const list = data.list || []
        if (page === 1) serverTime = serverTimestamp(data.serverTime)
        list.forEach(task => tasks.set(task.taskId, task))
        const lastUpdate = serverTimestamp(list.at(-1)?.updatedAt)
        // 扫过整个本轮更新窗口；不能因为大量批量导入只取第一页而漏报。
        if (!Number.isFinite(snapshotAtRef.current) || !list.length
          || !Number.isFinite(lastUpdate) || lastUpdate < snapshotAtRef.current - 2000
          || page * 100 >= data.total) break
        page += 1
      }
      const list = [...tasks.values()]
      const prev = snapshotRef.current
      for (const event of taskEvents(prev, list, snapshotAtRef.current)) {
        // 同一个任务的终态覆盖尚未播报的开始事件，避免打开面板时积压过时提示。
        pendingRef.current.set(event.task.taskId, event)
      }
      const curr = mergeTaskSnapshot(prev, list)
      snapshotRef.current = curr
      snapshotAtRef.current = serverTime
      flushPending()
      // 姿态依据本次实际返回记录，不从去重缓存恢复已删除任务的旧运行态。
      const statuses = list.map(item => item.status)
      const phase = statuses.includes('运行中') ? 'working'
        : statuses.includes('排队中') ? 'queued'
          : statuses.includes('已暂停') ? 'paused' : 'idle'
      if (activityStateRef.current !== phase) {
        activityStateRef.current = phase
        activityRef.current?.(phase)
      }
    } catch {
      // 请求失败保留上次快照和服务器基线，下轮继续补齐事件。
    } finally {
      if (inFlightRef.current === controller) inFlightRef.current = null
      if (requestRef.current === controller) requestRef.current = null
    }
  }, [flushPending])

  // 面板打开只暂停展示，不停止检测；关闭时立即补播期间积累的完成事件。
  useEffect(() => {
    if (enabled) flushPending()
  }, [enabled, flushPending])

  useEffect(() => {
    let alive = true
    const generation = ++generationRef.current
    async function boot() {
      // 配置与首次快照并行启动，缩小页面刚打开时的漏报窗口。
      const cfgPromise = loadNotifyConfig()
      if (snapshotRef.current === null) {
        cfgRef.current = {}
        await poll(generation)
      }
      const cfg = await cfgPromise
      if (!alive) return
      cfgRef.current = cfg || {}
      const master = cfg?.assistantNotifyEnabled !== 'false'
      if (!master) {
        snapshotRef.current = null
        snapshotAtRef.current = NaN
        pendingRef.current.clear()
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
        return
      }
      ensureNotifyPermission() // 启用播报时请求一次桌面通知权限
      const sec = Math.min(60, Math.max(10, parseInt(cfg?.assistantNotifyInterval || '15', 10) || 15))
      await poll(generation) // 立即建立首次快照
      if (!alive) return
      timerRef.current = setInterval(() => poll(generation), sec * 1000)
    }
    boot()
    return () => {
      alive = false
      generationRef.current += 1
      requestRef.current?.abort()
      requestRef.current = null
      inFlightRef.current = null
      activityStateRef.current = null
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    }
  }, [poll, configVersion])
}

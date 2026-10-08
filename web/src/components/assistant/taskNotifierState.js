/** 任务事件识别只比较服务器时间，不依赖浏览器时钟或已见过运行态。 */
export const RUNNING_STATES = ['排队中', '运行中', '已暂停']
export const DONE_STATES = ['已完成']
export const FAIL_STATES = ['失败', '已取消', '超时']

export function serverTimestamp(value) {
  if (!value) return NaN
  // 后端的 naive 时间属于同一应用时区，统一解析可避免浏览器时区影响比较。
  const text = String(value).replace(' ', 'T')
  return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text}Z`)
}

export function taskEvents(previous, list, since) {
  if (previous === null) return [] // 首次进入页面不播报历史完成记录。
  return list.flatMap(task => {
    const old = previous[task.taskId]
    const recent = Number.isFinite(since)
      && serverTimestamp(task.updatedAt || task.createdAt) >= since - 2000
    if (!old && RUNNING_STATES.includes(task.status)) return [{ task, kind: 'start' }]
    if (old ? old.status === task.status : !recent) return []
    if (DONE_STATES.includes(task.status)) return [{ task, kind: 'done' }]
    if (FAIL_STATES.includes(task.status)) return [{ task, kind: 'failed' }]
    return []
  })
}

export function mergeTaskSnapshot(previous, list) {
  // 保留离开第一页的任务状态，避免分页回到第一页时重复播报。
  const snapshot = { ...previous }
  for (const task of list) {
    delete snapshot[task.taskId]
    snapshot[task.taskId] = task
  }
  // 长期打开 WebUI 时限制快照容量，不让历史状态无限增长。
  return Object.fromEntries(Object.entries(snapshot).slice(-2000))
}

/** 当前消息按总展示时长计时；有后续消息时缩短到 5 秒，否则 10 秒。 */
export function noticeRemainingMs(startedAt, hasNext, now) {
  return Math.max(0, (hasNext ? 5000 : 10000) - Math.max(0, now - startedAt))
}

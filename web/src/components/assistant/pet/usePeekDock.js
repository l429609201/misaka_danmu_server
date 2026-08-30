/**
 * 展开/收起 Hook（usePeekDock）
 * ------------------------------------------------------------
 * 管理看板娘"藏身候选"下的展开/收起意图（真正收不收还要结合是否贴底，由组件判断）。
 *
 * 语义：
 *  - open=true  希望展开（露全身）
 *  - open=false 希望收起（贴底时下沉露头顶）
 * 触发：
 *  - enter()：鼠标进入形象 → 立即置 open=true，并取消待收起计时
 *  - leave()：鼠标离开形象 → 延时 idleMs 后置 open=false
 *  - arm()  ：进入"贴底"状态时调用，启动一次延时收起（若之后移出贴底，用 disarm 取消）
 *  - disarm()：离开贴底 → 立即 open=true 并清计时
 *
 * @param {object}  opts
 * @param {number}  opts.idleMs   延时收起时长（默认 4000）
 * @param {boolean} opts.enabled  是否启用（关时恒展开）
 * @returns {{ open, enter, leave, arm, disarm }}
 */
import { useEffect, useRef, useState } from 'react'

export function usePeekDock({ idleMs = 4000, enabled = true } = {}) {
  // 展开意图：默认展开（露全身）
  const [open, setOpen] = useState(true)
  const timer = useRef(0)

  useEffect(() => {
    if (!enabled) setOpen(true)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [enabled])

  const clear = () => {
    if (timer.current) clearTimeout(timer.current)
  }
  const scheduleClose = () => {
    clear()
    timer.current = setTimeout(() => setOpen(false), idleMs)
  }

  // 鼠标进入形象：立即展开
  const enter = () => {
    if (!enabled) return
    clear()
    setOpen(true)
  }
  // 鼠标离开形象：延时收起
  const leave = () => {
    if (!enabled) return
    scheduleClose()
  }
  // 进入贴底：启动延时收起
  const arm = () => {
    if (!enabled) return
    scheduleClose()
  }
  // 离开贴底：立即展开
  const disarm = () => {
    if (!enabled) return
    clear()
    setOpen(true)
  }

  return { open, enter, leave, arm, disarm }
}

/**
 * 看板娘状态机 Hook
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 useAgentPetMachine 设计思路，用 React Hook 实现。
 * 职责：集中管理助手的"情绪状态"，并提供语义化的切换方法。
 * 说明：状态本身与"如何渲染"解耦——状态机只负责 state，具体画什么由渲染器决定。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PET_STATES, DEFAULT_STATE, TRANSIENT_STATES } from './petActions'

/**
 * @param {object} options
 * @param {string} [options.initial] 初始状态，默认 idle
 * @param {number} [options.autoRevertMs] 瞬时状态播放后恢复当前基态的毫秒数，默认 2000
 */
export function usePetMachine(options = {}) {
  const { initial = DEFAULT_STATE, autoRevertMs = 2000 } = options

  // 当前情绪状态
  const [state, setState] = useState(initial)
  // 记录定时器，便于清理，避免多次切换导致的竞态
  const revertTimer = useRef(null)
  const baseState = useRef(initial)
  const taskState = useRef(initial)
  const inConversation = useRef(false)

  // 清理自动回落定时器
  const clearRevert = useCallback(() => {
    if (revertTimer.current) {
      clearTimeout(revertTimer.current)
      revertTimer.current = null
    }
  }, [])

  /**
   * 切换到指定状态。
   * 若目标是瞬时状态，autoRevertMs 后恢复当前任务/聊天基态。
   * 持续型状态不会自动回落，需显式切换。
   */
  const to = useCallback(
    nextState => {
      // 未知状态兜底为默认状态，避免渲染层拿到非法 key
      const target = PET_STATES.includes(nextState) ? nextState : DEFAULT_STATE
      clearRevert()
      if (!TRANSIENT_STATES.includes(target)) baseState.current = target
      setState(target)

      if (TRANSIENT_STATES.includes(target) && autoRevertMs > 0) {
        revertTimer.current = setTimeout(() => {
          setState(baseState.current)
          revertTimer.current = null
        }, autoRevertMs)
      }
    },
    [autoRevertMs, clearRevert]
  )

  const activity = useCallback(nextState => {
    const target = ['idle', 'queued', 'working', 'paused'].includes(nextState) ? nextState : DEFAULT_STATE
    taskState.current = target
    if (inConversation.current) return
    baseState.current = target
    setState(current => TRANSIENT_STATES.includes(current) ? current : target)
  }, [])

  const enterConversation = useCallback(() => {
    clearRevert()
    inConversation.current = true
    baseState.current = DEFAULT_STATE
    setState(DEFAULT_STATE)
  }, [clearRevert])

  const leaveConversation = useCallback(() => {
    clearRevert()
    inConversation.current = false
    baseState.current = taskState.current
    setState(taskState.current)
  }, [clearRevert])

  const chatTo = useCallback(nextState => {
    if (inConversation.current) to(nextState)
  }, [to])

  // 语义化快捷方法，业务层调用更直观
  const idle = useCallback(() => to('idle'), [to])
  const thinking = useCallback(() => to('thinking'), [to])
  const happy = useCallback(() => to('happy'), [to])
  const sad = useCallback(() => to('sad'), [to])
  const surprised = useCallback(() => to('surprised'), [to])
  const talking = useCallback(() => to('talking'), [to])

  // 组件卸载时清理定时器
  useEffect(() => clearRevert, [clearRevert])

  // why：必须 useMemo 缓存返回对象。此前每次渲染都返回新对象字面量，
  // 导致下游 useCallback([machine]) 反复重建，进而让 useTaskNotifier 的
  // useEffect 无限清理/重建 setInterval，轮询永远停在"首次建快照不播报"，
  // 气泡因此永不触发。state 变化时对象仍会更新（渲染需要），但
  // 方法引用保持稳定，下游只依赖方法时不会被无谓重建。
  return useMemo(
    () => ({ state, to, chatTo, activity, enterConversation, leaveConversation, idle, thinking, happy, sad, surprised, talking }),
    [state, to, chatTo, activity, enterConversation, leaveConversation, idle, thinking, happy, sad, surprised, talking]
  )
}

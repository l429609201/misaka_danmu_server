/**
 * 看板娘状态机 Hook
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 useAgentPetMachine 设计思路，用 React Hook 实现。
 * 职责：集中管理助手的"情绪状态"，并提供语义化的切换方法。
 * 说明：状态本身与"如何渲染"解耦——状态机只负责 state，具体画什么由渲染器决定。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { PET_STATES, DEFAULT_STATE, TRANSIENT_STATES } from './petActions'

/**
 * @param {object} options
 * @param {string} [options.initial] 初始状态，默认 idle
 * @param {number} [options.autoRevertMs] 瞬时状态（happy/surprised 等）自动回落到 idle 的毫秒数，默认 2000
 */
export function usePetMachine(options = {}) {
  const { initial = DEFAULT_STATE, autoRevertMs = 2000 } = options

  // 当前情绪状态
  const [state, setState] = useState(initial)
  // 记录定时器，便于清理，避免多次切换导致的竞态
  const revertTimer = useRef(null)

  // 清理自动回落定时器
  const clearRevert = useCallback(() => {
    if (revertTimer.current) {
      clearTimeout(revertTimer.current)
      revertTimer.current = null
    }
  }, [])

  /**
   * 切换到指定状态。
   * 若目标是"瞬时状态"（如 happy/surprised），会在 autoRevertMs 后自动回落到 idle。
   * 持续型状态（idle/thinking/talking）不会自动回落，需显式切换。
   */
  const to = useCallback(
    nextState => {
      // 未知状态兜底为默认状态，避免渲染层拿到非法 key
      const target = PET_STATES.includes(nextState) ? nextState : DEFAULT_STATE
      clearRevert()
      setState(target)

      if (TRANSIENT_STATES.includes(target) && autoRevertMs > 0) {
        revertTimer.current = setTimeout(() => {
          setState(DEFAULT_STATE)
          revertTimer.current = null
        }, autoRevertMs)
      }
    },
    [autoRevertMs, clearRevert]
  )

  // 语义化快捷方法，业务层调用更直观
  const idle = useCallback(() => to('idle'), [to])
  const thinking = useCallback(() => to('thinking'), [to])
  const happy = useCallback(() => to('happy'), [to])
  const sad = useCallback(() => to('sad'), [to])
  const surprised = useCallback(() => to('surprised'), [to])
  const talking = useCallback(() => to('talking'), [to])

  // 组件卸载时清理定时器
  useEffect(() => clearRevert, [clearRevert])

  return { state, to, idle, thinking, happy, sad, surprised, talking }
}

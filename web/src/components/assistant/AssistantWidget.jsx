/**
 * 助手总装组件（AssistantWidget）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantWidget.vue。
 * 组合：状态机 + 悬浮入口 + 聊天面板。挂到全局 Layout 即可全站可用。
 * 打开面板时隐藏悬浮入口，关闭时恢复（与 demo 行为一致）。
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { isMobileAtom } from '../../../store/index.js'
import { usePetMachine } from './pet/usePetMachine'
import { AssistantEntry } from './AssistantEntry'
import { AssistantPanel } from './AssistantPanel'
import { useTaskNotifier } from './useTaskNotifier'
import './assistant.css'

export function AssistantWidget() {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const [open, setOpen] = useState(false)
  const [notice, setNotice] = useState('') // 任务播报气泡文案（收起态显示）
  const noticeTimer = useRef(null)
  // 瞬时表情 2s 后自动回落 idle
  const machine = usePetMachine({ initial: 'idle', autoRevertMs: 2000 })

  // why：用 ref 持有最新 machine，供回调内取用。
  // machine 对象会随 state 变化而更新，若 handleNotify 直接依赖它，
  // 会导致 useTaskNotifier 的 useEffect 反复清理/重建轮询定时器，
  // 轮询永远停在"首次建快照不播报"，气泡永不触发。
  const machineRef = useRef(machine)
  machineRef.current = machine

  // 任务播报：弹气泡 + 表情联动，气泡 6s 后自动消失
  // 依赖数组为空，引用永久稳定，确保下游轮询定时器不被重建
  const handleNotify = useCallback((text, kind) => {
    setNotice(text)
    const m = machineRef.current
    if (kind === 'done') m?.happy?.()
    else if (kind === 'failed') m?.sad?.()
    else m?.talking?.()
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => setNotice(''), 6000)
  }, [])

  // 组件卸载时清理气泡定时器，避免内存泄漏
  useEffect(() => {
    return () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current)
    }
  }, [])

  // 仅在面板关闭（收起态）时轮询播报，避免打开面板还弹气泡打扰
  useTaskNotifier({ enabled: !open, onNotify: handleNotify, t })

  return (
    <>
      {/* 面板打开时隐藏入口，避免形象重叠 */}
      {!open && (
        <AssistantEntry
          state={machine.state}
          isMobile={isMobile}
          onOpen={() => setOpen(true)}
          notice={notice}
        />
      )}
      <AssistantPanel
        open={open}
        onClose={() => setOpen(false)}
        machine={machine}
        isMobile={isMobile}
      />
    </>
  )
}

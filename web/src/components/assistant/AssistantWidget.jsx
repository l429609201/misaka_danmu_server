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
import { noticeRemainingMs } from './taskNotifierState'
import './assistant.css'

export function AssistantWidget() {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const [open, setOpen] = useState(false)
  const [notice, setNotice] = useState(null) // 任务播报含标题、正文和状态
  const noticeTimer = useRef(null)
  const activeNotice = useRef(null)
  const noticeQueue = useRef([])
  const openRef = useRef(open)
  openRef.current = open
  // 瞬时表情 2s 后自动回落 idle
  const machine = usePetMachine({ initial: 'idle', autoRevertMs: 2000 })

  // why：用 ref 持有最新 machine，供回调内取用。
  // machine 对象会随 state 变化而更新，若 handleNotify 直接依赖它，
  // 会导致 useTaskNotifier 的 useEffect 反复清理/重建轮询定时器，
  // 轮询永远停在"首次建快照不播报"，气泡永不触发。
  const machineRef = useRef(machine)
  machineRef.current = machine

  // 无后续消息保留 10s；新消息到达后当前条总展示时间缩短为 5s。
  const showNextNotice = useCallback(() => {
    if (openRef.current) return
    if (!activeNotice.current) {
      if (!noticeQueue.current.length) return
      const next = noticeQueue.current.shift()
      activeNotice.current = { startedAt: performance.now() }
      setNotice(next)
      const m = machineRef.current
      if (next.kind === 'done') m?.happy?.()
      else if (next.kind === 'failed') m?.sad?.()
      else m?.to?.('greeting')
    }
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => {
      noticeTimer.current = null
      activeNotice.current = null
      setNotice(null)
      showNextNotice()
    }, noticeRemainingMs(activeNotice.current.startedAt, noticeQueue.current.length > 0, performance.now()))
  }, [])

  const handleNotify = useCallback((text, kind, detail) => {
    noticeQueue.current.push({ ...detail, body: detail?.body || text, kind })
    showNextNotice()
  }, [showNextNotice])

  useEffect(() => {
    if (!open) showNextNotice()
  }, [open, showNextNotice])

  const handleActivity = useCallback(phase => machineRef.current?.activity?.(phase), [])

  const openPanel = useCallback(() => {
    machineRef.current?.enterConversation?.()
    setOpen(true)
  }, [])
  const closePanel = useCallback(() => {
    machineRef.current?.leaveConversation?.()
    setOpen(false)
  }, [])

  // 组件卸载时清理气泡定时器，避免内存泄漏
  useEffect(() => {
    return () => {
      if (noticeTimer.current) clearTimeout(noticeTimer.current)
    }
  }, [])

  // 面板打开时仍检测任务，气泡暂存至收起后展示，避免完成事件漏报。
  useTaskNotifier({ enabled: !open, onNotify: handleNotify, onActivity: handleActivity, t })

  return (
    <>
      {/* 面板打开时隐藏入口，避免形象重叠 */}
      {!open && (
        <AssistantEntry
          state={machine.state}
          isMobile={isMobile}
          onOpen={openPanel}
          notice={notice}
        />
      )}
      <AssistantPanel
        open={open}
        onClose={closePanel}
        machine={machine}
        isMobile={isMobile}
      />
    </>
  )
}

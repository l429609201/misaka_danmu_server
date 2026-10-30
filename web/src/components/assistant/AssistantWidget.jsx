/**
 * 助手总装组件（AssistantWidget）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantWidget.vue。
 * 组合：状态机 + 悬浮入口 + 聊天面板。挂到全局 Layout 即可全站可用。
 * 打开面板时隐藏悬浮入口，关闭时恢复（与 demo 行为一致）。
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useAtom, useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import {
  isMobileAtom, assistantPetVisibleAtom, ASSISTANT_PET_CONFIG_KEY,
  assistantEnabledAtom, ASSISTANT_ENABLED_CONFIG_KEY,
} from '../../../store/index.js'
import { getConfig } from '@/apis'
import { usePetMachine } from './pet/usePetMachine'
import { AssistantEntry } from './AssistantEntry'
import { AssistantPanel } from './AssistantPanel'
import { MyIcon } from '../MyIcon'
import { useTaskNotifier } from './useTaskNotifier'
import { noticeRemainingMs } from './taskNotifierState'
import './assistant.css'

export function AssistantWidget() {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  // 助手形象开关（默认关闭）：关闭时只显示简洁按钮，可在头像菜单开启
  const [petVisible, setPetVisible] = useAtom(assistantPetVisibleAtom)
  // 助手形象开关以后端配置为准：挂载时读取一次（读取失败保持默认关闭）
  useEffect(() => {
    getConfig(ASSISTANT_PET_CONFIG_KEY)
      .then(res => setPetVisible(res?.data?.value === 'true'))
      .catch(() => {})
  }, [setPetVisible])
  // 助手总开关以后端配置为准（默认关闭，只有明确为 true 才显示）
  const [assistantEnabled, setAssistantEnabled] = useAtom(assistantEnabledAtom)
  useEffect(() => {
    getConfig(ASSISTANT_ENABLED_CONFIG_KEY)
      .then(res => setAssistantEnabled(res?.data?.value === 'true'))
      .catch(() => {})
  }, [setAssistantEnabled])
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

  // 页面内的即时事件（如主页搜索完成）通过全局事件投递气泡，不必等任务轮询。
  useEffect(() => {
    const onPageNotify = event => {
      const { text, kind = 'done', title, body } = event.detail || {}
      if (text) handleNotify(text, kind, { title, body })
    }
    window.addEventListener('assistant-notify', onPageNotify)
    return () => window.removeEventListener('assistant-notify', onPageNotify)
  }, [handleNotify])

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
  // 总开关关闭时停止任务轮询
  useTaskNotifier({ enabled: assistantEnabled && !open, onNotify: handleNotify, onActivity: handleActivity, t })

  // 总开关关闭：整个助手（形象/按钮/面板）都不渲染
  if (!assistantEnabled) return null

  return (
    <>
      {/* 面板打开时隐藏入口，避免形象重叠 */}
      {!open && petVisible && (
        <AssistantEntry
          state={machine.state}
          isMobile={isMobile}
          onOpen={openPanel}
          notice={notice}
        />
      )}
      {/* 形象关闭时保留一个简洁圆形按钮，助手功能仍可使用 */}
      {!open && !petVisible && (
        <button
          type="button"
          className="assistant-plain-fab"
          onClick={openPanel}
          aria-label={t('header.openAssistant')}
          title={t('header.openAssistant')}
        >
          <MyIcon icon="ai" size={22} />
        </button>
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

/**
 * 助手总装组件（AssistantWidget）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantWidget.vue。
 * 组合：状态机 + 悬浮入口 + 聊天面板。挂到全局 Layout 即可全站可用。
 * 打开面板时隐藏悬浮入口，关闭时恢复（与 demo 行为一致）。
 */
import { useState, useCallback, useRef } from 'react'
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

  // 任务播报：弹气泡 + 表情联动，气泡 6s 后自动消失
  const handleNotify = useCallback((text, kind) => {
    setNotice(text)
    if (kind === 'done') machine.happy?.()
    else if (kind === 'failed') machine.sad?.()
    else machine.talking?.()
    if (noticeTimer.current) clearTimeout(noticeTimer.current)
    noticeTimer.current = setTimeout(() => setNotice(''), 6000)
  }, [machine])

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

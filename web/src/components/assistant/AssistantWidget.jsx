/**
 * 助手总装组件（AssistantWidget）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantWidget.vue。
 * 组合：状态机 + 悬浮入口 + 聊天面板。挂到全局 Layout 即可全站可用。
 * 打开面板时隐藏悬浮入口，关闭时恢复（与 demo 行为一致）。
 */
import { useState } from 'react'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { usePetMachine } from './pet/usePetMachine'
import { AssistantEntry } from './AssistantEntry'
import { AssistantPanel } from './AssistantPanel'
import './assistant.css'

export function AssistantWidget() {
  const isMobile = useAtomValue(isMobileAtom)
  const [open, setOpen] = useState(false)
  // 瞬时表情 2s 后自动回落 idle
  const machine = usePetMachine({ initial: 'idle', autoRevertMs: 2000 })

  return (
    <>
      {/* 面板打开时隐藏入口，避免形象重叠 */}
      {!open && (
        <AssistantEntry state={machine.state} isMobile={isMobile} onOpen={() => setOpen(true)} />
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

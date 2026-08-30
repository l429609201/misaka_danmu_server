/**
 * 悬浮入口（AssistantEntry）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantEntry.vue。
 * 展示看板娘形象，点击打开面板；idle 时飘一句气泡。
 *
 * 交互特性：
 *  - 可拖动：鼠标按住形象可拖到屏幕任意位置，位置持久化（usePetDrag）
 *  - 墙边探头：拖到贴近左/右/底边时，闲置后"歪头从墙边探出"（左右旋转探头、
 *    底边下沉露头顶）；悬停/拖动时转回完整显示。其余位置永远完整。
 *  - 移动端关闭上述特性，保持固定右下角，避免与底部 Tab 冲突。
 *
 * @param {string}  state    当前情绪
 * @param {func}    onOpen   点击打开面板
 * @param {boolean} isMobile 是否移动端
 * @param {boolean} dock     是否启用可拖动+墙边探头（默认 true）
 * @param {number}  slide    左右滑进墙的深度百分比（默认 45，露出脸；越大藏越多）
 * @param {number}  tilt     趴墙倾斜角度（deg，默认 18，小角度=趴墙俏皮不猛转）
 * @param {number}  peekTop  底边露头顶比例（0~1，默认 0.32）
 */
import { useEffect } from 'react'
import { PetStage } from './pet/PetStage'
import { PET_ACTIONS } from './pet/petActions'
import { usePeekDock } from './pet/usePeekDock'
import { usePetDrag } from './pet/usePetDrag'

// 距屏幕某条边多少 px 内算"贴该边"（触发探头候选）
const EDGE_SNAP = 60

export function AssistantEntry({
  state = 'idle',
  onOpen,
  isMobile,
  dock = true,
  slide = 68,
  tilt = 18,
  peekTop = 0.32,
}) {
  // 仅在 idle 且有文案时展示气泡，避免思考/说话时气泡干扰
  const bubble = state === 'idle' ? PET_ACTIONS.idle.bubble : ''
  // 移动端不启用拖动/探头
  const useDock = dock && !isMobile

  const size = isMobile ? 84 : 110
  // 立绘高约为宽的 1.25 倍，估个入口高度供拖动边界/贴边判定用
  const estH = Math.round(size * 1.25)

  const { open, enter, leave, arm, disarm } = usePeekDock({ idleMs: 4000, enabled: useDock })
  const { pos, onMouseDown, dragging, movedRef } = usePetDrag({
    width: size,
    height: estH,
    topRatio: 0.4,
  })

  // 点击打开：若刚发生拖动则不触发（避免拖完误开面板）
  const handleClick = () => {
    if (useDock && movedRef.current) return
    onOpen?.()
  }

  // 判定贴的是哪条边（优先级：左 > 右 > 底）。none 表示不贴边。
  let edge = 'none'
  if (useDock && typeof window !== 'undefined') {
    if (pos.x <= EDGE_SNAP) edge = 'left'
    else if (pos.x + size >= window.innerWidth - EDGE_SNAP) edge = 'right'
    else if (pos.y + estH >= window.innerHeight - EDGE_SNAP) edge = 'bottom'
  }
  const nearEdge = edge !== 'none'

  // 进入/离开"贴边"时，启动/取消延时探头（拖动中不收）
  useEffect(() => {
    if (!useDock) return
    if (nearEdge && !dragging) arm()
    else disarm()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nearEdge, dragging, useDock])

  // 展开：不贴边、或拖动中、或 open 意图为真 → 完整显示
  const expanded = !useDock || !nearEdge || dragging || open
  // 收起方向：贴边且未展开时，取当前边对应的探头类
  const hideClass =
    useDock && nearEdge && !expanded
      ? { left: 'is-hide-left', right: 'is-hide-right', bottom: 'is-hide-bottom' }[edge]
      : ''

  const positionStyle = useDock
    ? {
        left: pos.x,
        top: pos.y,
        right: 'auto',
        bottom: 'auto',
        '--slide': `${slide}%`,
        '--tilt': `${tilt}deg`,
        '--peek-top': peekTop,
      }
    : undefined

  return (
    <div
      className={
        `assistant-fab ${isMobile ? 'is-mobile' : ''} ` +
        `${useDock ? 'is-dock' : ''} ` +
        `${expanded ? 'is-expanded' : ''} ${hideClass} ` +
        `${dragging ? 'is-dragging' : ''}`
      }
      style={positionStyle}
      onMouseDown={useDock ? onMouseDown : undefined}
      onClick={handleClick}
      onMouseEnter={useDock ? enter : undefined}
      onMouseLeave={useDock ? leave : undefined}
      role="button"
      aria-label="打开助手"
    >
      {bubble && <div className="assistant-fab-bubble">{bubble}</div>}
      <PetStage state={state} size={size} floating />
    </div>
  )
}

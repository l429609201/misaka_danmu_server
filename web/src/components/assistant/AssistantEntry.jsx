/**
 * 悬浮入口（AssistantEntry）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentAssistantEntry.vue。
 * 展示看板娘形象，点击打开面板；idle 时飘一句气泡。
 *
 * 交互特性：
 *  - 可拖动：鼠标按住形象可拖到屏幕任意位置，位置持久化（usePetDrag）
 *  - 墙边探头：贴近左右边缘后走入并停在对应探头姿势；悬停时反向走出。
 *    底边仍按原下沉规则收起，移动端同样支持拖动。
 *
 * @param {string}  state    当前情绪
 * @param {func}    onOpen   点击打开面板
 * @param {boolean} isMobile 是否移动端
 * @param {boolean} dock     是否启用可拖动+墙边探头（默认 true）
 * @param {number}  slide    左右滑进墙的深度百分比（默认 45，露出脸；越大藏越多）
 * @param {number}  tilt     趴墙倾斜角度（deg，默认 18，小角度=趴墙俏皮不猛转）
 * @param {number}  peekTop  底边露头顶比例（0~1，默认 0.32）
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import walkStrip from '@/assets/assistant/walk/gemini-walk-seven-strip.webp'
import peekLeft from '@/assets/assistant/peek/peek-left.webp'
import peekRight from '@/assets/assistant/peek/peek-right.webp'
import { useTranslation } from 'react-i18next'
import { PetStage } from './pet/PetStage'
import { getPetBubble, getPetLabel } from './pet/petActions'
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
  notice = '',
}) {
  const { t } = useTranslation()
  const [walkMotion, setWalkMotion] = useState(null)
  const [pressed, setPressed] = useState(false)
  const [reaction, setReaction] = useState('')
  const wasDragging = useRef(false)
  const previousHide = useRef('')
  const panelTimer = useRef(null)
  // 任务播报气泡(notice)优先；否则 idle 时展示默认文案
  const taskPhase = ['queued', 'working', 'paused'].includes(state)
  const bubble = notice || (taskPhase ? getPetLabel(state, t) : state === 'idle' ? getPetBubble('idle', t) : '')
  // 移动端同样启用拖动/贴边探头（触摸驱动）
  const useDock = dock

  const size = isMobile ? 84 : 110
  // 立绘高约为宽的 1.25 倍，估个入口高度供拖动边界/贴边判定用
  const estH = Math.round(size * 1.25)

  const { open, enter, leave, arm, disarm } = usePeekDock({ idleMs: 4000, enabled: useDock })
  const { pos, onPointerDown, dragging, movedRef } = usePetDrag({
    width: size,
    height: estH,
    topRatio: 0.4,
  })
  useEffect(() => {
    const release = () => setPressed(false)
    window.addEventListener('mouseup', release)
    window.addEventListener('touchend', release)
    window.addEventListener('touchcancel', release)
    window.addEventListener('blur', release)
    return () => {
      window.removeEventListener('mouseup', release)
      window.removeEventListener('touchend', release)
      window.removeEventListener('touchcancel', release)
      window.removeEventListener('blur', release)
    }
  }, [])
  useEffect(() => {
    if (dragging) {
      wasDragging.current = true
      setReaction('')
    } else if (wasDragging.current) {
      wasDragging.current = false
      setReaction('released')
    }
  }, [dragging])
  useEffect(() => {
    if (!reaction) return undefined
    const timer = window.setTimeout(() => setReaction(''), reaction === 'tap' ? 320 : 480)
    return () => window.clearTimeout(timer)
  }, [reaction])
  const startPress = event => {
    if (event.type === 'mousedown' && event.button !== 0) return
    setPressed(true)
    onPointerDown(event)
  }

  // 点击先做短动作，再展开对话；贴边时等走出动画结束。
  const handleClick = () => {
    if (useDock && movedRef.current) return
    if (panelTimer.current) return
    const exitingEdge = hideClass === 'is-hide-left' || hideClass === 'is-hide-right' || walkMotion?.direction === 'out'
    if (exitingEdge) enter()
    setReaction('tap')
    panelTimer.current = window.setTimeout(() => {
      panelTimer.current = null
      onOpen?.()
    }, exitingEdge ? 620 : 320)
  }
  useEffect(() => () => {
    if (panelTimer.current) window.clearTimeout(panelTimer.current)
  }, [])

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

  // 持续任务保留气泡内容，但只有新事件和交互才让贴边人物探出。
  const expanded = !useDock || !nearEdge || dragging || open || !!notice
  // 收起方向：贴边且未展开时，取当前边对应的探头类
  const hideClass =
    useDock && nearEdge && !expanded
      ? { left: 'is-hide-left', right: 'is-hide-right', bottom: 'is-hide-bottom' }[edge]
      : ''
  useLayoutEffect(() => {
    const from = previousHide.current
    previousHide.current = hideClass
    if (dragging) {
      setWalkMotion(null)
      return
    }
    if (hideClass === 'is-hide-left' || hideClass === 'is-hide-right') {
      setWalkMotion({ direction: 'in', edge: hideClass })
    } else if (from === 'is-hide-left' || from === 'is-hide-right') {
      setWalkMotion({ direction: 'out', edge: from })
    }
  }, [hideClass, dragging])
  useEffect(() => {
    if (!walkMotion) return undefined
    const timer = window.setTimeout(() => setWalkMotion(null), 560)
    return () => window.clearTimeout(timer)
  }, [walkMotion])
  const walking = !!walkMotion
  const mirroredWalk = walkMotion && (
    (walkMotion.edge === 'is-hide-right' && walkMotion.direction === 'out') ||
    (walkMotion.edge === 'is-hide-left' && walkMotion.direction === 'in')
  )

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

  const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : 1024
  const bubbleOnRight = pos.x < (viewportWidth - size) / 2
  const bubbleWidth = Math.max(48, Math.min(260,
    bubbleOnRight ? viewportWidth - pos.x - size - 26 : pos.x - 26))

  return (
    <div
      className={
        `assistant-fab ${isMobile ? 'is-mobile' : ''} ` +
        `${useDock ? 'is-dock' : ''} ` +
        `${expanded ? 'is-expanded' : ''} ${hideClass} ${walking ? 'is-walking' : ''} ` +
        `${dragging ? 'is-dragging' : ''} ${pressed ? 'is-pressed' : ''} ` +
        `${reaction === 'tap' ? 'is-tapping' : ''} ${reaction === 'released' ? 'is-released' : ''} ` +
        `${bubbleOnRight ? 'is-bubble-right' : ''} ` +
        `${taskPhase ? `is-task-active task-${state}` : ''}`
      }
      style={positionStyle}
      onMouseDown={useDock ? startPress : undefined}
      onTouchStart={useDock ? startPress : undefined}
      onClick={handleClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          handleClick()
        }
      }}
      onFocus={useDock ? enter : undefined}
      onBlur={useDock ? leave : undefined}
      onMouseEnter={useDock && !isMobile ? enter : undefined}
      onMouseLeave={useDock && !isMobile ? leave : undefined}
      role="button"
      tabIndex={0}
      aria-label={taskPhase ? `${t('assistant.title')} · ${getPetLabel(state, t)}` : t('assistant.title')}
    >
      {taskPhase && <span className="assistant-fab-status" aria-hidden="true" />}
      {bubble && (
        <div className="assistant-fab-bubble" style={{ maxWidth: bubbleWidth }} role={notice ? 'status' : undefined}>
          {notice ? (
            <>
              <div className={`assistant-notice-title is-${notice.kind}`}>{notice.title}</div>
              <div className="assistant-notice-body">{notice.body}</div>
            </>
          ) : bubble}
        </div>
      )}
      <div className="assistant-pet-dock-motion">
        <PetStage state={dragging && state === 'idle' ? 'surprised' : state} size={size} floating />
        {!walking && (hideClass === 'is-hide-left' || hideClass === 'is-hide-right') && (
          <img
            className="assistant-pet-peek"
            src={hideClass === 'is-hide-left' ? peekLeft : peekRight}
            alt=""
            draggable={false}
            style={{ width: size, height: Math.round(size * 617 / 512) }}
          />
        )}
        {walking && <span
          aria-hidden="true"
          className={`assistant-pet-walk-frames ${mirroredWalk ? 'is-mirrored' : ''}`}
          style={{ width: size, height: Math.round(size * 617 / 512), backgroundImage: `url(${walkStrip})` }}
        />}
      </div>
    </div>
  )
}

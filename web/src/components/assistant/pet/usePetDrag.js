/**
 * 拖动 Hook（usePetDrag）
 * ------------------------------------------------------------
 * 让看板娘入口可被鼠标任意拖动，位置持久化到 localStorage，刷新后记住。
 * 通过"移动阈值"区分「拖动」与「点击」：位移小于阈值视为点击（打开面板），
 * 超过阈值视为拖动（不触发点击）。
 *
 * ⚡ 屏幕缩放自适应：
 * 使用百分比定位（相对视口宽高），在浏览器缩放或移动端屏幕旋转时保持相对位置。
 *
 * @param {object} opts
 * @param {number} opts.width    入口宽度（用于初始靠右定位与边界约束）
 * @param {number} opts.height   入口高度（用于边界约束）
 * @param {number} opts.margin   贴边留白（默认 16）
 * @param {number} opts.topRatio 初始纵向位置占视口比例（默认 0.4，越小越靠上）
 * @param {string} opts.storeKey localStorage 键名
 * @returns {{ pos, onMouseDown, dragging, movedRef }}
 *   pos：{x,y} 当前左上角坐标（px，fixed 定位）
 *   onMouseDown：绑到入口的按下事件
 *   dragging：是否正在拖动
 *   movedRef：ref，值为 true 表示刚发生过拖动（供 click 判断是否拦截）
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const DRAG_THRESHOLD = 5 // px，超过才算拖动

// why：参考 MP 悬浮按钮用 right/bottom 贴边定位的做法，位置改为「锚定最近的左/右边 + 距边像素」，
// 纵向用比例；这样窗口缩放、手机横竖屏、地址栏伸缩时入口始终贴着同一侧，不会跑出屏幕。
// 存储格式：{ anchor: 'left'|'right', gap: px, y: 0~1 }
function fromLegacy(p) {
  // 兼容旧版（{x,y} 比例或像素），换算成锚点格式
  if (typeof window === 'undefined') return null
  const w = window.innerWidth
  const xPx = p.x > 1 ? p.x : p.x * w
  const yRatio = p.y > 1 ? p.y / window.innerHeight : p.y
  return xPx > w / 2
    ? { anchor: 'right', gap: Math.max(0, w - xPx), y: yRatio }
    : { anchor: 'left', gap: Math.max(0, xPx), y: yRatio }
}

function loadStored(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const p = JSON.parse(raw)
    if (p && (p.anchor === 'left' || p.anchor === 'right') && typeof p.gap === 'number' && typeof p.y === 'number') return p
    if (typeof p?.x === 'number' && typeof p?.y === 'number') return fromLegacy(p)
  } catch {
    /* 忽略解析失败 */
  }
  return null
}

export function usePetDrag({
  width = 110,
  height = 140,
  margin = 16,
  topRatio = 0.4,
  storeKey = 'assistant-pet-pos',
} = {}) {
  // 初始位置：优先用存储值，否则默认贴右侧、纵向 topRatio 处
  const [posRatio, setPosRatio] = useState(
    () => loadStored(storeKey) || { anchor: 'right', gap: margin, y: topRatio }
  )
  // 视口尺寸放进 state，缩放/旋转时触发重新渲染
  const [, setViewportTick] = useState(0)
  const [dragging, setDragging] = useState(false)
  const movedRef = useRef(false)
  // 拖动过程中的临时数据
  const start = useRef({ mx: 0, my: 0, px: 0, py: 0 })

  // 锚点 → 像素：x 由锚定边与距边像素算出（右锚定时是 left = 视口宽 - 距右边 - 宽度）
  const toPixels = useCallback(
    ratio => {
      if (typeof window === 'undefined') return { x: 0, y: 0 }
      const x = ratio.anchor === 'right' ? window.innerWidth - ratio.gap - width : ratio.gap
      return { x: Math.round(x), y: Math.round(ratio.y * window.innerHeight) }
    },
    [width]
  )

  // 像素 → 锚点：按入口中心落在哪半边决定锚定左/右
  const toRatio = useCallback(
    px => {
      if (typeof window === 'undefined') return { anchor: 'right', gap: margin, y: topRatio }
      const w = window.innerWidth
      const right = px.x + width / 2 > w / 2
      return {
        anchor: right ? 'right' : 'left',
        gap: Math.max(0, right ? w - px.x - width : px.x),
        y: px.y / window.innerHeight,
      }
    },
    [width, margin, topRatio]
  )

  const clamp = useCallback(
    (x, y) => {
      const maxX = window.innerWidth - width - margin
      const maxY = window.innerHeight - height - margin
      // 视口比入口还小时 max 可能小于 margin，此时以 margin 兜底，保证左上角可见
      return {
        x: Math.max(margin, Math.min(x, Math.max(margin, maxX))),
        y: Math.max(margin, Math.min(y, Math.max(margin, maxY))),
      }
    },
    [width, height, margin]
  )

  // 当前像素位置（供渲染使用）
  // why：存储的是比例，桌面端存下的靠右比例在窄屏手机上会让入口大半越出右边界，
  // 再叠加贴边探头平移后完全不可见；渲染前必须约束在视口内。
  const rawPos = toPixels(posRatio)
  const pos = typeof window === 'undefined' ? rawPos : clamp(rawPos.x, rawPos.y)

  // 统一从鼠标/触摸事件提取坐标（触摸取第一个触点）
  const getPoint = ev => {
    if (ev.touches && ev.touches.length) return { x: ev.touches[0].clientX, y: ev.touches[0].clientY }
    if (ev.changedTouches && ev.changedTouches.length) return { x: ev.changedTouches[0].clientX, y: ev.changedTouches[0].clientY }
    return { x: ev.clientX, y: ev.clientY }
  }

  // 指针按下：同时支持鼠标(mousedown)与触摸(touchstart)，实现移动端可拖动
  const onPointerDown = useCallback(
    e => {
      const isTouch = e.type === 'touchstart'
      // 鼠标仅响应左键；触摸不判断 button
      if (!isTouch && e.button !== 0) return
      movedRef.current = false
      const p0 = getPoint(e.nativeEvent || e)
      start.current = { mx: p0.x, my: p0.y, px: pos.x, py: pos.y }

      const onMove = ev => {
        const p = getPoint(ev)
        const dx = p.x - start.current.mx
        const dy = p.y - start.current.my
        if (!movedRef.current && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
          movedRef.current = true
          setDragging(true)
        }
        if (movedRef.current) {
          // 触摸拖动时阻止页面滚动
          if (ev.cancelable) ev.preventDefault()
          const newPos = clamp(start.current.px + dx, start.current.py + dy)
          // 实时更新百分比位置
          setPosRatio(toRatio(newPos))
        }
      }
      const onUp = () => {
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        window.removeEventListener('touchmove', onMove)
        window.removeEventListener('touchend', onUp)
        setDragging(false)
        if (movedRef.current) {
          // 存储百分比位置
          setPosRatio(ratio => {
            try {
              localStorage.setItem(storeKey, JSON.stringify(ratio))
            } catch {
              /* 忽略写入失败 */
            }
            return ratio
          })
        }
      }
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
      // touchmove 需 passive:false 才能 preventDefault 阻止滚动
      window.addEventListener('touchmove', onMove, { passive: false })
      window.addEventListener('touchend', onUp)
    },
    [pos.x, pos.y, clamp, storeKey, toRatio]
  )

  // 视口变化时只触发重渲染：锚点 + 渲染前 clamp 已保证在可视区内，不改写用户存储的位置。
  // 手机端地址栏伸缩只触发 visualViewport.resize，横竖屏切换补 orientationchange。
  useEffect(() => {
    const onResize = () => setViewportTick(n => n + 1)
    const vv = window.visualViewport
    window.addEventListener('resize', onResize)
    window.addEventListener('orientationchange', onResize)
    vv?.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('orientationchange', onResize)
      vv?.removeEventListener('resize', onResize)
    }
  }, [])

  // onMouseDown 保留为别名（兼容旧调用），onPointerDown 同时支持鼠标+触摸
  return { pos, onPointerDown, onMouseDown: onPointerDown, dragging, movedRef }
}

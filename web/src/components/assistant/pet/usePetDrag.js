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

function loadStored(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const p = JSON.parse(raw)
    // 兼容旧版：如果存储的是像素值，转换为百分比
    if (typeof p?.x === 'number' && typeof p?.y === 'number') {
      // 检查是否为百分比格式（0-1之间）
      if (p.x > 1 || p.y > 1) {
        // 旧版像素值，转换为百分比
        if (typeof window !== 'undefined') {
          return {
            x: p.x / window.innerWidth,
            y: p.y / window.innerHeight,
          }
        }
      }
      return p
    }
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
  // 初始位置：优先用存储值（百分比），否则默认靠右、纵向 topRatio 处
  const [posRatio, setPosRatio] = useState(() => {
    const stored = loadStored(storeKey)
    if (stored) return stored
    if (typeof window === 'undefined') return { x: 0.85, y: topRatio }
    // 默认位置：右侧、topRatio 处（百分比）
    const defaultX = (window.innerWidth - width - margin) / window.innerWidth
    return {
      x: Math.max(0, Math.min(1, defaultX)),
      y: topRatio,
    }
  })
  const [dragging, setDragging] = useState(false)
  const movedRef = useRef(false)
  // 拖动过程中的临时数据
  const start = useRef({ mx: 0, my: 0, px: 0, py: 0 })

  // 百分比转像素：根据当前视口尺寸计算实际位置
  const toPixels = useCallback(
    ratio => {
      if (typeof window === 'undefined') return { x: 0, y: 0 }
      return {
        x: Math.round(ratio.x * window.innerWidth),
        y: Math.round(ratio.y * window.innerHeight),
      }
    },
    []
  )

  // 像素转百分比：存储时转换为相对位置
  const toRatio = useCallback(
    px => {
      if (typeof window === 'undefined') return { x: 0, y: 0 }
      return {
        x: px.x / window.innerWidth,
        y: px.y / window.innerHeight,
      }
    },
    []
  )

  // 当前像素位置（供渲染使用）
  const pos = toPixels(posRatio)

  const clamp = useCallback(
    (x, y) => {
      const maxX = window.innerWidth - width - margin
      const maxY = window.innerHeight - height - margin
      return {
        x: Math.max(margin, Math.min(x, maxX)),
        y: Math.max(margin, Math.min(y, maxY)),
      }
    },
    [width, height, margin]
  )

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

  // 视口变化时重新计算像素位置（百分比不变，像素位置自动适配）
  useEffect(() => {
    const onResize = () => {
      // 强制重新计算：将当前百分比约束到合法范围
      setPosRatio(ratio => {
        const px = toPixels(ratio)
        const clamped = clamp(px.x, px.y)
        return toRatio(clamped)
      })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [clamp, toPixels, toRatio])

  // onMouseDown 保留为别名（兼容旧调用），onPointerDown 同时支持鼠标+触摸
  return { pos, onPointerDown, onMouseDown: onPointerDown, dragging, movedRef }
}

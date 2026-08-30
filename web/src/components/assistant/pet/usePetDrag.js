/**
 * 拖动 Hook（usePetDrag）
 * ------------------------------------------------------------
 * 让看板娘入口可被鼠标任意拖动，位置持久化到 localStorage，刷新后记住。
 * 通过"移动阈值"区分「拖动」与「点击」：位移小于阈值视为点击（打开面板），
 * 超过阈值视为拖动（不触发点击）。
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
    if (typeof p?.x === 'number' && typeof p?.y === 'number') return p
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
  // 初始位置：优先用存储值，否则默认靠右、纵向 topRatio 处
  const [pos, setPos] = useState(() => {
    const stored = loadStored(storeKey)
    if (stored) return stored
    if (typeof window === 'undefined') return { x: 0, y: 0 }
    return {
      x: window.innerWidth - width - margin,
      y: Math.round(window.innerHeight * topRatio),
    }
  })
  const [dragging, setDragging] = useState(false)
  const movedRef = useRef(false)
  // 拖动过程中的临时数据
  const start = useRef({ mx: 0, my: 0, px: 0, py: 0 })

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

  const onMouseDown = useCallback(
    e => {
      // 只响应左键
      if (e.button !== 0) return
      movedRef.current = false
      start.current = { mx: e.clientX, my: e.clientY, px: pos.x, py: pos.y }

      const onMove = ev => {
        const dx = ev.clientX - start.current.mx
        const dy = ev.clientY - start.current.my
        if (!movedRef.current && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
          movedRef.current = true
          setDragging(true)
        }
        if (movedRef.current) {
          setPos(clamp(start.current.px + dx, start.current.py + dy))
        }
      }
      const onUp = () => {
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        setDragging(false)
        if (movedRef.current) {
          // 拖动结束后持久化位置
          setPos(p => {
            try {
              localStorage.setItem(storeKey, JSON.stringify(p))
            } catch {
              /* 忽略写入失败 */
            }
            return p
          })
        }
      }
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [pos.x, pos.y, clamp, storeKey]
  )

  // 视口变化时把入口约束回可视范围内
  useEffect(() => {
    const onResize = () => setPos(p => clamp(p.x, p.y))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [clamp])

  return { pos, onMouseDown, dragging, movedRef }
}

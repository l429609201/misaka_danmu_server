/**
 * 图片渲染器（ImageRenderer）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 pet/renderers 思路：渲染器是"可插拔"的。
 * 本渲染器用一张静态 PNG 表现当前情绪，切换状态即切换 src，并带淡入过渡。
 *
 * 约定的渲染器接口（以后做 Live2DRenderer 时保持一致即可无缝替换）：
 *   props.state   当前情绪状态字符串
 *   props.size    渲染尺寸（px）
 *   props.className 额外类名
 */
import { useEffect, useRef, useState } from 'react'
import { getPetImage } from '../petActions'

export function ImageRenderer({ state = 'idle', size = 120, className = '' }) {
  const nextSrc = getPetImage(state)
  // 用于淡入：切图时先降透明度再升起
  const [visible, setVisible] = useState(true)
  const [src, setSrc] = useState(nextSrc)
  const prevState = useRef(state)

  useEffect(() => {
    if (prevState.current === state) return
    prevState.current = state
    // 触发一次淡出 -> 换图 -> 淡入
    setVisible(false)
    const t = setTimeout(() => {
      setSrc(nextSrc)
      setVisible(true)
    }, 120)
    return () => clearTimeout(t)
  }, [state, nextSrc])

  return (
    <img
      src={src}
      alt="assistant"
      draggable={false}
      className={`assistant-pet-img ${visible ? 'is-visible' : 'is-hidden'} ${className}`}
      style={{ width: size, height: 'auto' }}
      // 单张图加载失败时不至于破图，隐藏 alt 文本
      onError={e => {
        e.currentTarget.style.visibility = 'hidden'
      }}
    />
  )
}

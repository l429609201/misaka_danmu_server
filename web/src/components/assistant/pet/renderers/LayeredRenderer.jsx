/**
 * 分层渲染器（LayeredRenderer）
 * ------------------------------------------------------------
 * 用于 idle 状态：本体（眼珠已涂成眼白）打底 + 独立眼睛层叠加，
 * 眼睛层随鼠标小幅移动（跟随）并定时眨眼（scaleY 压扁）。
 * 定位参数在 eye-demo.html 中实测标定，集中放在 petActions 的 IDLE_LAYERS。
 *
 * 与 ImageRenderer 接口保持一致（state/size/className），可被 PetStage 无缝调用。
 */
import { useEffect, useRef } from 'react'
import { IDLE_LAYERS } from '../petActions'

export function LayeredRenderer({ size = 120, className = '' }) {
  const { bodyImg, eyesImg, eyeWidthRatio, eyeAspect, cx, cy, followAmp } = IDLE_LAYERS

  const stageRef = useRef(null)
  const eyesRef = useRef(null)
  // 目标位移与当前位移（rAF 平滑插值），眨眼缩放
  const target = useRef({ x: 0, y: 0 })
  const cur = useRef({ x: 0, y: 0 })
  const blink = useRef(1)

  // 眼睛层布局：按舞台实际宽度等比换算尺寸与居中定位
  const layout = () => {
    const stage = stageRef.current
    const eyes = eyesRef.current
    if (!stage || !eyes) return
    const sw = stage.clientWidth
    const sh = stage.clientHeight || sw * (1852 / 1537)
    const ew = sw * eyeWidthRatio
    const eh = ew * eyeAspect
    eyes.style.width = `${ew}px`
    eyes.style.left = `${(sw * cx) / 100 - ew / 2}px`
    eyes.style.top = `${(sh * cy) / 100 - eh / 2}px`
  }

  useEffect(() => {
    layout()
    const onResize = () => layout()
    window.addEventListener('resize', onResize)

    // 鼠标跟随：相对舞台中心的方向，限幅到 followAmp
    const onMove = e => {
      const stage = stageRef.current
      if (!stage) return
      const r = stage.getBoundingClientRect()
      const centerX = r.left + r.width / 2
      const centerY = r.top + (r.height * cy) / 100
      const dx = (e.clientX - centerX) / window.innerWidth
      const dy = (e.clientY - centerY) / window.innerHeight
      target.current.x = Math.max(-1, Math.min(1, dx * 2)) * followAmp
      target.current.y = Math.max(-1, Math.min(1, dy * 2)) * followAmp
    }
    window.addEventListener('mousemove', onMove)

    // rAF：位移平滑逼近 + 应用眨眼缩放
    let raf = 0
    const tick = () => {
      cur.current.x += (target.current.x - cur.current.x) * 0.12
      cur.current.y += (target.current.y - cur.current.y) * 0.12
      const eyes = eyesRef.current
      if (eyes) {
        eyes.style.transform =
          `translate(${cur.current.x}px, ${cur.current.y}px) scaleY(${blink.current})`
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    // 眨眼：随机间隔压扁一下
    let blinkTimer = 0
    const scheduleBlink = () => {
      blinkTimer = setTimeout(() => {
        blink.current = 0.1
        setTimeout(() => { blink.current = 1 }, 110)
        scheduleBlink()
      }, 2500 + Math.random() * 2500)
    }
    scheduleBlink()

    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('mousemove', onMove)
      cancelAnimationFrame(raf)
      clearTimeout(blinkTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      ref={stageRef}
      className={`assistant-pet-layered ${className}`}
      style={{ position: 'relative', width: size, height: 'auto' }}
    >
      <img
        src={bodyImg}
        alt="assistant"
        draggable={false}
        className="assistant-pet-img is-visible"
        style={{ width: '100%', height: 'auto', display: 'block' }}
        onLoad={layout}
        onError={e => { e.currentTarget.style.visibility = 'hidden' }}
      />
      <img
        ref={eyesRef}
        src={eyesImg}
        alt=""
        draggable={false}
        style={{
          position: 'absolute',
          transformOrigin: 'center center',
          pointerEvents: 'none',
        }}
        onLoad={layout}
      />
    </div>
  )
}

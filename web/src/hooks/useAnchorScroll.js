import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * 锚点定位 hook：用于"全功能搜索"点击结果后，滚动到目标功能区块并高亮。
 *
 * 工作机制：
 *   - 读取 location.hash（如 #feat-bullet-output）
 *   - 由于 Tab 切换后子组件 DOM 才渲染，采用有限次重试查找元素
 *   - 找到后 scrollIntoView + 添加高亮 class，闪烁后自动移除
 *
 * 用法：在 Layout 或根组件中调用一次 useAnchorScroll() 即可全局生效。
 *
 * @param {object} options
 * @param {number} options.maxRetries  最大重试次数（默认 20，约 20*100ms=2s）
 * @param {number} options.retryDelay  每次重试间隔 ms（默认 100）
 * @param {number} options.highlightMs 高亮持续时间 ms（默认 2000）
 */
export function useAnchorScroll({ maxRetries = 20, retryDelay = 100, highlightMs = 2000 } = {}) {
  const location = useLocation()

  useEffect(() => {
    const hash = location.hash?.replace(/^#/, '')
    if (!hash) return

    let retries = 0
    let timer = null
    let highlightTimer = null
    let cancelled = false

    const tryLocate = () => {
      if (cancelled) return
      const el = document.getElementById(hash)
      if (el) {
        // 滚动到目标功能区块
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        // 添加高亮 class，闪烁提示
        el.classList.add('feature-anchor-highlight')
        highlightTimer = setTimeout(() => {
          el.classList.remove('feature-anchor-highlight')
        }, highlightMs)
        return
      }
      // 元素尚未渲染（Tab 切换中），重试
      if (retries < maxRetries) {
        retries += 1
        timer = setTimeout(tryLocate, retryDelay)
      }
    }

    // 首次延迟到下一帧，等待路由/Tab 渲染开始
    timer = setTimeout(tryLocate, retryDelay)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      if (highlightTimer) clearTimeout(highlightTimer)
    }
    // 依赖 pathname + search + hash：跳转到不同功能时重新定位
  }, [location.pathname, location.search, location.hash, maxRetries, retryDelay, highlightMs])
}

export default useAnchorScroll

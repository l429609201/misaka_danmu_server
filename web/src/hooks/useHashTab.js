import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * 内层 Tab 深链 hook：让「功能搜索」的锚点能直达页面内嵌 Tabs 的某一项。
 *
 * 背景：外层 Tab 由 URL 的 ?key= 控制，但组件内部再嵌一层 Tabs 时，
 * 其激活项只存在于组件 state，锚点跳过去时目标仍藏在未激活的面板里，
 * useAnchorScroll 就找不到元素、定位失效。此 hook 把 hash 映射成内层 Tab key。
 *
 * @param {Record<string,string>} anchorToKey 锚点 id -> 内层 Tab key 的映射，
 *        必须定义为模块级常量（引用稳定），避免每次渲染触发副作用。
 * @param {string} defaultKey 默认激活的内层 Tab key
 * @returns {[string, Function]} [activeKey, setActiveKey]
 */
export function useHashTab(anchorToKey, defaultKey) {
  const location = useLocation()
  const [activeKey, setActiveKey] = useState(defaultKey)

  useEffect(() => {
    const hash = location.hash?.replace(/^#/, '')
    if (!hash) return
    const mapped = anchorToKey[hash]
    if (mapped) setActiveKey(mapped)
  }, [location.hash, location.search, anchorToKey])

  return [activeKey, setActiveKey]
}

export default useHashTab

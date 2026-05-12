import React, { useState, useEffect } from 'react'
import { Tabs } from 'antd'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store/index.js'
import classNames from 'classnames'

/**
 * 统一的标签页组件
 * 使用Ant Design的Tabs组件
 */
export const MobileTabs = ({ items, defaultActiveKey, onChange, ...props }) => {
  const isMobile = useAtomValue(isMobileAtom)
  const [activeKey, setActiveKey] = useState(defaultActiveKey || items[0]?.key)

  // 同步外部 defaultActiveKey 变化（如通过底部导航菜单切换 tab 时 URL 变化）
  useEffect(() => {
    if (defaultActiveKey && defaultActiveKey !== activeKey) {
      setActiveKey(defaultActiveKey)
    }
  }, [defaultActiveKey])

  const handleChange = (key) => {
    setActiveKey(key)
    onChange?.(key)
  }

  // 统一使用Tabs组件
  return (
    <Tabs
      activeKey={activeKey}
      items={items}
      onChange={(key) => {
        setActiveKey(key)
        onChange?.(key)
      }}
      {...props}
    />
  )
}
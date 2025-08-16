import { createContext, useContext, useEffect, useState } from 'react'
import { ConfigProvider } from 'antd'
import { theme } from 'antd'

import zhCN from 'antd/locale/zh_CN'

// 创建上下文
const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  const [isDark, setIsDark] = useState(false)

  // 初始化：检查系统偏好或本地存储
  useEffect(() => {
    const prefersDark =
      localStorage.theme === 'dark' ||
      (!('theme' in localStorage) &&
        window.matchMedia('(prefers-color-scheme: dark)').matches)

    setIsDark(prefersDark)
    document.documentElement.classList.toggle('dark', prefersDark)
  }, [])

  // 切换暗黑模式
  const toggleDarkMode = () => {
    const newDarkState = !isDark
    setIsDark(newDarkState)
    document.documentElement.classList.toggle('dark', newDarkState)
    localStorage.theme = newDarkState ? 'dark' : 'light'
  }

  // AntD 5 主题配置（核心）
  const { defaultAlgorithm, darkAlgorithm } = theme
  const antdConfig = {
    // 切换明暗算法
    algorithm: isDark ? darkAlgorithm : defaultAlgorithm,
    // 自定义主题变量（与Tailwind保持一致）
    token: {
      colorPrimary: isDark ? '#60a5fa' : '#3b82f6', // 与Tailwind primary匹配
      borderRadius: 4,
    },
    // 组件级样式调整
    components: {
      Button: {
        colorPrimaryHover: isDark ? '#3b82f6' : '#2563eb',
      },
    },
  }

  return (
    <ThemeContext.Provider value={{ isDark, toggleDarkMode }}>
      <ConfigProvider locale={zhCN} theme={antdConfig}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}

// 自定义Hook
export function useThemeMode() {
  return useContext(ThemeContext)
}

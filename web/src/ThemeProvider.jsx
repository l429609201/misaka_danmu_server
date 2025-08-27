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
  const lightTheme = {
    algorithm: defaultAlgorithm,
    token: {
      // 主色调：柔和的粉色，符合二次元风格
      colorPrimary: '#FF6B9B',
      colorPrimaryHover: '#FF528A',
      colorPrimaryActive: '#FF3879',

      // 辅助色
      colorSuccess: '#389e0d',
      colorWarning: '#FFD166',
      colorError: '#FF5252',
      colorInfo: '#64B5F6',

      // 背景色
      colorBgBase: '#FFF9FB',
      colorBgContainer: '#FFFFFF',
      colorBgElevated: '#FFF0F5',

      // 文本色
      colorTextBase: '#333333',
      colorTextSecondary: '#666666',
      colorTextTertiary: '#999999',

      // 边框色
      colorBorder: '#FFD9E5',
      colorBorderSecondary: '#FFE6EF',

      // 字体设置，选用更圆润的字体
      fontFamily: "'MyNunito', 'Nunito', 'Comic Sans MS', sans-serif",
    },
    components: {
      Button: {
        borderRadius: 20,
        fontSize: 14,
        height: 40,
      },
      Card: {
        borderRadius: 12,
        boxShadow: '0 4px 16px rgba(255, 107, 155, 0.1)',
        colorBorder: '#FFD9E5',
      },
      Tabs: {
        colorPrimary: '#FF6B9B',
        borderRadius: 8,
        itemActiveColor: '#FF6B9B',
      },
      Table: {
        colorBorder: '#FFD9E5',
        borderRadius: 8,
        headerBg: '#FFF0F5',
      },
      List: {
        colorBorder: '#FFD9E5',
        itemHoverBg: '#FFF0F5',
      },
      Form: {
        colorBorder: '#FFD9E5',
        itemMarginBottom: 16,
      },
      Input: {
        borderRadius: 8,
        borderColor: '#FFD9E5',
        hoverBorderColor: '#FF6B9B',
      },
    },
  }

  // 二次元风格暗色主题配置
  const darkTheme = {
    algorithm: darkAlgorithm,
    token: {
      // 主色调：在暗色背景上更突出的亮粉色
      colorPrimary: '#FF6B9B',
      colorPrimaryHover: '#FF528A',
      colorPrimaryActive: '#FF3879',

      // 辅助色（在暗色背景上更明亮）
      colorSuccess: '#6abe39',
      colorWarning: '#FFC850',
      colorError: '#FF5252',
      colorInfo: '#7DCFFF',

      // 背景色（更新为蓝紫色调）
      colorBgBase: '#0F172A', // 深蓝灰色作为整体背景
      colorBgContainer: '#1E293B', // 稍浅的蓝色作为容器背景
      colorBgElevated: '#273449', // 更高层级的元素背景

      // 文本色
      colorTextBase: '#F8FAFC',
      colorTextSecondary: '#E2E8F0',
      colorTextTertiary: '#94A3B8',

      // 边框色（配合蓝紫色调）
      colorBorder: '#334155',
      colorBorderSecondary: '#2A3A51',

      // 字体设置
      fontFamily: "'MyNunito', 'Nunito', 'Comic Sans MS', sans-serif",
    },
    components: {
      Button: {
        borderRadius: 20,
        fontSize: 14,
        height: 40,
      },
      Card: {
        borderRadius: 12,
        boxShadow: '0 4px 16px rgba(255, 107, 155, 0.12)',
        colorBorder: '#334155',
      },
      Tabs: {
        colorPrimary: '#FF6B9B',
        borderRadius: 8,
        itemActiveColor: '#FF6B9B',
      },
      Table: {
        colorBorder: '#334155',
        borderRadius: 8,
        headerBg: '#273449',
      },
      List: {
        colorBorder: '#334155',
        itemHoverBg: '#273449',
      },
      Form: {
        colorBorder: '#334155',
        itemMarginBottom: 16,
      },
      Input: {
        borderRadius: 8,
        borderColor: '#334155',
        hoverBorderColor: '#FF6B9B',
      },
    },
  }

  return (
    <ThemeContext.Provider value={{ isDark, toggleDarkMode }}>
      <ConfigProvider locale={zhCN} theme={isDark ? darkTheme : lightTheme}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}

// 自定义Hook
export function useThemeMode() {
  return useContext(ThemeContext)
}

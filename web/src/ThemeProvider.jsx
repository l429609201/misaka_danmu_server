import { createContext, useContext, useEffect, useState, useMemo } from 'react'
import { ConfigProvider } from 'antd'
import { theme } from 'antd'

import zhCN from 'antd/locale/zh_CN'

// ========== 颜色工具函数 ==========

// hex 转 RGB
function hexToRgb(hex) {
  const h = hex.replace('#', '')
  return {
    r: parseInt(h.substring(0, 2), 16),
    g: parseInt(h.substring(2, 4), 16),
    b: parseInt(h.substring(4, 6), 16),
  }
}

// RGB 转 hex
function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(x => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, '0')).join('')
}

// 混合两个颜色（ratio: 0=全是color2, 1=全是color1）
function mixColors(color1, color2, ratio) {
  const c1 = hexToRgb(color1)
  const c2 = hexToRgb(color2)
  return rgbToHex(
    c1.r * ratio + c2.r * (1 - ratio),
    c1.g * ratio + c2.g * (1 - ratio),
    c1.b * ratio + c2.b * (1 - ratio),
  )
}

// 加深颜色
function darkenColor(hex, amount) {
  const { r, g, b } = hexToRgb(hex)
  return rgbToHex(r * (1 - amount), g * (1 - amount), b * (1 - amount))
}

// hex 转 rgba 字符串
function hexToRgba(hex, alpha) {
  const { r, g, b } = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// 根据主色生成完整的派生色方案
function generateThemeColors(primaryColor) {
  const hoverColor = darkenColor(primaryColor, 0.1)
  const activeColor = darkenColor(primaryColor, 0.2)
  // 亮色模式下的派生色（与白色混合）
  const lightBorder = mixColors(primaryColor, '#FFFFFF', 0.15)
  const lightBorderSecondary = mixColors(primaryColor, '#FFFFFF', 0.1)
  const lightHoverBg = mixColors(primaryColor, '#FFFFFF', 0.06)
  const lightBgBase = mixColors(primaryColor, '#FFFFFF', 0.03)
  const lightHeaderBg = mixColors(primaryColor, '#FFFFFF', 0.08)
  const lightScrollbarThumb = mixColors(primaryColor, '#FFFFFF', 0.3)
  return {
    primary: primaryColor,
    hover: hoverColor,
    active: activeColor,
    light: {
      border: lightBorder,
      borderSecondary: lightBorderSecondary,
      hoverBg: lightHoverBg,
      bgBase: lightBgBase,
      headerBg: lightHeaderBg,
      scrollbarThumb: lightScrollbarThumb,
    },
    shadow: hexToRgba(primaryColor, 0.12),
    shadowLight: hexToRgba(primaryColor, 0.1),
  }
}

// 预设主题色
export const PRESET_THEME_COLORS = [
  { name: '樱花粉', color: '#FF6B9B' },
  { name: '天空蓝', color: '#4096FF' },
  { name: '深海蓝', color: '#1677FF' },
  { name: '薄荷绿', color: '#52C41A' },
  { name: '葡萄紫', color: '#722ED1' },
  { name: '薰衣草', color: '#9254DE' },
  { name: '落日橙', color: '#FA8C16' },
  { name: '中国红', color: '#F5222D' },
]

const DEFAULT_PRIMARY = '#FF6B9B'

// 创建上下文
const ThemeContext = createContext()

export function ThemeProvider({ children }) {
  const [isDark, setIsDark] = useState(false)
  const [themeColor, setThemeColorState] = useState(() => {
    return localStorage.getItem('themeColor') || DEFAULT_PRIMARY
  })

  // 根据主色生成派生色
  const colors = useMemo(() => generateThemeColors(themeColor), [themeColor])

  // 动态更新 CSS 变量
  const applyCssVariables = (isDarkMode, colorScheme) => {
    const root = document.documentElement
    // 主色始终更新
    root.style.setProperty('--color-primary', colorScheme.primary)
    root.style.setProperty('--color-primary-dark', colorScheme.hover)
    root.style.setProperty('--color-shadow', colorScheme.shadow)

    if (!isDarkMode) {
      // 亮色模式：背景、边框、hover 都跟随主色
      root.style.setProperty('--color-bg', colorScheme.light.bgBase)
      root.style.setProperty('--color-hover', colorScheme.light.hoverBg)
      root.style.setProperty('--color-border', colorScheme.light.border)
      root.style.setProperty('--scrollbar-thumb', colorScheme.light.scrollbarThumb)
      root.style.setProperty('--scrollbar-thumb-hover', colorScheme.primary)
    } else {
      // 暗色模式：背景/边框保持 slate 色调，只更新主色
      root.style.setProperty('--color-bg', '#0f172a')
      root.style.setProperty('--color-hover', '#273449')
      root.style.setProperty('--color-border', '#334155')
      root.style.setProperty('--scrollbar-thumb', '#475569')
      root.style.setProperty('--scrollbar-thumb-hover', '#64748b')
    }
  }

  // 设置主题色并持久化
  const setThemeColor = (color) => {
    setThemeColorState(color)
    localStorage.setItem('themeColor', color)
  }

  // 初始化：检查系统偏好或本地存储
  useEffect(() => {
    const prefersDark =
      localStorage.theme === 'dark' ||
      (!('theme' in localStorage) &&
        window.matchMedia('(prefers-color-scheme: dark)').matches)

    setIsDark(prefersDark)
    document.documentElement.classList.toggle('dark', prefersDark)
    updateMetaThemeColor(prefersDark)
  }, [])

  // 当 isDark 或 themeColor 变化时，更新 CSS 变量
  useEffect(() => {
    applyCssVariables(isDark, colors)
  }, [isDark, colors])

  // 切换暗黑模式
  const toggleDarkMode = () => {
    const newDarkState = !isDark
    setIsDark(newDarkState)
    document.documentElement.classList.toggle('dark', newDarkState)
    localStorage.theme = newDarkState ? 'dark' : 'light'
    updateMetaThemeColor(newDarkState)
  }

  // 更新meta标签主题色的函数
  const updateMetaThemeColor = isDarkMode => {
    const metaColor = isDarkMode ? '#0f172a' : colors.light.bgBase
    const themeColorMeta = document.querySelector('meta[name="theme-color"]')
    if (themeColorMeta) {
      themeColorMeta.setAttribute('content', metaColor)
    }
    const statusBarMeta = document.querySelector(
      'meta[name="apple-mobile-web-app-status-bar-style"]'
    )
    if (statusBarMeta) {
      statusBarMeta.setAttribute('content', metaColor)
    }
  }

  // AntD 5 主题配置（核心）- 动态生成
  const { defaultAlgorithm, darkAlgorithm } = theme

  const lightTheme = useMemo(() => ({
    algorithm: defaultAlgorithm,
    token: {
      colorPrimary: colors.primary,
      colorPrimaryHover: colors.hover,
      colorPrimaryActive: colors.active,
      colorSuccess: '#389e0d',
      colorWarning: '#FFD166',
      colorError: '#FF5252',
      colorInfo: '#64B5F6',
      colorBgBase: colors.light.bgBase,
      colorBgContainer: '#FFFFFF',
      colorBgElevated: colors.light.hoverBg,
      colorTextBase: '#333333',
      colorTextSecondary: '#666666',
      colorTextTertiary: '#999999',
      colorBorder: colors.light.border,
      colorBorderSecondary: colors.light.borderSecondary,
      fontFamily: "'MyNunito', 'Nunito', 'Comic Sans MS', sans-serif",
    },
    components: {
      Button: { borderRadius: 20, fontSize: 14, height: 40 },
      Card: {
        borderRadius: 12,
        boxShadow: `0 4px 16px ${colors.shadowLight}`,
        colorBorder: colors.light.border,
      },
      Tabs: {
        colorPrimary: colors.primary,
        borderRadius: 8,
        itemActiveColor: colors.primary,
      },
      Table: {
        colorBorder: colors.light.border,
        borderRadius: 8,
        headerBg: colors.light.hoverBg,
      },
      List: {
        colorBorder: colors.light.border,
        itemHoverBg: colors.light.hoverBg,
      },
      Form: {
        colorBorder: colors.light.border,
        itemMarginBottom: 16,
      },
      Input: {
        borderRadius: 8,
        borderColor: colors.light.border,
        hoverBorderColor: colors.primary,
      },
    },
  }), [colors])

  const darkTheme = useMemo(() => ({
    algorithm: darkAlgorithm,
    token: {
      colorPrimary: colors.primary,
      colorPrimaryHover: colors.hover,
      colorPrimaryActive: colors.active,
      colorSuccess: '#6abe39',
      colorWarning: '#FFC850',
      colorError: '#FF5252',
      colorInfo: '#7DCFFF',
      colorBgBase: '#0F172A',
      colorBgContainer: '#1E293B',
      colorBgElevated: '#273449',
      colorTextBase: '#F8FAFC',
      colorTextSecondary: '#E2E8F0',
      colorTextTertiary: '#94A3B8',
      colorBorder: '#334155',
      colorBorderSecondary: '#2A3A51',
      fontFamily: "'MyNunito', 'Nunito', 'Comic Sans MS', sans-serif",
    },
    components: {
      Button: { borderRadius: 20, fontSize: 14, height: 40 },
      Card: {
        borderRadius: 12,
        boxShadow: `0 4px 16px ${colors.shadow}`,
        colorBorder: '#334155',
      },
      Tabs: {
        colorPrimary: colors.primary,
        borderRadius: 8,
        itemActiveColor: colors.primary,
      },
      Table: {
        colorBorder: '#334155',
        borderRadius: 8,
        headerBg: '#273449',
        rowHoverBg: '#273449',
        colorBgContainer: '#1E293B',
      },
      List: {
        colorBorder: '#334155',
        itemHoverBg: '#273449',
        colorSplit: '#334155',
        colorBgContainer: '#1E293B',
      },
      Form: {
        colorBorder: '#334155',
        itemMarginBottom: 16,
      },
      Input: {
        borderRadius: 8,
        borderColor: '#334155',
        hoverBorderColor: colors.primary,
        colorBgContainer: '#273449',
      },
      InputNumber: { colorBgContainer: '#273449' },
      Select: { colorBgContainer: '#273449', colorBgElevated: '#334155' },
      Modal: { contentBg: '#1E293B', headerBg: '#1E293B', footerBg: '#1E293B' },
    },
  }), [colors])

  return (
    <ThemeContext.Provider value={{ isDark, toggleDarkMode, themeColor, setThemeColor }}>
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

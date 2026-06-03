import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import zhCN from './locales/zh-CN'
import zhTW from './locales/zh-TW'
import en from './locales/en'

// 支持的语言列表（用于语言切换器渲染）
export const SUPPORTED_LANGUAGES = [
  { key: 'zh-CN', name: '简体中文', iconfontIcon: 'icon-jiantizhongwen' },
  { key: 'zh-TW', name: '繁體中文', iconfontIcon: 'icon-fantizhongwen' },
  { key: 'en', name: 'English', iconfontIcon: 'icon-yingwenyuyan' },
]

// 默认语言：简体中文
export const DEFAULT_LANGUAGE = 'zh-CN'

const resources = {
  'zh-CN': { translation: zhCN },
  'zh-TW': { translation: zhTW },
  en: { translation: en },
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LANGUAGE,
    // 仅支持这三种语言，其余一律回退到简体中文
    supportedLngs: ['zh-CN', 'zh-TW', 'en'],
    // 把 zh、zh-Hans 等都归一到 zh-CN，zh-Hant 归到 zh-TW
    nonExplicitSupportedLngs: false,
    load: 'currentOnly',
    interpolation: {
      escapeValue: false, // React 已经处理了 XSS 转义
    },
    detection: {
      // 优先读取本地存储的语言设置
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'lang',
      caches: ['localStorage'],
    },
    react: {
      useSuspense: false,
    },
  })

export default i18n

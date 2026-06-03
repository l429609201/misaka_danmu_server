import i18n from '../i18n'

/**
 * 语言后缀映射：i18next language code → 后端 schema 的字段后缀
 *
 * 后端 schema 字段命名规则:
 *   - 默认 (zh-CN): label, description, placeholder, suffix, tips ...
 *   - English:      label_en, description_en, placeholder_en, suffix_en ...
 *   - 繁體中文:     label_tw, description_tw, placeholder_tw, suffix_tw ...
 *
 * 当目标语言的字段不存在时，自动 fallback 到默认字段（简体中文）。
 */
const LANG_SUFFIX_MAP = {
  en: '_en',
  'zh-TW': '_tw',
  // zh-CN 不需要后缀，直接使用原始字段
}

/**
 * 从后端返回的动态 schema 对象中，根据当前语言获取本地化字段值。
 *
 * @param {Object} item    - 后端返回的 schema 对象 (如 config item, job config 等)
 * @param {string} field   - 字段名 (如 'label', 'description', 'placeholder')
 * @param {string} [lang]  - 语言代码，默认使用 i18n 当前语言
 * @returns {*}            - 本地化后的字段值；找不到时返回 item[field] 或 undefined
 *
 * @example
 * // item = { label: '安全设置', label_en: 'Security', label_tw: '安全設定' }
 * getLocalizedField(item, 'label')        // 根据当前语言自动选择
 * getLocalizedField(item, 'label', 'en')  // → 'Security'
 * getLocalizedField(item, 'label', 'zh-TW')  // → '安全設定'
 * getLocalizedField(item, 'label', 'zh-CN')  // → '安全设置'
 *
 * // fallback: 若 label_en 不存在，则返回 label (简体中文)
 * // item = { label: '缓存设置' }
 * getLocalizedField(item, 'label', 'en')  // → '缓存设置' (fallback)
 */
export function getLocalizedField(item, field, lang) {
  if (!item) return undefined
  const currentLang = lang || i18n.resolvedLanguage || i18n.language || 'zh-CN'
  const suffix = LANG_SUFFIX_MAP[currentLang]

  if (suffix) {
    const localizedKey = field + suffix
    if (item[localizedKey] !== undefined && item[localizedKey] !== null && item[localizedKey] !== '') {
      return item[localizedKey]
    }
  }

  // fallback 到默认字段 (zh-CN)
  return item[field]
}

/**
 * 批量本地化数组中的对象。适用于 options 列表等场景。
 *
 * @param {Array} items   - 对象数组
 * @param {string[]} fields - 要本地化的字段名列表
 * @param {string} [lang]   - 语言代码
 * @returns {Array}         - 新数组，每个对象的指定字段已替换为本地化值
 */
export function localizeItems(items, fields = ['label'], lang) {
  if (!Array.isArray(items)) return items
  return items.map(item => {
    const localized = { ...item }
    for (const field of fields) {
      localized[field] = getLocalizedField(item, field, lang)
    }
    return localized
  })
}

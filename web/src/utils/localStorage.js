/**
 * 存到本地存储的操作都在这里定义，方便统一处理
 */

export function setStorage(key, data) {
  localStorage.setItem(key, JSON.stringify(data))
}

export function getStorage(key) {
  try {
    const value = localStorage.getItem(key)
    // 如果值不存在（null），则直接返回 null
    if (value === null) {
      return null
    }
    return JSON.parse(value)
  } catch (error) {
    console.error('解析本地存储失败:', error)
    // 如果解析JSON失败，很可能是因为存储的是一个未被JSON化的原始字符串（例如旧版Token）。
    // 在这种情况下，直接返回原始字符串值，以兼容旧数据格式。
    return localStorage.getItem(key)
  }
}

export function clearStorage(key) {
  localStorage.removeItem(key)
}

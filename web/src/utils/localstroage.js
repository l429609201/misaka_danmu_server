/**
 * 存到本地存储的操作都在这里定义，方便统一处理
 */

export function setStorage(key, data) {
  localStorage.setItem(key, JSON.stringify(data))
}

export function getStorage(key) {
  try {
    const value = localStorage.getItem(key)
    // 如果值不存在（null）或为空字符串，返回默认空对象
    if (value === null || value === '') {
      return {}
    }
    return JSON.parse(value)
  } catch (error) {
    console.error('解析本地存储失败:', error)
    return {}
  }
}

export function clearStorage(key) {
  localStorage.removeItem(key)
}

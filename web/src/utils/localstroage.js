/**
 * 存到本地存储的操作都在这里定义，方便统一处理
 */

export function setStorage(key, data) {
  localStorage.setItem(key, JSON.stringify(data))
}

export function getStorage(key) {
  const value = localStorage.getItem(key)
  if (!value) return {}

  return JSON.parse(value)
}

export function clearStorage(key) {
  localStorage.removeItem(key)
}

import axios from 'axios'
import Cookies from 'js-cookie'

const getURL = url => {
  // 开发环境使用 Vite 代理，生产环境使用相对路径
  const baseURL = '/'
  return { baseURL, url }
}

const instance = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
})

instance.interceptors.request.use(
  async config => {
    const token = Cookies.get('danmu_token')
    if (config.headers && !!token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    return config
  },
  error => Promise.reject(error)
)

instance.interceptors.response.use(
  res => res,
  error => {
    console.log('resError', error.response?.data, error.response?.config.url)

    // 401 未授权：自动清理 token 并跳转登录页
    if (error.response?.status === 401) {
      Cookies.remove('danmu_token', { path: '/' })
      // 避免在登录页重复跳转
      if (!window.location.pathname.includes('/login')) {
        window.location.href = '/login'
      }
    }

    const errorData = error.response?.data || {}
    // 统一转换为message字段,兼容FastAPI的detail字段和自定义的message字段
    const errorObj = {
      ...errorData,
      message: errorData.detail || errorData.message || '未知错误',
      code: error.response?.status
    }
    return Promise.reject(errorObj)
  }
)

const api = {
  get(url, data, other = { headers: {} }) {
    return instance({
      method: 'get',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: { ...other.headers },
      params: data,
      onDownloadProgress: other.onDownloadProgress,
    })
  },
  post(url, data, other = { headers: {} }) {
    return instance({
      method: 'post',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: { ...other.headers },
      data,
      // 同时支持上传和下载进度
      onUploadProgress: other.onUploadProgress,
      onDownloadProgress: other.onDownloadProgress,
    })
  },
  // patch/put/delete 与 post 类似，根据实际需求添加进度配置
  patch(url, data, other = { headers: {} }) {
    return instance({
      method: 'patch',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: { ...other.headers },
      data,
      onUploadProgress: other.onUploadProgress,
      onDownloadProgress: other.onDownloadProgress,
    })
  },
  put(url, data, other = { headers: {} }) {
    return instance({
      method: 'put',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: { ...other.headers },
      data,
      onUploadProgress: other.onUploadProgress,
      onDownloadProgress: other.onDownloadProgress,
    })
  },
  delete(url, data, other = { headers: {} }) {
    // 检查是否是config对象（包含params属性）
    const isConfig = data && typeof data === 'object' && (data.params || data.headers || data.data);
    if (isConfig) {
      return instance({
        method: 'delete',
        baseURL: getURL(url).baseURL,
        url: getURL(url).url,
        headers: { ...other.headers, ...(data.headers || {}) },
        params: data.params,
        data: data.data,
        onUploadProgress: data.onUploadProgress || other.onUploadProgress,
        onDownloadProgress: data.onDownloadProgress || other.onDownloadProgress,
      });
    } else {
      // 向后兼容：data作为请求体
      return instance({
        method: 'delete',
        baseURL: getURL(url).baseURL,
        url: getURL(url).url,
        headers: { ...other.headers },
        data,
        onUploadProgress: other.onUploadProgress,
        onDownloadProgress: other.onDownloadProgress,
      });
    }
  },
}

export default api

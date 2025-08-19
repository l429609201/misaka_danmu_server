import axios from 'axios'
import { getStorage } from '../utils'
import { DANMU_API_TOKEN_KEY } from '../configs'

const getURL = url => {
  return {
    baseURL: 'http://0.0.0.0:7768',
    url: url,
  }
}

const instance = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
})

instance.interceptors.request.use(
  async config => {
    const token = getStorage(DANMU_API_TOKEN_KEY)
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
    return Promise.reject(error.response?.data || {})
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
    return instance({
      method: 'delete',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: { ...other.headers },
      data,
      onUploadProgress: other.onUploadProgress,
      onDownloadProgress: other.onDownloadProgress,
    })
  },
}

export default api

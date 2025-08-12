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
    if (config.headers && token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }

    return config
  },
  error => Promise.reject(error)
)

instance.interceptors.response.use(
  res => {
    return res
  },
  error => {
    console.log(
      'resError',
      error.response && error.response.data,
      error.response && error.response.config.url
    )

    return Promise.reject((error.response && error.response.data) || {})
  }
)
const api = {
  get(url, data, other = { headers: {} }) {
    return instance({
      method: 'get',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: {
        ...other.headers,
      },
      params: data,
    })
  },
  post(url, data, other = { headers: {} }) {
    return instance({
      method: 'post',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: {
        ...other.headers,
      },
      data,
    })
  },
  patch(url, data, other = { headers: {} }) {
    return instance({
      method: 'patch',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: {
        ...other.headers,
      },
      data,
    })
  },
  put(url, data, other = { headers: {} }) {
    return instance({
      method: 'put',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: {
        ...other.headers,
      },
      data,
    })
  },
  delete(url, data, other = { headers: {} }) {
    return instance({
      method: 'delete',
      baseURL: getURL(url).baseURL,
      url: getURL(url).url,
      headers: {
        ...other.headers,
      },
      data,
    })
  },
}

export default api

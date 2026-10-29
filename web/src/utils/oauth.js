/**
 * 通用 OAuth 前端工具：弹窗发起授权、state 存取、完成消息协议。
 * 平台差异全部由后端注册的 OAuthProvider 处理，前端不再为每个来源写专用代码。
 */
import { getOAuthAuthorizeUrl } from '../apis'

/** 回调页通知父窗口时使用的消息类型 */
export const OAUTH_COMPLETE_MESSAGE = 'oauth-complete'

/** 新接入来源默认使用的通用回调路径 */
export const getOAuthCallbackPath = provider => `/oauth/callback/${encodeURIComponent(provider)}`

/** 各来源 state 在 sessionStorage 中的键名 */
export const getOAuthStateKey = provider => `oauth:${provider}:state`

/**
 * 判断 message 事件是否为指定来源的授权完成通知。
 * 只接受同源、且来自本次打开的授权弹窗的消息。
 */
export const isOAuthCompleteMessage = (event, provider, popup) =>
  event.origin === window.location.origin
  && event.source === popup
  && event.data?.type === OAUTH_COMPLETE_MESSAGE
  && event.data?.provider === provider

/**
 * 打开授权弹窗并跳转到后端生成的授权地址。
 * 必须在用户点击的同步阶段调用（首个 await 之前打开窗口），否则会被浏览器拦截。
 * @param {object} options
 * @param {string} options.provider 后端注册的平台名
 * @param {string} [options.callbackPath] 回调路径；需与平台后台登记的回调一致，默认通用路径
 * @returns {Promise<Window>} 已跳转的弹窗
 * @throws {Error} code 为 'popup-blocked' 或 'empty-url'，或接口原始错误
 */
export async function openOAuthPopup({ provider, callbackPath = getOAuthCallbackPath(provider) }) {
  const width = 600
  const height = 700
  const left = window.screen.width / 2 - width / 2
  const top = window.screen.height / 2 - height / 2
  const popup = window.open('about:blank', `oauth-${provider}`,
    `width=${width},height=${height},top=${top},left=${left},resizable=yes,scrollbars=yes`)
  if (!popup || popup.closed) {
    throw Object.assign(new Error('popup blocked'), { code: 'popup-blocked' })
  }
  const storageKey = getOAuthStateKey(provider)
  try {
    const res = await getOAuthAuthorizeUrl(provider, { redirect_uri: `${window.location.origin}${callbackPath}` })
    const data = res.data || res
    if (!data.url || !data.state) {
      throw Object.assign(new Error('empty authorize url'), { code: 'empty-url' })
    }
    if (popup.closed) return popup
    // 窗口已建立，必须显式写入它自己的 sessionStorage，不能依赖创建时的自动复制
    sessionStorage.setItem(storageKey, data.state)
    popup.sessionStorage.setItem(storageKey, data.state)
    popup.location.replace(data.url)
    return popup
  } catch (error) {
    if (!popup.closed) popup.close()
    sessionStorage.removeItem(storageKey)
    throw error
  }
}

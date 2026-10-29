/**
 * 通用 OAuth 回调页面
 *
 * 所有来源共用：把回调 URL 上的全部参数原样交给后端 /api/ui/oauth/{provider}/exchange，
 * 由后端对应的 OAuthProvider 解析（授权码 code、Worker 回传的 access_token 等）。
 * 新接入 OAuth 来源只需后端注册 provider，前端无需新增页面。
 */
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Spin, Result, Button } from 'antd'
import { useTranslation } from 'react-i18next'
import { exchangeOAuth } from '../../apis'
import { OAUTH_COMPLETE_MESSAGE, getOAuthStateKey } from '../../utils/oauth'

/**
 * @param {object} props
 * @param {string} [props.provider] 旧版专用回调路由传入的固定平台名；通用路由从路径参数读取
 */
export default function OAuthCallback({ provider: fixedProvider }) {
  const { t } = useTranslation()
  const params = useParams()
  const provider = fixedProvider || params.provider
  const [status, setStatus] = useState('loading') // loading | success | error
  const [message, setMessage] = useState('')
  const exchangeStarted = useRef(false)

  useEffect(() => {
    // 防止 StrictMode 重复消费一次性 state
    if (exchangeStarted.current) return
    exchangeStarted.current = true
    const storageKey = getOAuthStateKey(provider)
    const url = new URL(window.location.href)
    const payload = Object.fromEntries(url.searchParams.entries())
    // 部分 Worker 回跳时用 "?xxx=" 直接拼在已含 "?state=" 的地址后，
    // 浏览器会把 state 解析成 "<JWT>?xxx=..."；JWT 不含 "?"，截掉即可还原
    const urlState = (payload.state || '').split('?')[0]
    const state = urlState || sessionStorage.getItem(storageKey)
    sessionStorage.removeItem(storageKey)

    if (payload.error) {
      setStatus('error')
      setMessage(payload.error_description || payload.error)
      return
    }
    if (!state) {
      setStatus('error')
      setMessage(t('oauthCallback.stateMissing'))
      return
    }

    // redirect_uri 必须与发起授权时一致，即当前页面去掉查询参数后的地址
    exchangeOAuth(provider, {
      ...payload,
      state,
      redirect_uri: `${window.location.origin}${window.location.pathname}`,
    })
      .then(res => {
        const data = res.data
        if (data.success) {
          setStatus('success')
          setMessage(data.message || t('oauthCallback.authSuccess'))
          try {
            if (window.opener) {
              window.opener.postMessage({ type: OAUTH_COMPLETE_MESSAGE, provider }, window.location.origin)
              setTimeout(() => window.close(), 1500)
            }
          } catch (e) {
            console.error('Failed to notify parent:', e)
          }
        } else {
          setStatus('error')
          setMessage(data.message || t('oauthCallback.authFailed'))
        }
      })
      .catch(err => {
        setStatus('error')
        setMessage(err.response?.data?.detail || err.message || t('oauthCallback.requestFailed'))
      })
  }, [])

  const handleClose = () => {
    window.close()
  }

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      height: '100vh',
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    }}>
      <div style={{
        background: 'white',
        padding: '40px',
        borderRadius: '10px',
        boxShadow: '0 10px 40px rgba(0,0,0,0.1)',
        textAlign: 'center',
        minWidth: '320px',
      }}>
        {status === 'loading' && (
          <div>
            <Spin size="large" />
            <p style={{ marginTop: 16, color: '#666' }}>{t('oauthCallback.authorizing')}</p>
          </div>
        )}
        {status === 'success' && (
          <Result
            status="success"
            title={t('oauthCallback.successTitle')}
            subTitle={message || t('oauthCallback.autoClose')}
            extra={<Button onClick={handleClose}>{t('oauthCallback.closeWindow')}</Button>}
          />
        )}
        {status === 'error' && (
          <Result
            status="error"
            title={t('oauthCallback.failedTitle')}
            subTitle={message}
            extra={<Button onClick={handleClose}>{t('oauthCallback.closeWindow')}</Button>}
          />
        )}
      </div>
    </div>
  )
}

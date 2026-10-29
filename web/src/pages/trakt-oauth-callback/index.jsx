/**
 * Trakt OAuth 回调页面
 *
 * CF Worker 授权完成后 redirect 到此页面，URL 参数中携带 token 信息。
 * 页面提取参数，调后端 API 保存 OAuth 凭据，然后通知父窗口刷新状态。
 */
import { useEffect, useRef, useState } from 'react'
import { Spin, Result, Button } from 'antd'
import { useTranslation } from 'react-i18next'
import { exchangeOAuth } from '../../apis'

export default function TraktOAuthCallback() {
  const { t } = useTranslation()
  const [status, setStatus] = useState('loading') // loading | success | error
  const [message, setMessage] = useState('')
  const exchangeStarted = useRef(false)

  useEffect(() => {
    // 防止 StrictMode 重复消费 state 并交换凭据。
    if (exchangeStarted.current) return
    exchangeStarted.current = true
    const storageKey = 'oauth:trakt:state'
    const url = new URL(window.location.href)
    // Worker 回跳时用 "?token=..." 直接拼在已含 "?state=..." 的回调地址后，
    // 浏览器会把 state 解析成 "<JWT>?token=<JWT>"，导致后端 JWT 校验失败；
    // JWT 本身不含 "?"，这里截掉第一个 "?" 之后的内容还原真实 state。
    const rawState = url.searchParams.get('state')
    const state = (rawState ? rawState.split('?')[0] : '') || sessionStorage.getItem(storageKey)
    sessionStorage.removeItem(storageKey)
    // CF Worker 回调时会在 URL 参数中携带这些信息
    const accessToken = url.searchParams.get('access_token')
    const user = url.searchParams.get('user')
    const name = url.searchParams.get('name')
    const provider = url.searchParams.get('provider')
    const clientId = url.searchParams.get('client_id')
    // refresh_token 与 expires_in 用于后端落库 + 后续自动刷新（token 过期前续期）
    const refreshToken = url.searchParams.get('refresh_token')
    const expiresIn = url.searchParams.get('expires_in')

    if (!accessToken) {
      setStatus('error')
      setMessage(t('traktOAuth.tokenMissing'))
      return
    }

    if (provider !== 'trakt') {
      setStatus('error')
      setMessage(t('traktOAuth.wrongProvider'))
      return
    }

    if (!state) {
      setStatus('error')
      setMessage(t('bgmOAuth.stateMissing'))
      return
    }

    // 通用交换接口校验 state 后保存 Worker 载荷。
    exchangeOAuth('trakt', {
      state,
      redirect_uri: `${window.location.origin}/trakt-oauth-callback`,
      accessToken,
      userId: user || '',
      username: name || '',
      clientId: clientId || '',
      refreshToken: refreshToken || '',
      expiresIn: expiresIn ? Number(expiresIn) : undefined,
    })
      .then(res => {
        const data = res.data
        if (data.success) {
          setStatus('success')
          setMessage(t('traktOAuth.authSuccess'))
          // 通知父窗口
          try {
            if (window.opener) {
              window.opener.postMessage('TRAKT-OAUTH-COMPLETE', window.location.origin)
              setTimeout(() => window.close(), 1500)
            }
          } catch (e) {
            console.error('Failed to notify parent:', e)
          }
        } else {
          setStatus('error')
          setMessage(data.message || t('traktOAuth.authFailed'))
        }
      })
      .catch(err => {
        setStatus('error')
        setMessage(err.message || t('traktOAuth.requestFailed'))
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
      background: 'linear-gradient(135deg, #ed4245 0%, #c74b2a 100%)',
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
            <p style={{ marginTop: 16, color: '#666' }}>{t('traktOAuth.saving')}</p>
          </div>
        )}
        {status === 'success' && (
          <Result
            status="success"
            title={t('traktOAuth.successTitle')}
            subTitle={message || t('traktOAuth.autoClose')}
            extra={<Button onClick={handleClose}>{t('traktOAuth.closeWindow')}</Button>}
          />
        )}
        {status === 'error' && (
          <Result
            status="error"
            title={t('traktOAuth.failedTitle')}
            subTitle={message}
            extra={<Button onClick={handleClose}>{t('traktOAuth.closeWindow')}</Button>}
          />
        )}
      </div>
    </div>
  )
}

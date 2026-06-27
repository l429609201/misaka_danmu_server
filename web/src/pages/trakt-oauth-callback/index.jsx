/**
 * Trakt OAuth 回调页面
 *
 * CF Worker 授权完成后 redirect 到此页面，URL 参数中携带 token 信息。
 * 页面提取参数，调后端 API 保存 OAuth 凭据，然后通知父窗口刷新状态。
 */
import { useEffect, useState } from 'react'
import { Spin, Result, Button } from 'antd'
import { useTranslation } from 'react-i18next'
import Cookies from 'js-cookie'
import api from '../../apis/fetch'

export default function TraktOAuthCallback() {
  const { t } = useTranslation()
  const [status, setStatus] = useState('loading') // loading | success | error
  const [message, setMessage] = useState('')

  useEffect(() => {
    const url = new URL(window.location.href)
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

    const token = Cookies.get('danmu_token')

    // 调后端 API 保存 Trakt OAuth 凭据（含 client_id 用于后续 API 调用，refresh_token/expires_in 用于自动刷新）
    api.post('/api/ui/metadata/trakt/actions/save_oauth', {
      accessToken,
      userId: user || '',
      username: name || '',
      clientId: clientId || '',
      refreshToken: refreshToken || '',
      expiresIn: expiresIn ? Number(expiresIn) : undefined,
    }, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(res => {
        const data = res.data
        if (data.success) {
          setStatus('success')
          setMessage(t('traktOAuth.authSuccess'))
          // 通知父窗口
          try {
            if (window.opener) {
              window.opener.postMessage('TRAKT-OAUTH-COMPLETE', '*')
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

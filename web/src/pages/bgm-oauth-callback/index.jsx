/**
 * Bangumi OAuth 回调页面
 * 
 * 采用 ani-rss 模式：bgm.tv 授权后重定向到此前端页面，
 * 页面从 URL 提取 code，调后端 API 完成 token 交换，
 * 然后通知父窗口刷新授权状态。
 */
import { useEffect, useState } from 'react'
import { Spin, Result, Button } from 'antd'
import Cookies from 'js-cookie'
import api from '../../apis/fetch'

export default function BgmOAuthCallback() {
  const [status, setStatus] = useState('loading') // loading | success | error
  const [message, setMessage] = useState('')

  useEffect(() => {
    const url = new URL(window.location.href)
    const code = url.searchParams.get('code')
    const state = url.searchParams.get('state')

    if (!code) {
      setStatus('error')
      setMessage('授权码为空，请重新授权')
      return
    }

    if (!state) {
      setStatus('error')
      setMessage('state 参数缺失，请重新授权')
      return
    }

    // redirect_uri 必须和授权请求时一致（当前页面的 origin + /bgm-oauth-callback）
    const redirectUri = `${window.location.origin}/bgm-oauth-callback`

    const token = Cookies.get('danmu_token')

    api.post('/api/bangumi/auth/exchange_code', {
      code,
      state,
      redirect_uri: redirectUri,
    }, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(res => {
        const data = res.data
        if (data.success) {
          setStatus('success')
          setMessage('Bangumi 授权成功')
          // 通知父窗口
          try {
            if (window.opener) {
              window.opener.postMessage('BANGUMI-OAUTH-COMPLETE', '*')
              setTimeout(() => window.close(), 1500)
            }
          } catch (e) {
            console.error('Failed to notify parent:', e)
          }
        } else {
          setStatus('error')
          setMessage(data.message || '授权失败')
        }
      })
      .catch(err => {
        setStatus('error')
        setMessage(err.message || '请求失败，请重试')
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
            <p style={{ marginTop: 16, color: '#666' }}>正在完成授权...</p>
          </div>
        )}
        {status === 'success' && (
          <Result
            status="success"
            title="授权成功"
            subTitle={message || '窗口将自动关闭...'}
            extra={<Button onClick={handleClose}>关闭窗口</Button>}
          />
        )}
        {status === 'error' && (
          <Result
            status="error"
            title="授权失败"
            subTitle={message}
            extra={<Button onClick={handleClose}>关闭窗口</Button>}
          />
        )}
      </div>
    </div>
  )
}


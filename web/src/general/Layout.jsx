import { ErrorBoundary } from 'react-error-boundary'
import { ErrorFallback } from '../components/ErrorFallback.jsx'
import { Outlet } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { Header } from './Header.jsx'
import { useAtomValue, useSetAtom } from 'jotai'
import { isMobileAtom, userinfoAtom } from '../../store/index.js'
import { getUserInfo, autoLogin } from '../apis/index.js'
import classNames from 'classnames'
import Cookies from 'js-cookie'

export const Layout = () => {
  const setUserinfo = useSetAtom(userinfoAtom)
  const isMobile = useAtomValue(isMobileAtom)
  const [isAuthenticating, setIsAuthenticating] = useState(true)

  useEffect(() => {
    const token = Cookies.get('danmu_token')

    // 如果没有 token，尝试白名单自动登录
    if (!token) {
      autoLogin()
        .then(res => {
          // 自动登录成功，保存 token
          const { accessToken, expiresIn } = res.data
          const expiresInDays = expiresIn / (60 * 24)
          Cookies.set('danmu_token', accessToken, {
            expires: expiresInDays,
            path: '/',
            secure: location.protocol === 'https:',
            sameSite: 'lax'
          })
          // 获取用户信息
          return getUserInfo()
        })
        .then(res => {
          if (res && res.data && res.data.username) {
            setUserinfo(res.data)
            setIsAuthenticating(false)
          } else {
            window.location.href = '/login'
          }
        })
        .catch(err => {
          // 自动登录失败（不在白名单中），跳转登录页
          console.log('自动登录失败，跳转登录页')
          window.location.href = '/login'
        })
    } else {
      // 已有 token，直接获取用户信息
      getUserInfo()
        .then(res => {
          if (!res.data || !res.data.username) {
            Cookies.remove('danmu_token', { path: '/' })
            window.location.href = '/login'
          } else {
            setUserinfo(res.data)
            setIsAuthenticating(false)
          }
        })
        .catch(err => {
          // API 返回 401 或其他错误，跳转登录页
          // fetch.js 的拦截器会自动处理 401 并跳转
          console.error('获取用户信息失败:', err)
        })
    }
  }, [])

  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      {isAuthenticating ? (
        // 认证中显示加载状态
        <div className="flex items-center justify-center min-h-screen">
          <div className="text-center">
            <p className="text-base-text text-lg">正在加载...</p>
          </div>
        </div>
      ) : (
        <>
          <Header />
          <div
            className={classNames({
              'w-full min-h-screen px-4 pb-22 pt-14': isMobile,
              'max-w-[1200px] min-h-screen mx-auto pt-18 pb-10 px-8': !isMobile,
            })}
          >
            <Outlet />
          </div>
        </>
      )}
    </ErrorBoundary>
  )
}

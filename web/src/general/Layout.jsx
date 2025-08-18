import { ErrorBoundary } from 'react-error-boundary'
import { ErrorFallback } from '../components/ErrorFallback.jsx'
import { Outlet } from 'react-router-dom'
import { useEffect } from 'react'
import { getStorage } from '../utils/localstroage.js'
import { DANMU_API_TOKEN_KEY } from '../configs/index.js'
import { Header } from './Header.jsx'
import { useAtomValue, useSetAtom } from 'jotai'
import { isMobileAtom, userinfoAtom } from '../../store/index.js'
import { getUserInfo } from '../apis/index.js'
import classNames from 'classnames'

export const Layout = () => {
  const setUserinfo = useSetAtom(userinfoAtom)
  const isMobile = useAtomValue(isMobileAtom)
  useEffect(() => {
    const token = getStorage(DANMU_API_TOKEN_KEY)
    if (!token) {
      window.location.href = '/login'
    } else {
      getUserInfo()
        .then(res => {
          if (!res.data || !res.data.username) {
            window.location.href = '/login'
          } else {
            setUserinfo(res.data)
          }
        })
        .catch(err => {
          window.location.href = '/login'
        })
    }
  }, [])

  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      <Header />
      <div
        className={classNames({
          'w-full min-h-screen px-4 pb-20 pt-10': isMobile,
          'max-w-[1200px] min-h-screen mx-auto pt-20 px-8': !isMobile,
        })}
      >
        <Outlet />
      </div>
    </ErrorBoundary>
  )
}

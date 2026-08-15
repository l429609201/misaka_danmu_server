import { ErrorBoundary } from 'react-error-boundary'
import { ErrorFallback } from '../components/ErrorFallback.jsx'
import { Outlet } from 'react-router-dom'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store/index.js'
import classNames from 'classnames'

export const LayoutLogin = () => {
  const isMobile = useAtomValue(isMobileAtom)

  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      {/* why：原来的 bg-base-bg 是全屏实色背景，壁纸/玻璃主题下会把壁纸整块盖成白底。
          主布局 Layout.jsx 的内容容器就没有背景类（页面底色由 body 按主题提供），
          登录布局保持一致，去掉 bg-base-bg，让壁纸能透出来 */}
      <div
        className={classNames('min-h-screen', {
          'w-full px-4 pb-20 pt-8': isMobile,
          'max-w-[1200px] mx-auto pt-20 px-8': !isMobile,
        })}
      >
        <Outlet />
      </div>
    </ErrorBoundary>
  )
}

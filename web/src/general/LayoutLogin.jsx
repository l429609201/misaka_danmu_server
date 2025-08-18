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
      <div
        className={classNames({
          'w-full min-h-screen px-4 pb-20 pt-8': isMobile,
          'max-w-[1200px] min-h-screen mx-auto pt-20 px-8': !isMobile,
        })}
      >
        <Outlet />
      </div>
    </ErrorBoundary>
  )
}

import { ErrorBoundary } from 'react-error-boundary'
import { ErrorFallback } from '../components/ErrorFallback.jsx'
import DarkModeToggle from '../components/DarkModeToggle.jsx'
import { Outlet } from 'react-router-dom'

export const Layout = () => {
  return (
    <ErrorBoundary FallbackComponent={ErrorFallback}>
      <div className="bg-base-bg">
        <DarkModeToggle />
        <Outlet />
      </div>
    </ErrorBoundary>
  )
}

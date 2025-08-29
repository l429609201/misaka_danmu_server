import { RouterProvider } from 'react-router-dom'
import { router } from './general/Router.jsx'
import { ThemeProvider } from './ThemeProvider.jsx'
import { App as AppAntd } from 'antd'

export const App = () => (
  <ThemeProvider>
    <AppAntd>
      <RouterProvider router={router} />
    </AppAntd>
  </ThemeProvider>
)

import { RouterProvider } from 'react-router-dom'
import { router } from './general/Router.jsx'
import { ThemeProvider } from './ThemeProvider.jsx'

export const App = () => (
  <ThemeProvider>
    <RouterProvider router={router} />
  </ThemeProvider>
)

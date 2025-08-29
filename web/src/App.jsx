import { RouterProvider } from 'react-router-dom'
import { router } from './general/Router.jsx'
import { ThemeProvider } from './ThemeProvider.jsx'
import { App as AppAntd } from 'antd'
import { MessageProvider } from './MessageContext.jsx'
import { ModalProvider } from './ModalContext.jsx'

export const App = () => (
  <ThemeProvider>
    <AppAntd>
      <MessageProvider>
        <ModalProvider>
          <RouterProvider router={router} />
        </ModalProvider>
      </MessageProvider>
    </AppAntd>
  </ThemeProvider>
)

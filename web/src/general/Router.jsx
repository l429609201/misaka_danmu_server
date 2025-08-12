import { createBrowserRouter } from 'react-router-dom'

import { RoutePaths } from './RoutePaths.jsx'
import { NotFound } from './NotFound.jsx'
import { Layout } from './Layout.jsx'

import { Home } from '@/pages/home'
import { Login } from '@/pages/login'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      {
        index: true,
        element: <Home />,
      },
      {
        path: RoutePaths.LOGIN,
        element: <Login />,
      },
    ],
  },
  {
    path: '*',
    element: <NotFound></NotFound>,
  },
])

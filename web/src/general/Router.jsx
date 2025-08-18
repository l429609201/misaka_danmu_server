import { createBrowserRouter } from 'react-router-dom'

import { RoutePaths } from './RoutePaths.jsx'
import { NotFound } from './NotFound.jsx'
import { Layout } from './Layout.jsx'
import { LayoutLogin } from './LayoutLogin.jsx'

import { Home } from '@/pages/home'
import { Login } from '@/pages/login'
import { Task } from '@/pages/task'
import { Library } from '../pages/library/index.jsx'
import { Setting } from '../pages/setting/index.jsx'
import { Source } from '../pages/source/index.jsx'
import { Tokens } from '../pages/tokens/index.jsx'
import { AnimeDetail } from '../pages/anime/[id].jsx'
import { EpisodeDetail } from '../pages/episode/[id].jsx'
import { CommentDetail } from '../pages/comment/[id].jsx'

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
        path: RoutePaths.TASK,
        element: <Task />,
      },
      {
        path: RoutePaths.TOKEN,
        element: <Tokens />,
      },
      {
        path: RoutePaths.LIBRARY,
        element: <Library />,
      },
      {
        path: RoutePaths.SETTING,
        element: <Setting />,
      },
      {
        path: RoutePaths.SOURCE,
        element: <Source />,
      },
      {
        path: 'anime/:id',
        element: <AnimeDetail />,
      },
      {
        path: 'episode/:id',
        element: <EpisodeDetail />,
      },
      {
        path: 'comment/:id',
        element: <CommentDetail />,
      },
    ],
  },
  {
    path: RoutePaths.LOGIN,
    element: <LayoutLogin />,
    children: [
      {
        index: true,
        element: <Login />,
      },
    ],
  },
  {
    path: '*',
    element: <NotFound></NotFound>,
  },
])

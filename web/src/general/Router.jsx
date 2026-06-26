import { createBrowserRouter } from 'react-router-dom'
import { RoutePaths } from './RoutePaths.jsx'
import { RouterScrollBehavior } from './RouterScrollBehavior.jsx'
import { NotFound } from './NotFound.jsx'
import { Layout } from './Layout.jsx'
import { LayoutLogin } from './LayoutLogin.jsx'

import { Home } from '@/pages/home'
import { Login } from '@/pages/login'
import { Task } from '@/pages/task'
import { LibraryTabsPage } from '../pages/library/tabs.jsx'
import { Setting } from '../pages/setting/index.jsx'
import { Source } from '../pages/source/index.jsx'
import { AnimeDetail } from '../pages/anime/[id].jsx'
import { EpisodeDetail } from '../pages/episode/[id].jsx'
import { CommentDetail } from '../pages/comment/[id].jsx'
import { Control } from '../pages/control/index.jsx'
import { Bullet } from '../pages/bullet/index.jsx'
import MediaFetch from '../pages/media-fetch/index.jsx'
import BgmOAuthCallback from '../pages/bgm-oauth-callback/index.jsx'
import TraktOAuthCallback from '../pages/trakt-oauth-callback/index.jsx'

export const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <RouterScrollBehavior>
        <Layout />
      </RouterScrollBehavior>
    ),
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
        path: RoutePaths.BULLET,
        element: <Bullet />,
      },
      {
        path: RoutePaths.MEDIA_FETCH,
        element: <MediaFetch />,
      },
      {
        path: RoutePaths.LIBRARY,
        element: <LibraryTabsPage />,
      },
      {
        path: RoutePaths.BATCH_MANAGE,
        element: <LibraryTabsPage />,
      },
      {
        path: RoutePaths.SUBSCRIPTIONS,
        element: <LibraryTabsPage />,
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
        path: RoutePaths.CONTROL,
        element: <Control />,
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
    // Bangumi OAuth 回调页面（弹窗中打开，不需要 Layout）
    path: RoutePaths.BGM_OAUTH_CALLBACK,
    element: <BgmOAuthCallback />,
  },
  {
    // Trakt OAuth 回调页面（弹窗中打开，不需要 Layout）
    path: RoutePaths.TRAKT_OAUTH_CALLBACK,
    element: <TraktOAuthCallback />,
  },
  {
    path: '*',
    element: <NotFound></NotFound>,
  },
])

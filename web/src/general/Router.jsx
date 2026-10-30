import { createBrowserRouter } from 'react-router-dom'
import { RoutePaths } from './RoutePaths.jsx'
import { RouterScrollBehavior } from './RouterScrollBehavior.jsx'
import { NotFound } from './NotFound.jsx'
import { Layout } from './Layout.jsx'
import { LayoutLogin } from './LayoutLogin.jsx'

import { Home } from '@/pages/home'
import { Login } from '@/pages/login'
import { Search } from '@/pages/search'
import { ApiTest } from '@/pages/api-test'
import { Task } from '@/pages/task'
import { LibraryTabsPage } from '../pages/library/tabs.jsx'
import { Setting } from '../pages/setting/index.jsx'
import { Source } from '../pages/source/index.jsx'
import { AnimeDetail } from '../pages/anime/[id].jsx'
import { EpisodeDetail } from '../pages/episode/[id].jsx'
import { CommentDetail } from '../pages/comment/[id].jsx'
import { Control } from '../pages/control/index.jsx'
import { AIPage } from '../pages/ai/index.jsx'
import { Bullet } from '../pages/bullet/index.jsx'
import MediaFetch from '../pages/media-fetch/index.jsx'
import OAuthCallback from '../pages/oauth-callback/index.jsx'

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
        path: RoutePaths.SEARCH,
        element: <Search />,
      },
      {
        path: RoutePaths.API_TEST,
        element: <ApiTest />,
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
        // AI 独立一级页面（原 设置 → AI）
        path: RoutePaths.AI,
        element: <AIPage />,
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
    // 通用 OAuth 回调页面（弹窗中打开，不需要 Layout），平台由路径参数决定
    path: RoutePaths.OAUTH_CALLBACK,
    element: <OAuthCallback />,
  },
  {
    // 兼容旧回调地址：Bangumi 开发者后台已登记该地址，改动需用户同步修改平台配置
    path: RoutePaths.BGM_OAUTH_CALLBACK,
    element: <OAuthCallback provider="bangumi" />,
  },
  {
    // 兼容旧回调地址：保留已有 Trakt Worker 回跳路径
    path: RoutePaths.TRAKT_OAUTH_CALLBACK,
    element: <OAuthCallback provider="trakt" />,
  },
  {
    path: '*',
    element: <NotFound></NotFound>,
  },
])

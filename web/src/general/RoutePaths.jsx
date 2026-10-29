export const RoutePaths = {
  HOME: '/',
  SEARCH: '/search',
  API_TEST: '/api-test',
  LOGIN: '/login',
  LIBRARY: '/library',
  TASK: '/task',
  BULLET: '/bullet',
  MEDIA_FETCH: '/media-fetch',
  SOURCE: '/source',
  SETTING: '/setting',
  CONTROL: '/control',
  BGM_OAUTH_CALLBACK: '/bgm-oauth-callback',
  TRAKT_OAUTH_CALLBACK: '/trakt-oauth-callback',
  // 通用 OAuth 回调：新接入来源统一使用 /oauth/callback/{provider}
  OAUTH_CALLBACK: '/oauth/callback/:provider',
  BATCH_MANAGE: '/library/batch-manage',
  SUBSCRIPTIONS: '/library/subscriptions',
}

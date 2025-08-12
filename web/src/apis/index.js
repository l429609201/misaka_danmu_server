import api from './fetch'

/** 登录 */
export const login = data =>
  api.post('/api/ui/auth/token', data, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

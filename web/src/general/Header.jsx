import { useEffect, useMemo, useState } from 'react'
import { RoutePaths } from './RoutePaths.jsx'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAtom, useAtomValue } from 'jotai'
import { isMobileAtom, userinfoAtom } from '../../store/index.js'
import DarkModeToggle from '@/components/DarkModeToggle.jsx'
import { MyIcon } from '@/components/MyIcon.jsx'
import classNames from 'classnames'
import { Dropdown } from 'antd'
import { logout } from '../apis/index.js'
import { clearStorage } from '../utils/localstroage.js'
import { DANMU_API_TOKEN_KEY } from '../configs/index.js'

const navItems = [
  { key: RoutePaths.HOME, label: '首页', icon: 'home' },
  { key: RoutePaths.LIBRARY, label: '弹幕库', icon: 'danmukaiqi' },
  { key: RoutePaths.TASK, label: '任务管理器', icon: 'renwu' },
  { key: RoutePaths.TOKEN, label: '弹幕token', icon: 'key' },
  { key: RoutePaths.SOURCE, label: '搜索源', icon: 'yuan' },
  { key: RoutePaths.SETTING, label: '设置', icon: 'setting' },
]

export const Header = () => {
  const [isMobile, setIsMobile] = useAtom(isMobileAtom)
  const location = useLocation()
  console.log(location)

  const activeKey = useMemo(() => {
    if (location.pathname === '/') return RoutePaths.HOME
    return (
      navItems.filter(item => {
        return location.pathname?.includes(item.key) && item.key !== '/'
      })?.[0]?.key || RoutePaths.HOME
    )
  }, [location, navItems])

  useEffect(() => {
    const checkScreenSize = () => {
      setIsMobile(window.innerWidth <= 768)
    }
    checkScreenSize()
    window.addEventListener('resize', checkScreenSize)
    return () => {
      window.removeEventListener('resize', checkScreenSize)
    }
  }, [])

  return (
    <>
      {isMobile ? (
        <>
          <div className="fixed top-0 left-0 w-full z-50 py-2 bg-base-bg">
            <div className="flex justify-end px-2">
              <DarkModeToggle />
            </div>
          </div>
          <MobileHeader activeKey={activeKey} />
        </>
      ) : (
        <DesktopHeader activeKey={activeKey} />
      )}
    </>
  )
}

const MobileHeader = ({ activeKey }) => {
  const mobileNavItems = [
    ...navItems.slice(0, 3),
    { key: 'user', label: '我的', icon: 'user', children: navItems.slice(3) },
  ]
  const navigate = useNavigate()

  const onLogout = async () => {
    await logout()
    clearStorage(DANMU_API_TOKEN_KEY)
    navigate(RoutePaths.LOGIN)
  }

  return (
    <div className="fixed bottom-0 left-0 w-full shadow-box z-50 py-2 overflow-hidden bg-base-bg">
      <div className="flex justify-around items-center">
        {mobileNavItems.map(it => (
          <>
            {!it.children?.length ? (
              <div
                key={it.key}
                className={classNames(
                  'text-center',
                  it.key === activeKey && 'text-primary'
                )}
                onClick={() => {
                  navigate(it.key)
                }}
              >
                <div>
                  <MyIcon icon={it.icon} size={32} />
                </div>
                <div>{it.label}</div>
              </div>
            ) : (
              <Dropdown
                menu={{
                  items: [
                    ...it.children.map(o => ({
                      key: o.key,
                      label: (
                        <div
                          key={o.key}
                          className="flex items-center justify-start text-nowrap gap-2 py-2"
                          onClick={() => {
                            navigate(o.key)
                          }}
                        >
                          <MyIcon icon={o.icon} size={24} />
                          <div className="text-base">{o.label}</div>
                        </div>
                      ),
                    })),
                    {
                      key: 'logout',
                      label: (
                        <div
                          key="logout"
                          className="flex items-center justify-start text-nowrap gap-2 py-2"
                          onClick={onLogout}
                        >
                          <MyIcon icon="user" size={24} />
                          <div className="text-base">退出登录</div>
                        </div>
                      ),
                    },
                  ],
                }}
                key={it.key}
                placement="topLeft"
                trigger={['click']}
              >
                <div
                  className={classNames(
                    'text-center',
                    it.children.map(o => o.key).includes(activeKey) &&
                      'text-primary'
                  )}
                >
                  <div>
                    <MyIcon icon={it.icon} size={32} />
                  </div>
                  <div>{it.label}</div>
                </div>
              </Dropdown>
            )}
          </>
        ))}
      </div>
    </div>
  )
}

const DesktopHeader = ({ activeKey }) => {
  const navigate = useNavigate()
  const userinfo = useAtomValue(userinfoAtom)
  return (
    <div className="fixed top-0 left-0 w-full shadow-box z-50 py-2 bg-base-bg">
      <div className="flex justify-between items-center max-w-[1200px] mx-auto w-full px-6">
        <div onClick={() => navigate(RoutePaths.HOME)}>
          <img src="/images/logo.png" className="h-12 cursor-pointer" />
        </div>
        <div className="flex items-center justify-center">
          {navItems.map(it => (
            <div
              key={it.key}
              className={classNames(
                'text-lg font-semibold cursor-pointer mx-4',
                {
                  'text-primary': activeKey === it.key,
                }
              )}
              onClick={() => navigate(it.key)}
            >
              {it.label}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-center gap-6">
          <Dropdown
            menu={{
              items: [
                {
                  key: 'logout',
                  label: '退出',
                },
              ],
            }}
          >
            <div className="text-primary font-medium cursor-pointer">
              {userinfo?.username}
            </div>
          </Dropdown>
          <DarkModeToggle />
        </div>
      </div>
    </div>
  )
}

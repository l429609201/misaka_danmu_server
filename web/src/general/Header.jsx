import { useEffect, useMemo, useState } from 'react'
import { RoutePaths } from './RoutePaths.jsx'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAtom, useAtomValue } from 'jotai'
import { isMobileAtom, userinfoAtom } from '../../store/index.js'
import DarkModeToggle from '@/components/DarkModeToggle.jsx';
import { MyIcon } from '@/components/MyIcon'
import classNames from 'classnames'
import { Tag, Dropdown, Modal, Form, Input, Button, Space, Badge, Popconfirm } from 'antd';
import { logout, changePassword, checkAppUpdate, getDockerStatus, restartService } from '../apis/index.js'
import Cookies from 'js-cookie'
import { EyeInvisibleOutlined, EyeOutlined, LockOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import SessionManager from '@/components/SessionManager'
import VersionModal from '@/components/VersionModal'
import ThemeColorPicker from '@/components/ThemeColorPicker'
import RealtimeLogModal from '@/components/RealtimeLogModal'
import HistoryLogModal from '@/components/HistoryLogModal'

// 实时日志图标 (Lucide - scroll-text)
const RealtimeLogIcon = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M15 12h-5"/>
    <path d="M15 8h-5"/>
    <path d="M19 17V5a2 2 0 0 0-2-2H4"/>
    <path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2"/>
  </svg>
)

// 历史日志图标 (Lucide - archive)
const HistoryLogIcon = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect width="20" height="5" x="2" y="3" rx="1"/>
    <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>
    <path d="M10 12h4"/>
  </svg>
)

// GitHub 图标 (Simple Icons 标准)
const GithubIcon = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor">
    <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
  </svg>
)

// Telegram 图标 (Simple Icons 标准)
const TelegramIcon = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor">
    <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z"/>
  </svg>
)

// 文档图标 (Lucide - book-open)
const DocsIcon = () => (
  <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
    <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
  </svg>
)
import { useMessage } from '../MessageContext'
import {
  useFloating,
  autoUpdate,
  offset,
  flip,
  shift,
  useInteractions,
  useClick,
  useDismiss,
  FloatingPortal,
} from '@floating-ui/react'

const navItems = [
  { key: RoutePaths.HOME, label: '首页', icon: 'home' },
  { key: RoutePaths.LIBRARY, label: '弹幕库', icon: 'tvlibrary' },
  { key: RoutePaths.TASK, label: '任务管理器', icon: 'renwu' },
  { key: RoutePaths.BULLET, label: '弹幕', icon: 'danmu' },
  { key: RoutePaths.MEDIA_FETCH, label: '媒体获取', icon: 'movie' },
  { key: RoutePaths.SOURCE, label: '搜索源', icon: 'search' },
  { key: RoutePaths.CONTROL, label: '外部控制', icon: 'controlapi' },
  { key: RoutePaths.SETTING, label: '设置', icon: 'setting' },
]
import { getVersion } from '../apis/index.js';

const FloatingMenu = ({ trigger, items, onItemClick, activeKey }) => {
  const [isOpen, setIsOpen] = useState(false)

  const { refs, floatingStyles, context } = useFloating({
    open: isOpen,
    onOpenChange: setIsOpen,
    placement: 'top', // 强制向上展开
    middleware: [
      offset(8),
      shift({ padding: 8 }),
    ],
    whileElementsMounted: autoUpdate,
  })

  const click = useClick(context)
  const dismiss = useDismiss(context)
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss])

  return (
    <>
      <div
        ref={refs.setReference}
        {...getReferenceProps()}
        className="flex-1"
      >
        {trigger}
      </div>
      <FloatingPortal>
        {isOpen && (
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className="z-[1000]"
          >
            <div className="space-y-2 bg-base-card backdrop-blur-sm rounded-lg shadow-xl border border-gray-200/50 dark:border-gray-800/30 p-2">
              {items.map((item, index) => (
                <button
                  key={item.key}
                  onClick={() => {
                    onItemClick?.(item)
                    setIsOpen(false)
                  }}
                  className={classNames(
                    'block w-full px-4 py-2 rounded-md transition-all duration-200 text-sm font-medium text-left',
                    activeKey === item.key
                      ? 'bg-primary text-white shadow-sm'
                      : 'text-base-text hover:bg-base-hover'
                  )}
                  style={{
                    animationDelay: `${index * 50}ms`
                  }}
                >
                  <div className="flex items-center justify-start gap-2">
                    <MyIcon icon={item.icon} size={20} />
                    <div>{item.label}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </FloatingPortal>
    </>
  )
}

export const Header = () => {
  const [isMobile, setIsMobile] = useAtom(isMobileAtom)
  const location = useLocation()
  const navigate = useNavigate()
  const [version, setVersion] = useState('N/A');
  const [docsUrl, setDocsUrl] = useState('');
  const [hasUpdate, setHasUpdate] = useState(false);
  const [versionModalOpen, setVersionModalOpen] = useState(false);
  const [realtimeLogOpen, setRealtimeLogOpen] = useState(false);
  const [historyLogOpen, setHistoryLogOpen] = useState(false);
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
    const fetchVersion = async () => {
      const res = await getVersion();
      setVersion(`v${res.data.version}`);
      setDocsUrl(res.data.docsUrl || '');
    };
    fetchVersion();

    // 检查更新
    const checkUpdate = async () => {
      try {
        const res = await checkAppUpdate();
        setHasUpdate(res.data?.hasUpdate || false);
      } catch (e) {
        console.error('检查更新失败:', e);
      }
    };
    checkUpdate();
  }, []);
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
            <div className="flex justify-start items-center px-4 md:px-8">
              <div onClick={() => navigate(RoutePaths.HOME)}>
                <img src="/images/logo.png" className="h-12 cursor-pointer" />
              </div>
              <div className="flex items-center justify-center gap-3 ml-auto">
                <Tooltip title="实时日志">
                  <div onClick={() => setRealtimeLogOpen(true)} className="cursor-pointer" style={{ color: '#1890ff', fontSize: 18 }}>
                    <RealtimeLogIcon />
                  </div>
                </Tooltip>
                <Tooltip title="历史日志">
                  <div onClick={() => setHistoryLogOpen(true)} className="cursor-pointer" style={{ color: '#8B7355', fontSize: 18 }}>
                    <HistoryLogIcon />
                  </div>
                </Tooltip>
                <Tooltip title="GitHub">
                  <a
                    href="https://github.com/l429609201/misaka_danmu_server"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: '#333', fontSize: 18 }}
                  >
                    <GithubIcon />
                  </a>
                </Tooltip>
                <Tooltip title="Telegram">
                  <a
                    href="https://t.me/misaka_danmaku"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: '#0088cc', fontSize: 18 }}
                  >
                    <TelegramIcon />
                  </a>
                </Tooltip>
                {docsUrl && (
                  <Tooltip title="文档">
                    <a
                      href={docsUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: '#8CA1AF', fontSize: 18 }}
                    >
                      <DocsIcon />
                    </a>
                  </Tooltip>
                )}
                <Badge dot={hasUpdate} offset={[-5, 5]}>
                  <Tag
                    className="cursor-pointer"
                    onClick={() => setVersionModalOpen(true)}
                  >
                    {version}
                  </Tag>
                </Badge>
                <DarkModeToggle />
              </div>
            </div>
          </div>
          <MobileHeader activeKey={activeKey} />
        </>
      ) : (
        <DesktopHeader
          activeKey={activeKey}
          version={version}
          docsUrl={docsUrl}
          hasUpdate={hasUpdate}
          onVersionClick={() => setVersionModalOpen(true)}
          onRealtimeLog={() => setRealtimeLogOpen(true)}
          onHistoryLog={() => setHistoryLogOpen(true)}
        />
      )}

      {/* 版本信息弹窗 */}
      <VersionModal
        open={versionModalOpen}
        onClose={() => setVersionModalOpen(false)}
        currentVersion={version}
      />

      {/* 实时日志弹窗 */}
      <RealtimeLogModal
        open={realtimeLogOpen}
        onClose={() => setRealtimeLogOpen(false)}
      />

      {/* 历史日志弹窗 */}
      <HistoryLogModal
        open={historyLogOpen}
        onClose={() => setHistoryLogOpen(false)}
      />
    </>
  )
}

const MobileHeader = ({ activeKey }) => {
  const mobileNavItems = [
    ...navItems.slice(0, 3), // 首页、弹幕库、任务管理器直接显示
    { key: 'user', label: '我的', icon: 'user', children: navItems.slice(3) }, // 其余的放在"我的"菜单下
  ]
  const navigate = useNavigate()
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false)
  const [isSessionModalOpen, setIsSessionModalOpen] = useState(false)
  const [isThemeColorOpen, setIsThemeColorOpen] = useState(false)
  const [passwordForm] = Form.useForm()
  const [passwordLoading, setPasswordLoading] = useState(false)
  const [currentPasswordVisible, setCurrentPasswordVisible] = useState(false)
  const [newPasswordVisible, setNewPasswordVisible] = useState(false)
  const [confirmPasswordVisible, setConfirmPasswordVisible] = useState(false)
  const [dockerAvailable, setDockerAvailable] = useState(false)
  const [restartLoading, setRestartLoading] = useState(false)
  const messageApi = useMessage()

  // 检查 Docker 套接字是否可用
  useEffect(() => {
    const checkDocker = async () => {
      try {
        const res = await getDockerStatus()
        // API 返回 socketAvailable 字段
        setDockerAvailable(res.data?.socketAvailable || false)
      } catch (error) {
        setDockerAvailable(false)
      }
    }
    checkDocker()
  }, [])

  const onLogout = async () => {
    await logout()
    Cookies.remove('danmu_token', { path: '/' })
    // 刷新页面清理前端状态（定时器、WebSocket、缓存等）
    window.location.href = RoutePaths.LOGIN
  }

  const handleRestart = async () => {
    try {
      setRestartLoading(true)
      await restartService()
      messageApi.success('重启指令已发送，请稍候...')
    } catch (error) {
      messageApi.error(error.response?.data?.detail || '重启失败')
    } finally {
      setRestartLoading(false)
    }
  }

  const handleChangePassword = async (values) => {
    try {
      setPasswordLoading(true)
      await changePassword(values)
      messageApi.success('密码修改成功')
      setIsPasswordModalOpen(false)
      passwordForm.resetFields()
    } catch (error) {
      // 优先从 error.response.data.detail 获取（直接来自后端）
      const detail = error.response?.data?.detail || error.detail

      let errorMsg = '密码修改失败'

      if (Array.isArray(detail)) {
        // Pydantic 422 验证错误：[{loc, msg, type}, ...]
        errorMsg = detail.map(err => err.msg || JSON.stringify(err)).join('; ')
      } else if (typeof detail === 'string') {
        // 业务逻辑错误：字符串
        errorMsg = detail
      } else if (error.message && typeof error.message === 'string') {
        // fetch.js 拦截器添加的 message 字段
        errorMsg = error.message
      }

      messageApi.error(errorMsg)
    } finally {
      setPasswordLoading(false)
    }
  }

  const handleMenuItemClick = (item) => {
    if (item.key === 'logout') {
      onLogout()
    } else if (item.key === 'change-password') {
      setIsPasswordModalOpen(true)
    } else if (item.key === 'session-manager') {
      setIsSessionModalOpen(true)
    } else if (item.key === 'theme-color') {
      setIsThemeColorOpen(true)
    } else if (item.key === 'restart-service') {
      // 重启由 Popconfirm 处理，这里不做任何事
    } else {
      navigate(item.key)
    }
  }

  // 计算"我的"菜单的显示标签
  const userMenuItem = mobileNavItems.find(it => it.key === 'user')
  const activeChildItem = userMenuItem?.children?.find(child => child.key === activeKey)
  const userMenuLabel = activeChildItem ? activeChildItem.label : '我的'

  return (
    <>
      <div className="fixed bottom-0 left-0 w-full shadow-box z-50 py-2 overflow-hidden bg-base-bg">
        <div className="flex justify-evenly items-center">
          {mobileNavItems.map(it => (
            <>
              {!it.children?.length ? (
                <div
                  key={it.key}
                  className={classNames(
                    'text-center flex-1',
                    it.key === activeKey && 'text-primary'
                  )}
                  onClick={() => {
                    navigate(it.key)
                  }}
                >
                  <div>
                    <MyIcon icon={it.icon} size={26} />
                  </div>
                  <div className="text-xs">{it.label}</div>
                </div>
              ) : (
                <FloatingMenu
                  key={it.key}
                  trigger={
                    <div
                      className={classNames(
                        'text-center flex-1',
                        it.children.map(o => o.key).includes(activeKey) &&
                          'text-primary'
                      )}
                    >
                      <div>
                        <MyIcon icon={it.icon} size={26} />
                      </div>
                      <div className="text-xs">{userMenuLabel}</div>
                    </div>
                  }
                  items={[
                    ...it.children.map(o => ({
                      key: o.key,
                      label: o.label,
                      icon: o.icon,
                    })),
                    {
                      key: 'theme-color',
                      label: '主题色',
                      icon: 'setting',
                    },
                    {
                      key: 'session-manager',
                      label: '会话管理',
                      icon: 'checksurface',
                    },
                    {
                      key: 'change-password',
                      label: '修改密码',
                      icon: 'key',
                    },
                    ...(dockerAvailable ? [{
                      key: 'restart-service',
                      label: (
                        <Popconfirm
                          title="确认重启"
                          description="确定要重启服务吗？"
                          onConfirm={handleRestart}
                          okText="确定"
                          cancelText="取消"
                        >
                          <span>重启服务</span>
                        </Popconfirm>
                      ),
                      icon: 'refresh',
                    }] : []),
                    {
                      key: 'logout',
                      label: '退出登录',
                      icon: 'user',
                    },
                  ]}
                  onItemClick={handleMenuItemClick}
                  activeKey={activeKey}
                />
              )}
            </>
          ))}
        </div>
      </div>

      {/* 修改密码弹框 */}
      <Modal
        title="修改密码"
        open={isPasswordModalOpen}
        onCancel={() => {
          setIsPasswordModalOpen(false)
          passwordForm.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={passwordForm}
          layout="vertical"
          onFinish={handleChangePassword}
        >
          <Form.Item
            name="oldPassword"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入当前密码"
              iconRender={(visible) =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
              visibilityToggle={{
                visible: currentPasswordVisible,
                onVisibleChange: setCurrentPasswordVisible,
              }}
            />
          </Form.Item>

          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[{ required: true, message: '请输入新密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请输入新密码"
              iconRender={(visible) =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
              visibilityToggle={{
                visible: newPasswordVisible,
                onVisibleChange: setNewPasswordVisible,
              }}
            />
          </Form.Item>

          <Form.Item
            name="confirmPassword"
            label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="请再次输入新密码"
              iconRender={(visible) =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
              visibilityToggle={{
                visible: confirmPasswordVisible,
                onVisibleChange: setConfirmPasswordVisible,
              }}
            />
          </Form.Item>

          <Form.Item>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button
                onClick={() => {
                  setIsPasswordModalOpen(false)
                  passwordForm.resetFields()
                }}
              >
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={passwordLoading}>
                确认修改
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 会话管理弹窗 */}
      <SessionManager
        open={isSessionModalOpen}
        onClose={() => setIsSessionModalOpen(false)}
      />

      {/* 主题色切换弹窗 */}
      <ThemeColorPicker
        open={isThemeColorOpen}
        onClose={() => setIsThemeColorOpen(false)}
      />
    </>
  )
}

const DesktopHeader = ({ activeKey, version, docsUrl, hasUpdate, onVersionClick, onRealtimeLog, onHistoryLog }) => {
  const navigate = useNavigate()
  const userinfo = useAtomValue(userinfoAtom)
  const messageApi = useMessage()
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false)
  const [isSessionModalOpen, setIsSessionModalOpen] = useState(false)
  const [isThemeColorOpen, setIsThemeColorOpen] = useState(false)
  const [form] = Form.useForm()
  const [showPassword1, setShowPassword1] = useState(false)
  const [showPassword2, setShowPassword2] = useState(false)
  const [showPassword3, setShowPassword3] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [dockerAvailable, setDockerAvailable] = useState(false)
  const [restartLoading, setRestartLoading] = useState(false)

  // 检查 Docker 套接字是否可用
  useEffect(() => {
    const checkDocker = async () => {
      try {
        const res = await getDockerStatus()
        // API 返回 socketAvailable 字段
        setDockerAvailable(res.data?.socketAvailable || false)
      } catch (error) {
        setDockerAvailable(false)
      }
    }
    checkDocker()
  }, [])

  const onLogout = async () => {
    await logout()
    Cookies.remove('danmu_token', { path: '/' })
    // 刷新页面清理前端状态（定时器、WebSocket、缓存等）
    window.location.href = RoutePaths.LOGIN
  }

  const handleRestart = async () => {
    try {
      setRestartLoading(true)
      await restartService()
      messageApi.success('重启指令已发送，请稍候...')
    } catch (error) {
      messageApi.error(error.response?.data?.detail || '重启失败')
    } finally {
      setRestartLoading(false)
    }
  }

  const handleChangePassword = async (values) => {
    try {
      setIsLoading(true)
      await changePassword(values)
      form.resetFields()
      messageApi.success('修改成功')
      setIsPasswordModalOpen(false)
    } catch (error) {
      // 优先从 error.response.data.detail 获取（直接来自后端）
      const detail = error.response?.data?.detail || error.detail

      let errorMsg = '修改失败'

      if (Array.isArray(detail)) {
        // Pydantic 422 验证错误：[{loc, msg, type}, ...]
        errorMsg = detail.map(err => err.msg || JSON.stringify(err)).join('; ')
      } else if (typeof detail === 'string') {
        // 业务逻辑错误：字符串
        errorMsg = detail
      } else if (error.message && typeof error.message === 'string') {
        // fetch.js 拦截器添加的 message 字段
        errorMsg = error.message
      }

      messageApi.error(errorMsg)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <>
      <div className="fixed top-0 left-0 w-full shadow-box z-50 py-2 bg-base-bg">
        <div className="flex justify-start items-center max-w-[1200px] mx-auto w-full px-6 gap-4">
          <div onClick={() => navigate(RoutePaths.HOME)}>
            <img src="/images/logo.png" className="h-12 cursor-pointer" />
          </div>
          <div className="flex items-center justify-center">
            {navItems.map(it => (
              <div
                key={it.key}
                className={classNames(
                  'text-base font-semibold cursor-pointer mx-3',
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
          <div className="flex items-center justify-center gap-4 ml-auto">
            <Tooltip title="实时日志">
              <div onClick={onRealtimeLog} className="cursor-pointer" style={{ color: '#1890ff', fontSize: 20 }}>
                <RealtimeLogIcon />
              </div>
            </Tooltip>
            <Tooltip title="历史日志">
              <div onClick={onHistoryLog} className="cursor-pointer" style={{ color: '#8B7355', fontSize: 20 }}>
                <HistoryLogIcon />
              </div>
            </Tooltip>
            <Tooltip title="GitHub">
              <a
                href="https://github.com/l429609201/misaka_danmu_server"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#333', fontSize: 20 }}
              >
                <GithubIcon />
              </a>
            </Tooltip>
            <Tooltip title="Telegram">
              <a
                href="https://t.me/misaka_danmaku"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#0088cc', fontSize: 20 }}
              >
                <TelegramIcon />
              </a>
            </Tooltip>
            {docsUrl && (
              <Tooltip title="文档">
                <a
                  href={docsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: '#8CA1AF', fontSize: 20 }}
                >
                  <DocsIcon />
                </a>
              </Tooltip>
            )}
            <Badge dot={hasUpdate} offset={[-5, 5]}>
              <Tag
                className="cursor-pointer"
                onClick={onVersionClick}
              >
                {version}
              </Tag>
            </Badge>
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'themeColor',
                    label: (
                      <div onClick={() => setIsThemeColorOpen(true)} className="text-base">
                        主题色
                      </div>
                    ),
                  },
                  {
                    key: 'sessionManager',
                    label: (
                      <div onClick={() => setIsSessionModalOpen(true)} className="text-base">
                        会话管理
                      </div>
                    ),
                  },
                  {
                    key: 'changePassword',
                    label: (
                      <div onClick={() => setIsPasswordModalOpen(true)} className="text-base">
                        修改密码
                      </div>
                    ),
                  },
                  ...(dockerAvailable ? [{
                    key: 'restart',
                    label: (
                      <Popconfirm
                        title="确认重启"
                        description="确定要重启服务吗？"
                        onConfirm={handleRestart}
                        okText="确定"
                        cancelText="取消"
                      >
                        <div className="text-base">重启服务</div>
                      </Popconfirm>
                    ),
                  }] : []),
                  {
                    key: 'logout',
                    label: (
                      <div onClick={onLogout} className="text-base">
                        退出登录
                      </div>
                    ),
                  },
                ],
              }}
            >
              <div className="text-primary font-medium cursor-pointer flex items-center gap-1">
                <MyIcon icon="user" size={18} />
                {userinfo?.username}
              </div>
            </Dropdown>
            <DarkModeToggle />
          </div>
        </div>
      </div>

      <Modal
        title="修改密码"
        open={isPasswordModalOpen}
        onCancel={() => {
          setIsPasswordModalOpen(false)
          form.resetFields()
        }}
        footer={null}
        width={500}
      >
        <div className="mb-4">
          如果您是使用初始随机密码登录的，建议您在此修改为自己的密码。
        </div>
        <Form
          form={form}
          layout="vertical"
          onFinish={handleChangePassword}
        >
          <Form.Item
            name="oldPassword"
            label="当前密码"
            rules={[{ required: true, message: '请输入当前密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入当前密码"
              visibilityToggle={{
                visible: showPassword1,
                onVisibleChange: setShowPassword1,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
          <Form.Item
            name="newPassword"
            label="新密码"
            rules={[{ required: true, message: '请输入新密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入新密码"
              visibilityToggle={{
                visible: showPassword2,
                onVisibleChange: setShowPassword2,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>
          <Form.Item
            name="checkPassword"
            label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              {
                required: true,
                message: '请输入新密码',
              },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('新密码不匹配'))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder="请输入新密码"
              visibilityToggle={{
                visible: showPassword3,
                onVisibleChange: setShowPassword3,
              }}
              iconRender={visible =>
                visible ? <EyeOutlined /> : <EyeInvisibleOutlined />
              }
            />
          </Form.Item>

          <Form.Item>
            <div className="flex justify-end gap-2">
              <Button onClick={() => {
                setIsPasswordModalOpen(false)
                form.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={isLoading}>
                确认修改
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Modal>

      {/* 会话管理弹窗 */}
      <SessionManager
        open={isSessionModalOpen}
        onClose={() => setIsSessionModalOpen(false)}
      />

      {/* 主题色切换弹窗 */}
      <ThemeColorPicker
        open={isThemeColorOpen}
        onClose={() => setIsThemeColorOpen(false)}
      />
    </>
  )
}

import { useEffect, useMemo, useState, useRef, useLayoutEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { RoutePaths } from './RoutePaths.jsx'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useAtom, useAtomValue, useSetAtom } from 'jotai'
import { isMobileAtom, userinfoAtom } from '../../store/index.js'
import DarkModeToggle from '@/components/DarkModeToggle.jsx';
import { MyIcon } from '@/components/MyIcon'
import classNames from 'classnames'
import { Tag, Dropdown, Modal, Form, Input, Button, Space, Badge, Popconfirm } from 'antd';
import { logout, changePassword, checkAppUpdate, getDockerStatus, restartService, getVersion } from '../apis/index.js'
import Cookies from 'js-cookie'
import { EyeInvisibleOutlined, EyeOutlined, LockOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import SessionManager from '@/components/SessionManager'
import VersionModal from '@/components/VersionModal'
import ThemeColorPicker from '@/components/ThemeColorPicker'
import PageStylePicker from '@/components/PageStylePicker'
import LanguagePicker from '@/components/LanguagePicker'
import RealtimeLogModal from '@/components/RealtimeLogModal'
import CacheManagerModal from '@/components/CacheManagerModal'
import HistoryLogModal from '@/components/HistoryLogModal'
import { RateLimitIndicator } from '@/components/RateLimitIndicator'
import { clearBrowserCache } from '@/utils/clearCache'
import { useMessage } from '../MessageContext'
import {
  useFloating,
  autoUpdate,
  offset,
  shift,
  useInteractions,
  useClick,
  useDismiss,
  FloatingPortal,
} from '@floating-ui/react'

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

const navItems = [
  { key: RoutePaths.HOME, label: 'nav.home', icon: 'home', iconfontIcon: 'icon-home' },
  { key: RoutePaths.LIBRARY, label: 'nav.library', icon: 'tvlibrary', iconfontIcon: 'icon-tvlibrary', children: [
    { key: 'library', label: 'libraryPage.pageTitle', icon: 'kufangguanli' },
    { key: 'batch', label: 'libraryPage.btnBatchManage', icon: 'piliangguanli' },
  ] },
  { key: RoutePaths.TASK, label: 'nav.task', icon: 'renwu', iconfontIcon: 'icon-renwu', children: [
    { key: 'task', label: 'nav.taskRunning', icon: 'tongji-jinhangzhongderenwushuliang' },
    { key: 'webhook', label: 'nav.taskWebhook', icon: 'Webhookrenwu', iconSize: 28, iconClassName: 'ml-px' },
    { key: 'schedule', label: 'nav.taskSchedule', icon: 'dingshirenwu' },
    { key: 'ratelimit', label: 'nav.taskRatelimit', icon: 'liukong' },
  ]},
  { key: RoutePaths.BULLET, label: 'nav.bullet', icon: 'danmu', iconfontIcon: 'icon-danmu', children: [
    { key: 'token', label: 'nav.bulletToken', icon: 'tokenguanli' },
    { key: 'output', label: 'nav.bulletOutput', icon: 'shuchupeizhi' },
    { key: 'storage', label: 'nav.bulletStorage', icon: 'cunchupeizhi' },
    { key: 'fallback', label: 'nav.bulletFallback', icon: 'sanfangyunpeizhi' },
  ]},
  { key: RoutePaths.MEDIA_FETCH, label: 'nav.mediaFetch', icon: 'movie', iconfontIcon: 'icon-movie', children: [
    { key: 'library-scan', label: 'nav.mediaLibraryScan', icon: 'meitiduqu', iconSize: 28 , iconClassName: 'ml-px' },
    { key: 'local-scan', label: 'nav.mediaLocalScan', icon: 'bendiduqu' },
  ]},
  { key: RoutePaths.SOURCE, label: 'nav.source', icon: 'search', iconfontIcon: 'icon-search', children: [
    { key: 'scrapers', label: 'nav.sourceScrapers', icon: 'accurate-search' },
    { key: 'metadata', label: 'nav.sourceMetadata', icon: 'accurate-search-full' },
    { key: 'global-filter', label: 'nav.sourceGlobalFilter', icon: 'guolvshezhi' },
  ]},
  { key: RoutePaths.CONTROL, label: 'nav.control', icon: 'controlapi', iconfontIcon: 'icon-controlapi', children: [
    { key: 'apikey', label: 'nav.controlApikey', icon: 'API' },
    { key: 'settings', label: 'nav.controlSettings', icon: 'canshupeizhi' },
    { key: 'apilogs', label: 'nav.controlApilogs', icon: 'APIrizhi' },
    { key: 'mcp', label: 'nav.controlMcp', icon: 'MCP' },
    { key: 'apidoc', label: 'nav.controlApidoc', icon: 'kuaijierukou_apiwendang' },
  ]},
  { key: RoutePaths.SETTING, label: 'nav.setting', icon: 'setting', iconfontIcon: 'icon-setting', children: [
    { key: 'parameters', label: 'nav.settingParameters', icon: 'canshupeizhi' },
    { key: 'proxy', label: 'nav.settingProxy', icon: 'dailipeizhi' },
    { key: 'webhook', label: 'nav.settingWebhook', icon: 'webhookpeizhi' },
    { key: 'notification', label: 'nav.settingNotification', icon: 'jiaohu' },
    { key: 'recognition', label: 'nav.settingRecognition', icon: 'renlianshibie_o' },
    { key: 'automatch', label: 'nav.settingAutomatch', icon: 'ai' },
  ]},
]

// 导航图标统一样式
// icon+文字模式：字体图标按普通字符渲染（保留字体 hinting，最清晰），不强制 flex 居中
// icon-only 模式：固定整数 box 居中对齐
const navIconStyle = (size, scale, compact = false) => {
  if (compact) {
    return {
      fontSize: size,
      lineHeight: 1,
      width: size + 2,
      height: size + 2,
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      ...(scale ? { transform: `scale(${scale})` } : {}),
    }
  }
  // 非 compact：按字符自然渲染，避免 inline-flex 居中导致字形亚像素模糊
  return {
    fontSize: size,
    lineHeight: 1,
    display: 'inline-block',
    verticalAlign: 'middle',
  }
}

const getChildNavigatePath = (parentItem, childKey) => {
  if (parentItem?.key === RoutePaths.LIBRARY) {
    return childKey === 'batch' ? RoutePaths.BATCH_MANAGE : RoutePaths.LIBRARY
  }
  return `${parentItem.key}?key=${childKey}`
}

const getNavChildActiveKey = (parentItem, location, subKey) => {
  if (parentItem?.key === RoutePaths.LIBRARY) {
    return location.pathname === RoutePaths.BATCH_MANAGE ? 'batch' : 'library'
  }
  return subKey
}


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
            <div className="floating-menu-panel space-y-2 bg-base-card backdrop-blur-sm rounded-2xl shadow-xl border border-gray-200/50 dark:border-gray-800/30 p-2">
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
                      ? 'text-white shadow-sm'
                      : 'text-base-text hover:bg-base-hover'
                  )}
                  style={{
                    animationDelay: `${index * 50}ms`,
                    ...(activeKey === item.key ? { backgroundColor: 'var(--color-primary)' } : {}),
                  }}
                >
                  <div className="flex items-center justify-start gap-2">
                    <span className="inline-flex w-7 shrink-0 items-center justify-center">
                      <MyIcon icon={item.icon} size={item.iconSize || 20} className={item.iconClassName} />
                    </span>
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
  const { t } = useTranslation()
  const [isMobile, setIsMobile] = useAtom(isMobileAtom)
  const location = useLocation()
  const navigate = useNavigate()
  const [version, setVersion] = useState('N/A');
  const [docsUrl, setDocsUrl] = useState('');
  const [hasUpdate, setHasUpdate] = useState(false);
  const [versionModalOpen, setVersionModalOpen] = useState(false);
  const [realtimeLogOpen, setRealtimeLogOpen] = useState(false);
  const [historyLogOpen, setHistoryLogOpen] = useState(false);

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
      // 阈值 900px：低于此宽度桌面端导航即使 icon-only 也容易和右侧工具栏挤压重叠，切到移动端底部 tab 更友好
      setIsMobile(window.innerWidth <= 900)
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
                <RateLimitIndicator />
                <Tooltip title={t('header.realtimeLog')}>
                  <div onClick={() => setRealtimeLogOpen(true)} className="cursor-pointer" style={{ color: '#1890ff', fontSize: 18 }}>
                    <RealtimeLogIcon />
                  </div>
                </Tooltip>
                <Tooltip title={t('header.historyLog')}>
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
                  <Tooltip title={t('header.docs')}>
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
  const { t } = useTranslation()
  const mobileNavItems = [
    ...navItems.slice(0, 3), // 首页、弹幕库、任务管理器直接显示
    { key: 'user', label: 'nav.my', icon: 'user', children: navItems.slice(3) }, // 其余的放在"我的"菜单下
  ]
  const navigate = useNavigate()
  const location = useLocation()

  const [searchParams] = useSearchParams()
  const subKey = searchParams.get('key') // 当前 URL 中的子标签 key
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false)
  const [isSessionModalOpen, setIsSessionModalOpen] = useState(false)
  const [isThemeColorOpen, setIsThemeColorOpen] = useState(false)
  const [isPageStyleOpen, setIsPageStyleOpen] = useState(false)
  const [isLanguageOpen, setIsLanguageOpen] = useState(false)
  const [isCacheModalOpen, setIsCacheModalOpen] = useState(false)
  const [passwordForm] = Form.useForm()
  const [passwordLoading, setPasswordLoading] = useState(false)
  const [currentPasswordVisible, setCurrentPasswordVisible] = useState(false)
  const [newPasswordVisible, setNewPasswordVisible] = useState(false)
  const [confirmPasswordVisible, setConfirmPasswordVisible] = useState(false)
  const [dockerAvailable, setDockerAvailable] = useState(false)
  const [restartLoading, setRestartLoading] = useState(false)
  const messageApi = useMessage()
  const setUserinfo = useSetAtom(userinfoAtom)

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
    try {
      await logout()
    } catch (error) {
      // 即使服务端会话已失效，也必须清理本地登录状态，避免旧 token 残留导致无法重新登录
      console.warn('退出登录接口调用失败，已继续清理本地登录状态:', error)
    } finally {
      Cookies.remove('danmu_token', { path: '/' })
      setUserinfo(undefined)
      // 刷新页面清理前端状态（定时器、WebSocket、缓存等）
      window.location.href = RoutePaths.LOGIN
    }
  }

  const handleRestart = async () => {
    try {
      setRestartLoading(true)
      await restartService()
      messageApi.success(t('header.restartSent'))
    } catch (error) {
      messageApi.error(error.response?.data?.detail || t('header.restartFailed'))
    } finally {
      setRestartLoading(false)
    }
  }

  const handleChangePassword = async (values) => {
    try {
      setPasswordLoading(true)
      await changePassword(values)
      messageApi.success(t('header.passwordChangeSuccess'))
      setIsPasswordModalOpen(false)
      passwordForm.resetFields()
    } catch (error) {
      // 优先从 error.response.data.detail 获取（直接来自后端）
      const detail = error.response?.data?.detail || error.detail

      let errorMsg = t('header.passwordChangeFailed')

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

  const handleMenuItemClick = (item, parentItem) => {
    if (item.key === 'logout') {
      onLogout()
    } else if (item.key === 'change-password') {
      setIsPasswordModalOpen(true)
    } else if (item.key === 'session-manager') {
      setIsSessionModalOpen(true)
    } else if (item.key === 'theme-color') {
      setIsThemeColorOpen(true)
    } else if (item.key === 'page-style') {
      setIsPageStyleOpen(true)
    } else if (item.key === 'language') {
      setIsLanguageOpen(true)
    } else if (item.key === 'cache-manager') {
      setIsCacheModalOpen(true)
    } else if (item.key === 'clear-browser-cache') {
      clearBrowserCache()
    } else if (item.key === 'restart-service') {
      // 重启由 Popconfirm 处理，这里不做任何事
    } else if (parentItem?.key === 'user') {
      // "我的"菜单的子项是navItems中的页面，其children是子页面
      // 导航到该页面的第一个子页面
      const navItem = navItems.find(n => n.key === item.key)
      if (navItem?.children?.length) {
        navigate(getChildNavigatePath(navItem, navItem.children[0].key))
      } else {
        navigate(item.key)
      }
    } else if (parentItem) {
      // 非"我的"菜单的子项（如任务管理器的子项），导航到父路径+子key
      navigate(getChildNavigatePath(parentItem, item.key))
    } else {
      navigate(item.key)
    }
  }

  // 计算"我的"菜单的显示标签
  const userMenuItem = mobileNavItems.find(it => it.key === 'user')
  const activeChildItem = userMenuItem?.children?.find(child => child.key === activeKey)
  const userMenuLabel = activeChildItem ? t(activeChildItem.label) : t('nav.my')

  return (
    <>
      <div className="fixed bottom-5 left-4 right-4 shadow-box z-50 py-2.5 px-2 overflow-hidden bg-base-bg rounded-3xl mobile-nav-floating">
        <div className="flex justify-evenly items-center">
          {mobileNavItems.map(it => (
            <>
              {!it.children?.length ? (
                <div
                  key={it.key}
                  className="text-center flex-1"
                  style={it.key === activeKey ? { color: 'var(--color-primary)' } : undefined}
                  onClick={() => {
                    navigate(it.key)
                  }}
                >
                  <div>
                    <MyIcon icon={it.icon} size={26} />
                  </div>
                  <div className="text-xs">{t(it.label)}</div>
                </div>
              ) : (
                <FloatingMenu
                  key={it.key}
                  trigger={
                    <div
                      className="text-center flex-1"
                      style={
                        it.children.map(o => o.key).includes(activeKey)
                          ? { color: 'var(--color-primary)' }
                          : undefined
                      }
                    >
                      <div>
                        <MyIcon icon={it.icon} size={26} />
                      </div>
                      <div className="text-xs">{it.key === 'user' ? userMenuLabel : t(it.label)}</div>
                    </div>
                  }
                  items={[
                    ...it.children.map(o => ({
                      key: o.key,
                      label: t(o.label),
                      icon: o.icon,
                      iconSize: o.iconSize,
                      iconClassName: o.iconClassName,
                    })),
                    ...(it.key === 'user' ? [
                      {
                        key: 'theme-color',
                        label: t('header.themeColor'),
                        icon: 'MenuIcon-gexinghua-heise',
                      },
                      {
                        key: 'page-style',
                        label: t('header.pageStyle'),
                        icon: 'fengge',
                      },
                      {
                        key: 'language',
                        label: t('header.language'),
                        icon: 'yuyan',
                      },
                      {
                        key: 'session-manager',
                        label: t('header.sessionManager'),
                        icon: 'huihuaguanli',
                      },
                      {
                        key: 'change-password',
                        label: t('header.changePassword'),
                        icon: 'key',
                      },
                      {
                        key: 'cache-manager',
                        label: t('header.cacheManager'),
                        icon: 'refresh',
                      },
                      {
                        key: 'clear-browser-cache',
                        label: t('header.clearBrowserCache'),
                        icon: 'qinglihuancun',
                      },
                      ...(dockerAvailable ? [{
                        key: 'restart-service',
                        label: (
                          <Popconfirm
                            title={t('header.restartConfirmTitle')}
                            description={t('header.restartConfirmDesc')}
                            onConfirm={handleRestart}
                            okText={t('common.confirm')}
                            cancelText={t('common.cancel')}
                          >
                            <span>{t('header.restartService')}</span>
                          </Popconfirm>
                        ),
                        icon: 'zhongqi',
                      }] : []),
                      {
                        key: 'logout',
                        label: t('header.logout'),
                        icon: 'tuichudenglu',
                      },
                    ] : []),
                  ]}
                  onItemClick={(item) => handleMenuItemClick(item, it)}
                  activeKey={it.key === 'user' ? activeKey : getNavChildActiveKey(it, location, subKey)}
                />
              )}
            </>
          ))}
        </div>
      </div>

      {/* 修改密码弹框 */}
      <Modal
        title={t('header.changePassword')}
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
            label={t('header.currentPassword')}
            rules={[{ required: true, message: t('header.inputCurrentPassword') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('header.inputCurrentPassword')}
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
            label={t('header.newPassword')}
            rules={[{ required: true, message: t('header.inputNewPassword') }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('header.inputNewPassword')}
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
            label={t('header.confirmNewPassword')}
            dependencies={['newPassword']}
            rules={[
              { required: true, message: t('header.confirmInputNewPassword') },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error(t('header.passwordNotMatch')))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder={t('header.confirmInputNewPassword')}
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
                {t('common.cancel')}
              </Button>
              <Button type="primary" htmlType="submit" loading={passwordLoading}>
                {t('header.confirmChange')}
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

      {/* 页面样式切换弹窗 */}
      <PageStylePicker
        open={isPageStyleOpen}
        onClose={() => setIsPageStyleOpen(false)}
      />

      {/* 语言切换弹窗 */}
      <LanguagePicker
        open={isLanguageOpen}
        onClose={() => setIsLanguageOpen(false)}
      />

      {/* 缓存管理弹窗 */}
      <CacheManagerModal
        open={isCacheModalOpen}
        onClose={() => setIsCacheModalOpen(false)}
      />
    </>
  )
}

const DesktopHeader = ({ activeKey, version, docsUrl, hasUpdate, onVersionClick, onRealtimeLog, onHistoryLog }) => {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const userinfo = useAtomValue(userinfoAtom)
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const subKey = searchParams.get('key')

  const messageApi = useMessage()
  const setUserinfo = useSetAtom(userinfoAtom)
  const [isPasswordModalOpen, setIsPasswordModalOpen] = useState(false)
  const [isSessionModalOpen, setIsSessionModalOpen] = useState(false)
  const [isThemeColorOpen, setIsThemeColorOpen] = useState(false)
  const [isPageStyleOpen, setIsPageStyleOpen] = useState(false)
  const [isLanguageOpen, setIsLanguageOpen] = useState(false)
  const [isCacheModalOpen, setIsCacheModalOpen] = useState(false)
  const [form] = Form.useForm()
  const [showPassword1, setShowPassword1] = useState(false)
  const [showPassword2, setShowPassword2] = useState(false)
  const [showPassword3, setShowPassword3] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [dockerAvailable, setDockerAvailable] = useState(false)
  const [restartLoading, setRestartLoading] = useState(false)

  // ---- 导航挤压检测 ----
  const [compactNav, setCompactNav] = useState(false)
  const navContainerRef = useRef(null)
  const navMeasureRef = useRef(null)

  // 检测导航是否需要切换到 icon-only 模式
  const checkNavCompact = useCallback(() => {
    const container = navContainerRef.current
    const measure = navMeasureRef.current
    if (!container || !measure) return
    const available = container.clientWidth
    const natural = measure.scrollWidth
    setCompactNav(available < natural)
  }, [])

  useEffect(() => {
    const container = navContainerRef.current
    if (!container) return

    const ro = new ResizeObserver(checkNavCompact)
    ro.observe(container)
    if (container.parentElement) ro.observe(container.parentElement)
    return () => ro.disconnect()
  }, [checkNavCompact])

  // 语言切换时重新检测导航宽度（文字长度变化）
  useEffect(() => {
    // 延迟一帧让 DOM 先更新完文字内容
    requestAnimationFrame(checkNavCompact)
  }, [i18n.language, checkNavCompact])

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
    try {
      await logout()
    } catch (error) {
      // 即使服务端会话已失效，也必须清理本地登录状态，避免旧 token 残留导致无法重新登录
      console.warn('退出登录接口调用失败，已继续清理本地登录状态:', error)
    } finally {
      Cookies.remove('danmu_token', { path: '/' })
      setUserinfo(undefined)
      // 刷新页面清理前端状态（定时器、WebSocket、缓存等）
      window.location.href = RoutePaths.LOGIN
    }
  }

  const handleRestart = async () => {
    try {
      setRestartLoading(true)
      await restartService()
      messageApi.success(t('header.restartSent'))
    } catch (error) {
      messageApi.error(error.response?.data?.detail || t('header.restartFailed'))
    } finally {
      setRestartLoading(false)
    }
  }

  const handleChangePassword = async (values) => {
    try {
      setIsLoading(true)
      await changePassword(values)
      form.resetFields()
      messageApi.success(t('header.changeSuccess'))
      setIsPasswordModalOpen(false)
    } catch (error) {
      // 优先从 error.response.data.detail 获取（直接来自后端）
      const detail = error.response?.data?.detail || error.detail

      let errorMsg = t('header.changeFailed')

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
          <div onClick={() => navigate(RoutePaths.HOME)} className="flex-shrink-0">
            <img src="/images/logo.png" className="h-12 cursor-pointer" />
          </div>
          {/* 隐藏的测量行：icon+文字完整渲染，用于计算自然宽度 */}
          <div
            ref={navMeasureRef}
            className="flex items-center"
            style={{ position: 'absolute', visibility: 'hidden', whiteSpace: 'nowrap', pointerEvents: 'none', height: 0, overflow: 'hidden' }}
            aria-hidden="true"
          >
            {navItems.map(it => (
              <div key={it.key} className="flex items-center gap-1 mx-1 text-sm font-semibold whitespace-nowrap">
                <i
                  className={`iconfont ${it.iconfontIcon}`}
                  style={navIconStyle(16, it.iconScale)}
                />
                <span>{t(it.label)}</span>
              </div>
            ))}
          </div>

          {/* 实际导航 */}
          <div ref={navContainerRef} className="flex items-center justify-center min-w-0 flex-1">
            {navItems.map(it => {
              const isActive = activeKey === it.key

              // ---- 挤压态：icon-only ----
              if (compactNav) {
                if (it.children) {
                  // 有子项时用 Dropdown，顶部显示分类标题
                  return (
                    <Dropdown
                      key={it.key}
                      menu={{
                        items: [
                          { key: '_title', type: 'group', label: (<div className="font-bold text-sm" style={{ color: 'var(--ant-color-text)' }}>{t(it.label)}</div>) },
                          { type: 'divider' },
                          ...it.children.map(child => ({
                            key: child.key,
                            label: (
                              <span className="inline-flex items-center gap-2">
                                {child.icon && (
                                  <span className="inline-flex w-7 shrink-0 items-center justify-center">
                                    <MyIcon icon={child.icon} size={child.iconSize || 20} className={child.iconClassName} />
                                  </span>
                                )}
                                <span>{t(child.label)}</span>
                              </span>
                            ),
                          })),
                        ],
                        selectedKeys: [getNavChildActiveKey(it, location, subKey)],
                        onClick: ({ key: childKey }) => {
                          if (childKey !== '_title') navigate(getChildNavigatePath(it, childKey))
                        },
                      }}
                    >
                      <div
                        className={classNames(
                          'cursor-pointer mx-0.5 p-1.5 rounded-md transition-colors hover:bg-[var(--ant-color-bg-text-hover)]',
                          { 'text-primary': isActive }
                        )}
                        onClick={() => navigate(it.key)}
                      >
                        <i
                          className={`iconfont ${it.iconfontIcon}`}
                          style={navIconStyle(22, it.iconScale, true)}
                        />
                      </div>
                    </Dropdown>
                  )
                }
                // 无子项时用 Tooltip
                return (
                  <Tooltip key={it.key} title={t(it.label)}>
                    <div
                      className={classNames(
                        'cursor-pointer mx-0.5 p-1.5 rounded-md transition-colors hover:bg-[var(--ant-color-bg-text-hover)]',
                        { 'text-primary': isActive }
                      )}
                      onClick={() => navigate(it.key)}
                    >
                      <i
                        className={`iconfont ${it.iconfontIcon}`}
                        style={navIconStyle(22, it.iconScale, true)}
                      />
                    </div>
                  </Tooltip>
                )
              }

              // ---- 正常态：icon + 文字 ----
              if (it.children) {
                return (
                  <Dropdown
                    key={it.key}
                    menu={{
                      items: it.children.map(child => ({
                        key: child.key,
                        label: (
                          <span className="inline-flex items-center gap-2">
                            {child.icon && (
                              <span className="inline-flex w-7 shrink-0 items-center justify-center">
                                <MyIcon icon={child.icon} size={child.iconSize || 20} className={child.iconClassName} />
                              </span>
                            )}
                            <span>{t(child.label)}</span>
                          </span>
                        ),
                      })),
                      selectedKeys: [getNavChildActiveKey(it, location, subKey)],
                      onClick: ({ key: childKey }) => {
                        navigate(getChildNavigatePath(it, childKey))
                      },
                    }}
                  >
                    <div
                      className={classNames(
                        'text-sm font-semibold cursor-pointer mx-1 flex items-center gap-1 whitespace-nowrap',
                        { 'text-primary': isActive }
                      )}
                      onClick={() => navigate(it.key)}
                    >
                      <i
                        className={`iconfont ${it.iconfontIcon}`}
                        style={navIconStyle(16, it.iconScale)}
                      />
                      <span>{t(it.label)}</span>
                    </div>
                  </Dropdown>
                )
              }
              return (
                <div
                  key={it.key}
                  className={classNames(
                    'text-sm font-semibold cursor-pointer mx-1 flex items-center gap-1 whitespace-nowrap',
                    { 'text-primary': isActive }
                  )}
                  onClick={() => navigate(it.key)}
                >
                  <i
                    className={`iconfont ${it.iconfontIcon}`}
                    style={navIconStyle(16, it.iconScale)}
                  />
                  <span>{t(it.label)}</span>
                </div>
              )
            })}
          </div>
          <div className="flex items-center justify-center gap-4 ml-auto flex-shrink-0">
            <RateLimitIndicator />
            <Tooltip title={t('header.realtimeLog')}>
              <div onClick={onRealtimeLog} className="cursor-pointer" style={{ color: '#1890ff', fontSize: 20 }}>
                <RealtimeLogIcon />
              </div>
            </Tooltip>
            <Tooltip title={t('header.historyLog')}>
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
              <Tooltip title={t('header.docs')}>
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
                    icon: <MyIcon icon="MenuIcon-gexinghua-heise" size={16} />,
                    label: (
                      <div onClick={() => setIsThemeColorOpen(true)} className="text-base">
                        {t('header.themeColor')}
                      </div>
                    ),
                  },
                  {
                    key: 'pageStyle',
                    icon: <MyIcon icon="fengge" size={16} />,
                    label: (
                      <div onClick={() => setIsPageStyleOpen(true)} className="text-base">
                        {t('header.pageStyle')}
                      </div>
                    ),
                  },
                  {
                    key: 'language',
                    icon: <MyIcon icon="yuyan" size={16} />,
                    label: (
                      <div onClick={() => setIsLanguageOpen(true)} className="text-base">
                        {t('header.language')}
                      </div>
                    ),
                  },
                  {
                    key: 'sessionManager',
                    icon: <MyIcon icon="huihuaguanli" size={16} />,
                    label: (
                      <div onClick={() => setIsSessionModalOpen(true)} className="text-base">
                        {t('header.sessionManager')}
                      </div>
                    ),
                  },
                  {
                    key: 'changePassword',
                    icon: <MyIcon icon="key" size={16} />,
                    label: (
                      <div onClick={() => setIsPasswordModalOpen(true)} className="text-base">
                        {t('header.changePassword')}
                      </div>
                    ),
                  },
                  {
                    key: 'cacheManager',
                    icon: <MyIcon icon="refresh" size={16} />,
                    label: (
                      <div onClick={() => setIsCacheModalOpen(true)} className="text-base">
                        {t('header.cacheManager')}
                      </div>
                    ),
                  },
                  {
                    key: 'clearCache',
                    icon: <MyIcon icon="qinglihuancun" size={16} />,
                    label: (
                      <div onClick={clearBrowserCache} className="text-base">
                        {t('header.clearBrowserCache')}
                      </div>
                    ),
                  },
                  ...(dockerAvailable ? [{
                    key: 'restart',
                    icon: <MyIcon icon="zhongqi" size={16} />,
                    label: (
                      <Popconfirm
                        title={t('header.restartConfirmTitle')}
                        description={t('header.restartConfirmDesc')}
                        onConfirm={handleRestart}
                        okText={t('common.confirm')}
                        cancelText={t('common.cancel')}
                      >
                        <div className="text-base">{t('header.restartService')}</div>
                      </Popconfirm>
                    ),
                  }] : []),
                  {
                    key: 'logout',
                    icon: <MyIcon icon="tuichudenglu" size={16} />,
                    label: (
                      <div onClick={onLogout} className="text-base">
                        {t('header.logout')}
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
        title={t('header.changePassword')}
        open={isPasswordModalOpen}
        onCancel={() => {
          setIsPasswordModalOpen(false)
          form.resetFields()
        }}
        footer={null}
        width={500}
      >
        <div className="mb-4">
          {t('header.passwordChangeTip')}
        </div>
        <Form
          form={form}
          layout="vertical"
          onFinish={handleChangePassword}
        >
          <Form.Item
            name="oldPassword"
            label={t('header.currentPassword')}
            rules={[{ required: true, message: t('header.inputCurrentPassword') }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder={t('header.inputCurrentPassword')}
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
            label={t('header.newPassword')}
            rules={[{ required: true, message: t('header.inputNewPassword') }]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder={t('header.inputNewPassword')}
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
            label={t('header.confirmNewPassword')}
            dependencies={['newPassword']}
            rules={[
              {
                required: true,
                message: t('header.inputNewPassword'),
              },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('newPassword') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error(t('header.passwordMismatch')))
                },
              }),
            ]}
          >
            <Input.Password
              prefix={<LockOutlined className="text-gray-400" />}
              placeholder={t('header.inputNewPassword')}
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
                {t('common.cancel')}
              </Button>
              <Button type="primary" htmlType="submit" loading={isLoading}>
                {t('header.confirmChange')}
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

      {/* 页面样式切换弹窗 */}
      <PageStylePicker
        open={isPageStyleOpen}
        onClose={() => setIsPageStyleOpen(false)}
      />

      {/* 语言切换弹窗 */}
      <LanguagePicker
        open={isLanguageOpen}
        onClose={() => setIsLanguageOpen(false)}
      />

      {/* 缓存管理弹窗 */}
      <CacheManagerModal
        open={isCacheModalOpen}
        onClose={() => setIsCacheModalOpen(false)}
      />
    </>
  )
}

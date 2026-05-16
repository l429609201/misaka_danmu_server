import {
  Button,
  Card,
  Checkbox,
  Dropdown,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Row,
  Select,
  Slider,
  Spin,
  Switch,
  Space,
  Tag,
  Tooltip,
  Upload,
  Typography,
  Progress,
} from 'antd'
import { useEffect, useState, useRef } from 'react'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import {
  biliLogout,
  getbiliLoginQrcode,
  getbiliUserinfo,
  executeScraperAction,
  getScrapers,
  getSingleScraper,
  pollBiliLogin,
  setScrapers,
  setSingleScraper,
  getResourceRepo,
  getRepoRefs,
  saveResourceRepo,
  getScraperVersions,
  loadScraperResources,
  backupScrapers,
  restoreScrapers,
  reloadScrapers,
  uploadScraperPackage,
  deleteScraperBackup,
  deleteCurrentScrapers,
  deleteAllScrapers,
  getScraperAutoUpdate,
  saveScraperAutoUpdate,
  getScraperFullReplace,
  saveScraperFullReplace,
  getScraperDefaultBlacklist,
  getCommonBlacklist,
  startScraperDownload,
  getScraperDownloadStatus,
  getCurrentScraperDownload,
  cancelScraperDownload,
  generateRegex,
} from '../../../apis'
import { MyIcon } from '@/components/MyIcon'
import {
  closestCorners,
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import {
  CloudOutlined,
  DesktopOutlined,
  KeyOutlined,
  LockOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
} from '@ant-design/icons'

import ReactMarkdown from 'react-markdown'
import { QRCodeCanvas } from 'qrcode.react'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'

const SortableItem = ({
  item,
  biliUserinfo,
  index,
  handleChangeStatus,
  handleConfig,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.providerName, // 使用 providerName 作为唯一ID
    data: {
      item,
      index,
    },
  })

  const isMobile = useAtomValue(isMobileAtom)

  // 只保留必要的样式，移除会阻止滚动的touchAction
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    ...(isDragging && { cursor: 'grabbing' }),
  }

  return (
    <List.Item ref={setNodeRef} style={style}>
      <div className={`w-full flex ${isMobile ? 'gap-2' : 'items-center justify-between'}`}>
        {/* 左侧添加拖拽手柄 */}
        <div className="flex items-center gap-2">
          {/* 将attributes移到拖拽图标容器上，确保只有拖拽图标可触发拖拽 */}
          <div {...attributes} {...listeners} style={{ cursor: 'grab' }}>
            <MyIcon icon="drag" size={24} />
          </div>
          <div>{item.displayName || item.providerName}</div>
        </div>
        <div className={`flex ${isMobile ? 'ml-auto' : 'items-center justify-around'} gap-4`}>
          {item.providerName === 'bilibili' && (
            <div className={`flex ${isMobile ? 'items-center gap-2' : ''} ${isMobile ? 'text-center' : ''}`}>
              {biliUserinfo.isLogin ? (
                <div className={`flex ${isMobile ? 'flex-row items-center justify-center gap-2' : 'items-center justify-start gap-2'}`}>
                  <img
                    className="w-6 h-6 rounded-full"
                    src={biliUserinfo.face}
                  />
                  <span className={isMobile ? 'text-sm' : ''}>{biliUserinfo.uname}</span>
                </div>
              ) : (
                <span className="opacity-50 text-sm">未登录</span>
              )}
            </div>
          )}
          <div className={`flex ${isMobile ? 'justify-between items-center' : 'items-center justify-around'} gap-4`}>
            <div onClick={handleConfig} className="cursor-pointer">
              <MyIcon icon="setting" size={24} />
            </div>
            {item.useProxy && (
              <Tooltip title="已启用代理">
                <span className="text-blue-500"><MyIcon icon="wangluo" size={18} /></span>
              </Tooltip>
            )}
            {item.logRawResponses && (
              <Tooltip title="已启用记录原始响应">
                <span className="text-orange-400"><MyIcon icon="rizhi" size={18} /></span>
              </Tooltip>
            )}
            {item.version && (
              <Tag color="blue">{item.version}</Tag>
            )}
            <Switch
              checked={item.isEnabled}
              checkedChildren="已启用"
              unCheckedChildren="未启用"
              onChange={handleChangeStatus}
            />
          </div>
        </div>
      </div>
    </List.Item>
  )
}

export const Scrapers = () => {
  const [loading, setLoading] = useState(false)
  const [list, setList] = useState([])
  const [activeItem, setActiveItem] = useState(null)
  const dragOverlayRef = useRef(null)
  const eventSourceRef = useRef(null)
  // 设置窗口
  const [open, setOpen] = useState(false)
  // 设置类型
  const [setname, setSetname] = useState('')
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [form] = Form.useForm()

  const isMobile = useAtomValue(isMobileAtom)

  // bili 相关
  const [biliQrcode, setBiliQrcode] = useState({})
  const [biliQrcodeStatus, setBiliQrcodeStatus] = useState('')
  const [biliQrcodeLoading, setBiliQrcodeLoading] = useState(false)
  const [biliUserinfo, setBiliUserinfo] = useState({})
  const [biliLoginOpen, setBiliLoginOpen] = useState(false)
  const [biliQrcodeChecked, setBiliQrcodeChecked] = useState(false)
  /** 扫码登录轮训 */
  const timer = useRef(0)
  // dandanplay auth mode
  const [dandanAuthMode, setDandanAuthMode] = useState('local') // 'local' or 'proxy'
  const [showAppSecret, setShowAppSecret] = useState(false)
  const [showDisclaimerModal, setShowDisclaimerModal] = useState(false)
  // 填充默认黑名单加载状态
  const [loadingDefaultBlacklist, setLoadingDefaultBlacklist] = useState(false)
  const [loadingCommonBlacklist, setLoadingCommonBlacklist] = useState(false)
  const [aiRegexModalOpen, setAiRegexModalOpen] = useState(false)
  const [aiRegexDesc, setAiRegexDesc] = useState('')
  const [aiRegexLoading, setAiRegexLoading] = useState(false)
  const [aiRegexResult, setAiRegexResult] = useState('')

  // 资源仓库相关
  const [resourceRepoUrl, setResourceRepoUrl] = useState('')
  const [loadingResources, setLoadingResources] = useState(false)
  const [versionInfo, setVersionInfo] = useState({
    localVersion: 'unknown',
    remoteVersion: null,
    officialVersion: null,
    hasUpdate: false,
    localChangelog: null,
    remoteChangelog: null,
    officialChangelog: null
  })
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [changelogModal, setChangelogModal] = useState({ open: false, title: '', content: '' })
  const [uploadingPackage, setUploadingPackage] = useState(false)
  const [sseConnected, setSseConnected] = useState(false)

  // 自动更新相关
  const [autoUpdateEnabled, setAutoUpdateEnabled] = useState(false)
  const [autoUpdateLoading, setAutoUpdateLoading] = useState(false)

  // 全量替换相关
  const [fullReplaceEnabled, setFullReplaceEnabled] = useState(false)
  const [fullReplaceLoading, setFullReplaceLoading] = useState(false)

  // 分支选择相关
  const [selectedBranch, setSelectedBranch] = useState('main')
  const [repoRefs, setRepoRefs] = useState({ branches: [], tags: [], minServerVersion: null })
  const [refsLoading, setRefsLoading] = useState(false)

  // 下载进度相关
  const [downloadProgress, setDownloadProgress] = useState({
    visible: false,
    current: 0,
    total: 0,
    progress: 0,
    message: '',
    scraper: '',
    isRestarting: false  // 是否正在等待重启
  })
  const currentDownloadTaskId = useRef(null)  // 当前下载任务 ID


  const modalApi = useModal()
  const messageApi = useMessage()

  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 5,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        distance: 8,
        delay: 100,
      },
    })
  )

  useEffect(() => {
    getInfo()
    loadResourceRepoConfig()
    loadAutoUpdateConfig()
    loadFullReplaceConfig()

    // 建立 SSE 日志流, 根据相关事件自动刷新版本信息
    const token = Cookies.get('danmu_token')
    if (token) {
      const abortController = new AbortController()
      eventSourceRef.current = abortController

      fetchEventSource('/api/ui/logs/stream', {
        signal: abortController.signal,
        headers: {
          Authorization: `Bearer ${token}`,
        },
        onopen: async response => {
          if (response.ok) {
            setSseConnected(true)
          } else {
            setSseConnected(false)
            throw new Error(`连接失败: ${response.status}`)
          }
        },
        onmessage: event => {
          const data = event.data || ''
          if (!data) return

          // 监听与弹幕源加载/重载/还原相关的日志, 自动刷新版本信息
          if (
            data.includes('弹幕源') &&
            (data.includes('成功加载了') || data.includes('成功重载了') || data.includes('成功从备份重载了'))
          ) {
            loadVersionInfo()
          }
        },
        onerror: error => {
          console.error('版本信息 SSE 连接错误:', error)
          setSseConnected(false)
          throw error
        },
      }).catch(error => {
        if (error.name !== 'AbortError') {
          console.error('版本信息 SSE 流错误:', error)
        }
      })
    } else {
      console.warn('未找到 danmu_token, 跳过版本信息 SSE 监听')
    }

    // 清理函数:组件卸载时关闭SSE连接
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.abort()
        eventSourceRef.current = null
      }
      // 同时取消下载任务
      if (currentDownloadTaskId.current) {
        cancelScraperDownload(currentDownloadTaskId.current).catch(() => {})
        currentDownloadTaskId.current = null
      }
    }
  }, [])

  const getInfo = async () => {
    let scraperList = []
    try {
      setLoading(true)
      const res1 = await getScrapers()
      scraperList = res1.data ?? []
      setList(scraperList)
    } catch (error) {
    } finally {
      setLoading(false)
    }
    // bilibili 登录状态：仅当 bilibili 搜索源存在时才请求，避免无意义的报错
    const hasBilibili = scraperList.some(s => s.providerName === 'bilibili')
    if (hasBilibili) {
      try {
        const res2 = await getbiliUserinfo()
        setBiliUserinfo(res2.data)
      } catch (error) {
      }
    }
  }

  const loadResourceRepoConfig = async () => {
    try {
      const res = await getResourceRepo()
      setResourceRepoUrl(res.data?.repoUrl || '')

      // 同时加载版本信息和分支/标签列表
      await Promise.all([loadVersionInfo(), loadRepoRefs()])
    } catch (error) {
      console.error('加载资源仓库配置失败:', error)
    }
  }

  const loadRepoRefs = async () => {
    try {
      setRefsLoading(true)
      const res = await getRepoRefs()
      setRepoRefs({
        branches: res.data?.branches || [],
        tags: res.data?.tags || [],
        minServerVersion: res.data?.minServerVersion || null,
      })
    } catch (error) {
      console.error('加载仓库分支/标签失败:', error)
    } finally {
      setRefsLoading(false)
    }
  }

  const loadVersionInfo = async () => {
    try {
      setLoadingVersions(true)
      const res = await getScraperVersions()
      setVersionInfo({
        localVersion: res.data?.localVersion || 'unknown',
        remoteVersion: res.data?.remoteVersion || null,
        officialVersion: res.data?.officialVersion || null,
        hasUpdate: res.data?.hasUpdate || false,
        localChangelog: res.data?.localChangelog || null,
        remoteChangelog: res.data?.remoteChangelog || null,
        officialChangelog: res.data?.officialChangelog || null,
        minFetchableVersion: res.data?.minFetchableVersion || null,
      })
      return res.data
    } catch (error) {
      console.error('加载版本信息失败:', error)
      return null
    } finally {
      setLoadingVersions(false)
    }
  }

  // 加载自动更新配置（后端轮询，前端只控制开关）
  const loadAutoUpdateConfig = async () => {
    try {
      const res = await getScraperAutoUpdate()
      const enabled = res.data?.enabled || false
      setAutoUpdateEnabled(enabled)
    } catch (error) {
      console.error('加载自动更新配置失败:', error)
    }
  }

  // 切换自动更新状态（后端轮询，前端只控制开关）
  const handleAutoUpdateToggle = async (checked) => {
    try {
      setAutoUpdateLoading(true)
      // 获取当前配置的间隔时间，默认30分钟
      const currentConfig = await getScraperAutoUpdate()
      const interval = currentConfig.data?.interval || 30
      await saveScraperAutoUpdate({ enabled: checked, interval })
      setAutoUpdateEnabled(checked)
      if (checked) {
        messageApi.success(`已启用自动更新，后台每${interval}分钟检查一次`)
      } else {
        messageApi.success('已关闭自动更新')
      }
    } catch (error) {
      messageApi.error('保存自动更新配置失败')
    } finally {
      setAutoUpdateLoading(false)
    }
  }

  // 加载全量替换配置
  const loadFullReplaceConfig = async () => {
    try {
      const res = await getScraperFullReplace()
      const enabled = res.data?.enabled || false
      setFullReplaceEnabled(enabled)
    } catch (error) {
      console.error('加载全量替换配置失败:', error)
    }
  }

  // 切换全量替换状态
  const handleFullReplaceToggle = async (checked) => {
    try {
      setFullReplaceLoading(true)
      await saveScraperFullReplace({ enabled: checked })
      setFullReplaceEnabled(checked)
      if (checked) {
        messageApi.success('已启用全量替换模式，下次更新将从 Releases 下载压缩包')
      } else {
        messageApi.success('已关闭全量替换模式，将使用逐文件对比下载')
      }
    } catch (error) {
      messageApi.error('保存全量替换配置失败')
    } finally {
      setFullReplaceLoading(false)
    }
  }

  // 通过 SSE 订阅下载任务进度
  const subscribeDownloadProgress = (taskId) => {
    const token = Cookies.get('danmu_token')
    if (!token) {
      messageApi.error('未找到认证令牌')
      setLoadingResources(false)
      return
    }

    // 标记任务是否已完成（用于忽略连接断开错误）
    let taskCompleted = false

    fetchEventSource(`/api/ui/scrapers/download/progress/${taskId}`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      onopen: async response => {
        if (response.ok) {
          console.log('SSE 进度流已连接')
        } else {
          throw new Error(`连接失败: ${response.status}`)
        }
      },
      onmessage: event => {
        try {
          const data = JSON.parse(event.data)

          if (data.type === 'progress') {
            console.log('收到 progress 消息:', data.status, 'need_restart:', data.need_restart)
            // 更新进度
            // 当 total = 0 时（无需下载），显示 100%；否则按实际进度计算
            // 当 current = total 且 total > 0 时，也显示 100%（下载完成，可能在热加载中）
            let progress = 0
            if (data.total === 0) {
              // 无需下载的情况，直接显示 100%
              progress = 100
            } else if (data.current >= data.total) {
              // 下载完成（可能在热加载或部署阶段）
              progress = 100
            } else {
              progress = Math.round((data.current / data.total) * 100)
            }

            setDownloadProgress(prev => ({
              ...prev,
              current: data.current,
              total: data.total,
              progress: progress,
              scraper: data.current_file,
              message: data.messages?.slice(-1)[0] || prev.message
            }))

            // 检查状态 - 只更新显示，不处理完成逻辑（统一在 done 消息中处理）
            if (data.status === 'completed') {
              const downloadedCount = data.downloaded_count || 0
              const skippedCount = data.skipped_count || 0
              const failedCount = data.failed_count || 0

              // 只更新进度显示，完成逻辑统一在 done 消息中处理
              if (data.need_restart) {
                setDownloadProgress(prev => ({
                  ...prev,
                  progress: 100,
                  message: `下载完成! 成功: ${downloadedCount}, 跳过: ${skippedCount}，等待容器重启...`
                }))
              } else {
                setDownloadProgress(prev => ({
                  ...prev,
                  progress: 100,
                  message: `下载完成! 成功: ${downloadedCount}, 跳过: ${skippedCount}, 失败: ${failedCount}`
                }))
              }
              // 不在这里设置 taskCompleted 和刷新，等待 done 消息统一处理
            }

            if (data.status === 'failed') {
              taskCompleted = true
              messageApi.error(data.error_message || '下载失败')
              setDownloadProgress({
                visible: false,
                current: 0,
                total: 0,
                progress: 0,
                message: '',
                scraper: '',
                isRestarting: false
              })
              setLoadingResources(false)
            }

            if (data.status === 'cancelled') {
              taskCompleted = true
              messageApi.info('下载已取消')
              setDownloadProgress({
                visible: false,
                current: 0,
                total: 0,
                progress: 0,
                message: '',
                scraper: '',
                isRestarting: false
              })
              setLoadingResources(false)
            }
          }

          // 处理重启通知
          if (data.type === 'restart') {
            taskCompleted = true
            messageApi.info(data.message || '弹幕源更新完成，容器即将重启...')
            setDownloadProgress(prev => ({
              ...prev,
              progress: 100,
              message: data.message || '弹幕源更新完成，容器即将重启...'
            }))
            // 不立即关闭进度条，等待 done 消息
          }

          if (data.type === 'done') {
            console.log('收到 done 消息:', data)
            taskCompleted = true

            // 检查是否需要重启
            if (data.need_restart) {
              messageApi.info('弹幕源更新完成，容器正在重启中...')
              setDownloadProgress(prev => ({
                ...prev,
                progress: 0,  // 重置进度，用于显示重启等待进度
                message: '弹幕源更新完成，容器正在重启中...',
                isRestarting: true
              }))

              // 轮询检测服务是否恢复，最多等待 120 秒
              const checkServiceReady = async () => {
                const maxWaitSeconds = 120  // 最大等待时间
                const checkInterval = 2000   // 每 2 秒检测一次
                let waitSeconds = 0
                let serviceWentDown = false  // 标记服务是否已经停止过

                // 第一阶段：等待服务停止（最多等待 30 秒）
                setDownloadProgress(prev => ({
                  ...prev,
                  progress: 0,
                  message: '等待容器停止...'
                }))

                for (let i = 0; i < 30; i++) {
                  waitSeconds++
                  const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
                  setDownloadProgress(prev => ({
                    ...prev,
                    progress: Math.min(restartProgress, 25),  // 第一阶段最多 25%
                    message: `等待容器停止... (${waitSeconds}秒)`
                  }))

                  try {
                    const response = await fetch('/api/ui/version', {
                      method: 'GET',
                      signal: AbortSignal.timeout(2000)
                    })
                    if (!response.ok) {
                      // 服务返回错误，认为已停止
                      serviceWentDown = true
                      console.log('服务已停止（返回错误）')
                      break
                    }
                  } catch (e) {
                    // 服务不可用，认为已停止
                    serviceWentDown = true
                    console.log('服务已停止（连接失败）')
                    break
                  }

                  await new Promise(resolve => setTimeout(resolve, 1000))
                }

                // 如果服务一直没停止，可能重启很快，继续等待恢复
                if (!serviceWentDown) {
                  console.log('服务似乎没有停止，可能重启非常快，继续检测...')
                }

                // 第二阶段：等待服务恢复
                for (let i = 0; i < 60; i++) {  // 最多尝试 60 次
                  // 更新等待状态和进度
                  const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
                  setDownloadProgress(prev => ({
                    ...prev,
                    progress: Math.min(restartProgress, 95),  // 最多显示 95%，留 5% 给完成
                    message: `正在等待服务恢复... (${waitSeconds}秒)`
                  }))

                  try {
                    // 使用 /api/ui/version 接口检测服务是否完全启动
                    const response = await fetch('/api/ui/version', {
                      method: 'GET',
                      signal: AbortSignal.timeout(3000)
                    })
                    if (response.ok) {
                      // 服务恢复，刷新界面
                      setDownloadProgress(prev => ({
                        ...prev,
                        progress: 100,
                        message: '服务已恢复，正在刷新...'
                      }))
                      await new Promise(resolve => setTimeout(resolve, 500))
                      setDownloadProgress({
                        visible: false,
                        current: 0,
                        total: 0,
                        progress: 0,
                        message: '',
                        scraper: '',
                        isRestarting: false
                      })
                      messageApi.success('容器重启完成')
                      getInfo()
                      loadVersionInfo()
                      setLoadingResources(false)
                      return
                    }
                  } catch (e) {
                    // 服务还未恢复，继续等待
                    console.log(`等待服务恢复... (${waitSeconds}秒)`)
                  }

                  // 等待 checkInterval 毫秒，同时更新秒数
                  for (let j = 0; j < checkInterval / 1000; j++) {
                    await new Promise(resolve => setTimeout(resolve, 1000))
                    waitSeconds++
                    const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
                    setDownloadProgress(prev => ({
                      ...prev,
                      progress: Math.min(restartProgress, 95),
                      message: `正在等待服务恢复... (${waitSeconds}秒)`
                    }))
                  }
                }

                // 超时，关闭进度条并提示用户手动刷新
                setDownloadProgress({
                  visible: false,
                  current: 0,
                  total: 0,
                  progress: 0,
                  message: '',
                  scraper: '',
                  isRestarting: false
                })
                messageApi.warning('容器重启超时，请手动刷新页面')
                setLoadingResources(false)
              }

              checkServiceReady()
            } else {
              // 不需要重启的情况（首次下载热加载完成 或 所有弹幕源都是最新的）
              messageApi.success('弹幕源加载完成')

              // 显示刷新动画
              setDownloadProgress(prev => ({
                ...prev,
                progress: 100,
                message: '正在刷新页面数据...',
                isRestarting: true  // 复用重启动画
              }))

              // 延迟关闭进度条并刷新数据
              setTimeout(() => {
                setDownloadProgress({
                  visible: false,
                  current: 0,
                  total: 0,
                  progress: 0,
                  message: '',
                  scraper: '',
                  isRestarting: false
                })
                getInfo()
                loadVersionInfo()
                setLoadingResources(false)
              }, 1500)
            }

            // SSE 流正常结束
            throw new Error('任务完成，停止 SSE')
          }

          if (data.type === 'error') {
            taskCompleted = true
            messageApi.error(data.message || '下载失败')
            setDownloadProgress({
              visible: false,
              current: 0,
              total: 0,
              progress: 0,
              message: '',
              scraper: '',
              isRestarting: false
            })
            setLoadingResources(false)
            throw new Error('任务失败，停止 SSE')
          }
        } catch (e) {
          if (e.message.includes('停止 SSE')) {
            throw e
          }
          console.error('解析 SSE 消息失败:', e)
        }
      },
      onerror: error => {
        console.error('SSE 进度流错误:', error)
        // 如果任务已完成，忽略连接断开错误
        if (taskCompleted) {
          console.log('任务已完成，忽略连接断开错误')
          throw new Error('任务已完成，停止重试')
        }
        if (error.name !== 'AbortError') {
          // SSE 断开时，尝试查询缓存的任务状态（可能是容器重启导致的断开）
          console.log('SSE 断开，尝试查询缓存的任务状态...')

          // 使用 fetch 直接查询，避免 axios 拦截器的影响
          const token = Cookies.get('danmu_token')
          fetch(`/api/ui/scrapers/download/cached-status/${taskId}`, {
            headers: { Authorization: `Bearer ${token}` }
          })
            .then(res => res.json())
            .then(result => {
              if (result.found && result.data) {
                const data = result.data
                if (data.status === 'completed') {
                  // 任务已完成，可能是容器重启前完成的
                  taskCompleted = true
                  const downloadedCount = data.downloaded_count || 0
                  const skippedCount = data.skipped_count || 0
                  const failedCount = data.failed_count || 0

                  if (data.need_restart) {
                    // 容器正在重启，不在这里刷新，让 checkServiceReady() 处理
                    // 只更新提示信息，不触发刷新
                    console.log('SSE 断开后查询到任务完成且需要重启，等待 checkServiceReady() 处理')
                    setDownloadProgress(prev => ({
                      ...prev,
                      progress: 100,
                      message: `下载完成! 成功: ${downloadedCount}, 跳过: ${skippedCount}，容器正在重启...`,
                      isRestarting: true
                    }))
                    // 不刷新，直接返回
                    return
                  } else {
                    messageApi.success(`下载完成! 成功: ${downloadedCount}, 跳过: ${skippedCount}`)
                    setDownloadProgress(prev => ({
                      ...prev,
                      progress: 100,
                      message: `下载完成! 成功: ${downloadedCount}, 跳过: ${skippedCount}`
                    }))
                  }

                  // 只有不需要重启时才延迟刷新
                  setTimeout(() => {
                    setDownloadProgress({
                      visible: false,
                      current: 0,
                      total: 0,
                      progress: 0,
                      message: '',
                      scraper: '',
                      isRestarting: false
                    })
                    getInfo()
                    loadVersionInfo()
                    setLoadingResources(false)
                  }, 2000)
                } else if (data.status === 'failed') {
                  messageApi.error(data.error_message || '下载失败')
                  setDownloadProgress({
                    visible: false,
                    current: 0,
                    total: 0,
                    progress: 0,
                    message: '',
                    scraper: '',
                    isRestarting: false
                  })
                  setLoadingResources(false)
                } else {
                  // 任务状态未知，显示错误
                  messageApi.error('进度连接出错，请刷新页面查看状态')
                  setDownloadProgress({
                    visible: false,
                    current: 0,
                    total: 0,
                    progress: 0,
                    message: '',
                    scraper: '',
                    isRestarting: false
                  })
                  setLoadingResources(false)
                }
              } else {
                // 缓存中没有找到任务状态，显示错误
                messageApi.error('进度连接出错，请刷新页面查看状态')
                setDownloadProgress({
                  visible: false,
                  current: 0,
                  total: 0,
                  progress: 0,
                  message: '',
                  scraper: '',
                  isRestarting: false
                })
                setLoadingResources(false)
              }
            })
            .catch(fetchError => {
              console.error('查询缓存状态失败:', fetchError)
              // 查询失败，可能是容器正在重启，显示友好提示
              messageApi.warning('连接已断开，可能是容器正在重启，请稍后刷新页面')
              setDownloadProgress(prev => ({
                ...prev,
                message: '连接已断开，可能是容器正在重启...'
              }))
              // 不立即关闭进度条，让用户看到提示
              setTimeout(() => {
                setDownloadProgress({
                  visible: false,
                  current: 0,
                  total: 0,
                  progress: 0,
                  message: '',
                  scraper: '',
                  isRestarting: false
                })
                setLoadingResources(false)
              }, 3000)
            })
        }
        throw error
      },
    }).catch(error => {
      if (!error.message?.includes('停止')) {
        console.error('SSE 流错误:', error)
      }
    })
  }

  const handleLoadResources = async () => {
    if (!resourceRepoUrl.trim()) {
      messageApi.error('请输入资源仓库链接')
      return
    }

    try {
      setLoadingResources(true)

      // 保存配置
      await saveResourceRepo({ repoUrl: resourceRepoUrl })

      // 重置进度状态
      setDownloadProgress({
        visible: true,
        current: 0,
        total: 0,
        progress: 0,
        message: '正在启动下载任务...',
        scraper: '',
        isRestarting: false
      })

      // 启动后台下载任务
      const res = await startScraperDownload({
        repoUrl: resourceRepoUrl,
        fullReplace: fullReplaceEnabled,
        branch: selectedBranch  // 添加分支参数
      })

      const taskId = res.data.task_id
      if (!taskId) {
        throw new Error('启动下载任务失败')
      }

      // 保存任务 ID 以便取消
      currentDownloadTaskId.current = taskId

      setDownloadProgress(prev => ({
        ...prev,
        message: `下载任务已启动，正在获取资源信息...`
      }))

      // 通过 SSE 订阅任务进度
      subscribeDownloadProgress(taskId)

    } catch (error) {
      messageApi.error(error.response?.data?.detail || '启动下载任务失败')
      setDownloadProgress({
        visible: false,
        current: 0,
        total: 0,
        progress: 0,
        message: '',
        scraper: '',
        isRestarting: false
      })
      setLoadingResources(false)
    }
  }

  const handleUploadPackage = async (file) => {
    // 验证文件对象
    if (!file || !(file instanceof File)) {
      messageApi.error('无效的文件对象')
      return false
    }

    const formData = new FormData()
    formData.append('file', file)

    setUploadingPackage(true)

    try {
      // 传递配置对象,设置正确的 Content-Type
      const res = await uploadScraperPackage(formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      })

      const responseData = res.data || {}
      const needRestart = responseData.need_restart
      const autoRestart = responseData.auto_restart

      if (needRestart) {
        // 需要重启容器
        if (autoRestart) {
          // 自动重启：显示等待进度
          messageApi.info(responseData.message || '上传成功，容器正在重启...')
          setDownloadProgress({
            visible: true,
            current: 0,
            total: 0,
            progress: 0,
            message: '容器正在重启中...',
            scraper: '',
            isRestarting: true
          })

          // 轮询检测服务是否恢复
          const checkServiceReady = async () => {
            const maxWaitSeconds = 120
            let waitSeconds = 0
            let serviceWentDown = false

            // 第一阶段：等待服务停止
            setDownloadProgress(prev => ({
              ...prev,
              progress: 0,
              message: '等待容器停止...'
            }))

            for (let i = 0; i < 30; i++) {
              waitSeconds++
              const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
              setDownloadProgress(prev => ({
                ...prev,
                progress: Math.min(restartProgress, 25),
                message: `等待容器停止... (${waitSeconds}秒)`
              }))

              try {
                const response = await fetch('/api/ui/version', {
                  method: 'GET',
                  signal: AbortSignal.timeout(2000)
                })
                if (!response.ok) {
                  serviceWentDown = true
                  break
                }
              } catch (e) {
                serviceWentDown = true
                break
              }

              await new Promise(resolve => setTimeout(resolve, 1000))
            }

            // 第二阶段：等待服务恢复
            for (let i = 0; i < 60; i++) {
              const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
              setDownloadProgress(prev => ({
                ...prev,
                progress: Math.min(restartProgress, 95),
                message: `正在等待服务恢复... (${waitSeconds}秒)`
              }))

              try {
                const response = await fetch('/api/ui/version', {
                  method: 'GET',
                  signal: AbortSignal.timeout(3000)
                })
                if (response.ok) {
                  setDownloadProgress(prev => ({
                    ...prev,
                    progress: 100,
                    message: '服务已恢复，正在刷新...'
                  }))
                  await new Promise(resolve => setTimeout(resolve, 500))
                  setDownloadProgress({
                    visible: false,
                    current: 0,
                    total: 0,
                    progress: 0,
                    message: '',
                    scraper: '',
                    isRestarting: false
                  })
                  messageApi.success('容器重启完成')
                  await getInfo()
                  await loadVersionInfo()
                  return
                }
              } catch (e) {
                // 继续等待
              }

              for (let j = 0; j < 2; j++) {
                await new Promise(resolve => setTimeout(resolve, 1000))
                waitSeconds++
                const restartProgress = Math.round((waitSeconds / maxWaitSeconds) * 100)
                setDownloadProgress(prev => ({
                  ...prev,
                  progress: Math.min(restartProgress, 95),
                  message: `正在等待服务恢复... (${waitSeconds}秒)`
                }))
              }
            }

            // 超时
            setDownloadProgress({
              visible: false,
              current: 0,
              total: 0,
              progress: 0,
              message: '',
              scraper: '',
              isRestarting: false
            })
            messageApi.warning('容器重启超时，请手动刷新页面')
          }

          checkServiceReady()
        } else {
          // 手动重启：显示提示信息
          messageApi.warning(responseData.message || '上传成功，请手动重启容器以加载新的弹幕源')
        }
      } else {
        // 不需要重启（首次上传热加载）
        messageApi.success(responseData.message || '上传成功')

        // 延迟刷新,等待后台热加载完成
        setTimeout(async () => {
          try {
            await getInfo()
            await loadVersionInfo()
          } catch (error) {
            console.error('刷新信息失败:', error)
          }
        }, 2500)
      }
    } catch (error) {
      messageApi.error(error.response?.data?.detail || '上传失败')
    } finally {
      setUploadingPackage(false)
    }

    // 返回 false 阻止 Upload 组件的默认上传行为
    return false
  }

  const handleDragEnd = event => {
    const { active, over } = event

    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    // 找到原位置和新位置
    const activeIndex = list.findIndex(item => item.providerName === active.id)
    const overIndex = list.findIndex(item => item.providerName === over.id)

    if (activeIndex !== -1 && overIndex !== -1) {
      // 1. 重新排列数组
      const newList = [...list]
      const [movedItem] = newList.splice(activeIndex, 1)
      newList.splice(overIndex, 0, movedItem)

      // 2. 重新计算所有项的display_order（从1开始连续编号）
      const updatedList = newList.map((item, index) => ({
        ...item,
        displayOrder: index + 1, // 排序值从1开始
      }))

      // 3. 更新状态
      setList(updatedList)
      setScrapers(updatedList)
      messageApi.success(
        `已更新排序，${movedItem.providerName} 移动到位置 ${overIndex + 1}`
      )
    }

    setActiveItem(null)
  }

  // 处理拖拽开始
  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = list.find(item => item.providerName === active.id)
    setActiveItem(item)
  }

  const handleChangeStatus = item => {
    const newList = list.map(it => {
      if (it.providerName === item.providerName) {
        return {
          ...it,
          isEnabled: !it.isEnabled,
        }
      } else {
        return it
      }
    })
    setList(newList)
    setScrapers(newList)
  }

  const handleConfig = async item => {
    const res = await getSingleScraper({
      name: item.providerName,
    })
    setOpen(true)
    setSetname(item.providerName)
    const setNameCapitalize = `${item.providerName.charAt(0).toUpperCase()}${item.providerName.slice(1)}`

    // 动态地为所有可配置字段设置表单初始值
    const dynamicInitialValues = {}
    if (item.configurableFields) {
      for (const [key, fieldInfo] of Object.entries(item.configurableFields)) {
        const camelKey = key.replace(/_([a-z])/g, g => g[1].toUpperCase())
        const config = parseFieldConfig(fieldInfo)
        let value = res.data?.[camelKey]

        // 如果是 boolean 类型，需要将字符串转换为真正的 boolean
        if (config.type === 'boolean') {
          if (typeof value === 'string') {
            value = value === 'true' || value === '1'
          } else if (typeof value === 'number') {
            value = value !== 0
          } else {
            value = Boolean(value)
          }
        }

        dynamicInitialValues[camelKey] = value
      }
    }

    form.setFieldsValue({
      [`scraper${setNameCapitalize}LogResponses`]:
        res.data?.[`scraper${setNameCapitalize}LogResponses`] ?? false,
      [`${item.providerName}EpisodeBlacklistRegex`]:
        res.data?.[`${item.providerName}EpisodeBlacklistRegex`] || '',
      useProxy: res.data?.useProxy ?? false,
      [`scraper_${item.providerName}_search_timeout`]:
        parseInt(res.data?.[`scraper_${item.providerName}_search_timeout`]) || 15,
      ...dynamicInitialValues,
    })

    // Dandanplay specific logic
    if (item.providerName === 'dandanplay') {
      // 如果配置了 App ID，则为本地模式，否则默认为代理模式
      if (res.data?.dandanplayAppId) {
        setDandanAuthMode('local')
      } else {
        setDandanAuthMode('proxy')
      }
    }
  }

  const handleSaveSingleScraper = async () => {
    try {
      setConfirmLoading(true)
      const values = await form.validateFields()
      const setNameCapitalize = `${setname.charAt(0).toUpperCase()}${setname.slice(1)}`

      // 根据当前模式，清空另一种模式的配置
      if (setname === 'dandanplay') {
        if (dandanAuthMode === 'local') {
          values.dandanplayProxyConfig = ''
        } else {
          values.dandanplayAppId = ''
          values.dandanplayAppSecret = ''
          values.dandanplayAppSecretAlt = ''
          values.dandanplayApiBaseUrl = ''
        }
        // dandanplay 不使用全局代理，移除该字段
        delete values.useProxy
      }

      await setSingleScraper({
        ...values,
        [`scraper${setNameCapitalize}LogResponses`]:
          values[`scraper${setNameCapitalize}LogResponses`],
        name: setname,
      })
      messageApi.success('保存成功')
    } catch (error) {
      console.error(error)
      messageApi.error('保存失败')
    } finally {
      setConfirmLoading(false)
      setOpen(false)
      form.resetFields()
      getInfo() // 刷新列表以更新代理/日志图标状态
    }
  }

  const startBiliLoginPoll = data => {
    timer.current = window.setInterval(() => {
      pollBiliLogin({
        qrcodeKey: data.qrcodeKey,
      })
        .then(res => {
          if (res.data.code === 86038) {
            clearInterval(timer.current)
            setBiliQrcodeStatus('expire')
          } else if (res.data.code === 86090) {
            setBiliQrcodeStatus('mobileConfirm')
          } else if (res.data.code === 0) {
            // 登录成功
            clearInterval(timer.current)
            setBiliLoginOpen(false)
            setOpen(false)
            getInfo()
          }
        })
        .catch(error => {
          setBiliQrcodeStatus('error')
          clearInterval(timer.current)
        })
    }, 1000)
  }

  useEffect(() => {
    return () => {
      clearInterval(timer.current)
    }
  }, [])

  const handleBiliQrcode = async () => {
    try {
      const res = await getbiliLoginQrcode()
      setBiliQrcode(res.data)
      setBiliQrcodeLoading(true)
      setBiliLoginOpen(true)
      startBiliLoginPoll(res.data)
      setBiliQrcodeStatus('')
    } catch (error) {
      messageApi.error('获取二维码失败')
    } finally {
      setBiliQrcodeLoading(false)
    }
  }

  const cancelBiliLogin = () => {
    setBiliLoginOpen(false)
    clearInterval(timer.current)
    setBiliQrcodeStatus('')
  }

  // 填充源默认分集黑名单
  const handleFillDefaultBlacklist = async () => {
    if (!setname) return
    try {
      setLoadingDefaultBlacklist(true)
      const res = await getScraperDefaultBlacklist(setname)
      if (res.data && res.data.defaultBlacklist) {
        form.setFieldValue(`${setname}EpisodeBlacklistRegex`, res.data.defaultBlacklist)
        messageApi.success('已填充源默认过滤规则')
      } else {
        messageApi.warning('该搜索源没有默认过滤规则')
      }
    } catch (error) {
      messageApi.error('获取源默认过滤规则失败')
    } finally {
      setLoadingDefaultBlacklist(false)
    }
  }

  // 填充通用分集黑名单
  const handleFillCommonBlacklist = async () => {
    if (!setname) return
    try {
      setLoadingCommonBlacklist(true)
      const res = await getCommonBlacklist()
      if (res.data && res.data.commonBlacklist) {
        form.setFieldValue(`${setname}EpisodeBlacklistRegex`, res.data.commonBlacklist)
        messageApi.success('已填充通用过滤规则')
      } else {
        messageApi.warning('未找到通用过滤规则')
      }
    } catch (error) {
      messageApi.error('获取通用过滤规则失败')
    } finally {
      setLoadingCommonBlacklist(false)
    }
  }

  const handleAiGenerate = async () => {
    if (!aiRegexDesc.trim()) {
      messageApi.warning('请输入描述')
      return
    }
    setAiRegexLoading(true)
    setAiRegexResult('')
    try {
      const existing = form.getFieldValue(`${setname}EpisodeBlacklistRegex`) || ''
      const res = await generateRegex(aiRegexDesc.trim(), existing, 'episode_blacklist')
      if (res.data?.regex) {
        setAiRegexResult(res.data.regex)
      } else {
        messageApi.error('AI 未能生成有效的正则表达式')
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || 'AI 正则生成失败')
    } finally {
      setAiRegexLoading(false)
    }
  }

  const handleApplyAiRegex = () => {
    if (!aiRegexResult) return
    const fieldKey = `${setname}EpisodeBlacklistRegex`
    form.setFieldValue(fieldKey, aiRegexResult)
    setAiRegexModalOpen(false)
    setAiRegexDesc('')
    setAiRegexResult('')
    messageApi.success('已应用 AI 生成的规则')
  }

  const handleBiliLogout = () => {
    modalApi.confirm({
      title: '清除缓存',
      zIndex: 1002,
      content: <div>确定要注销当前的Bilibili登录吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await biliLogout()
          getInfo()
          setBiliQrcodeStatus('')
        } catch (err) { }
      },
    })
  }

  const renderDragOverlay = () => {
    if (!activeItem) return null

    return (
      <div ref={dragOverlayRef} style={{ width: '100%', maxWidth: '100%' }}>
        <List.Item
          style={{
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
            opacity: 0.9,
          }}
        >
          <div className="w-full flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MyIcon icon="drag" size={24} />
              <div>{activeItem.displayName || activeItem.providerName}</div>
            </div>
            <div className="flex items-center justify-around gap-4">
              <MyIcon icon="setting" size={24} />
              {activeItem.isEnabled ? (
                <Tag color="green">已启用</Tag>
              ) : (
                <Tag color="red">未启用</Tag>
              )}
            </div>
          </div>
        </List.Item>
      </div>
    )
  }

  // 解析字段配置（兼容多种格式）
  const parseFieldConfig = (fieldInfo) => {
    if (typeof fieldInfo === 'string') {
      // 旧格式：仅label
      return { label: fieldInfo, type: 'string', tooltip: '' }
    } else if (Array.isArray(fieldInfo)) {
      // 元组格式：[label, type, tooltip]
      return {
        label: fieldInfo[0],
        type: fieldInfo[1] || 'string',
        tooltip: fieldInfo[2] || '',
        placeholder: '',
        options: [],
        min: undefined,
        max: undefined,
        step: undefined,
        rows: 4,
      }
    } else {
      // 新格式：完整对象
      return {
        type: 'string',
        tooltip: '',
        placeholder: '',
        options: [],
        rows: 4,
        ...fieldInfo,
      }
    }
  }

  // 处理 action 类型按钮点击
  const handleActionClick = async (providerName, actionName, successMessage, errorMessage) => {
    try {
      const res = await executeScraperAction(providerName, actionName)
      if (res.data?.success === false) {
        messageApi.error(res.data?.message || errorMessage || '操作失败')
      } else {
        messageApi.success(res.data?.message || successMessage || '操作成功')
      }
    } catch (error) {
      console.error('Action error:', error)
      messageApi.error(error?.response?.data?.detail || errorMessage || '操作失败')
    }
  }

  const renderDynamicFormItems = () => {
    const currentScraper = list.find(it => it.providerName === setname)
    if (!currentScraper || !currentScraper.configurableFields) {
      return null
    }

    return Object.entries(currentScraper.configurableFields).map(
      ([key, fieldInfo]) => {
        const config = parseFieldConfig(fieldInfo)
        const { label, type, tooltip, placeholder, options, min, max, step, rows } = config
        const camelKey = key.replace(/_([a-z])/g, g => g[1].toUpperCase())

        // 如果是 dandanplay，则跳过所有已在定制UI中处理的字段
        if (setname === 'dandanplay') {
          return null
        }

        // 跳过通用黑名单字段，因为它在下面有专门的渲染逻辑
        if (key.endsWith('_episode_blacklist_regex')) {
          return null
        }

        // 根据类型渲染对应的表单控件
        switch (type) {
          case 'boolean':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                valuePropName="checked"
                className="mb-4"
                tooltip={tooltip}
              >
                <Switch />
              </Form.Item>
            )

          case 'password':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Input.Password placeholder={placeholder} />
              </Form.Item>
            )

          case 'number':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <InputNumber
                  min={min}
                  max={max}
                  step={step}
                  placeholder={placeholder}
                  style={{ width: '100%' }}
                />
              </Form.Item>
            )

          case 'select':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Select placeholder={placeholder || '请选择'}>
                  {(options || []).map(opt => (
                    <Select.Option key={opt.value} value={opt.value}>
                      {opt.label}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            )

          case 'textarea':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Input.TextArea rows={rows} placeholder={placeholder} />
              </Form.Item>
            )

          case 'url':
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Input
                  placeholder={placeholder || 'https://example.com'}
                />
              </Form.Item>
            )

          case 'action':
            // action 类型：渲染一个按钮，点击后调用后端 action
            const { actionName, buttonText, buttonType, confirmText, successMessage, errorMessage } = config
            return (
              <Form.Item
                key={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Button
                  type={buttonType || 'default'}
                  onClick={async () => {
                    // 如果有确认文本，先弹出确认框
                    if (confirmText) {
                      Modal.confirm({
                        title: '确认操作',
                        content: confirmText,
                        okText: '确认',
                        cancelText: '取消',
                        onOk: async () => {
                          await handleActionClick(setname, actionName, successMessage, errorMessage)
                        }
                      })
                    } else {
                      await handleActionClick(setname, actionName, successMessage, errorMessage)
                    }
                  }}
                >
                  {buttonText || label}
                </Button>
              </Form.Item>
            )

          case 'string':
          default:
            // 为 gamer 的 cookie 提供更大的输入框
            if (key === 'gamerCookie') {
              return (
                <Form.Item
                  key={camelKey}
                  name={camelKey}
                  label={label}
                  className="mb-4"
                  tooltip={tooltip}
                >
                  <Input.TextArea rows={4} />
                </Form.Item>
              )
            }
            return (
              <Form.Item
                key={camelKey}
                name={camelKey}
                label={label}
                className="mb-4"
                tooltip={tooltip}
              >
                <Input placeholder={placeholder} />
              </Form.Item>
            )
        }
      }
    )
  }

  return (
    <div className="my-6">
      {/* 资源仓库配置卡片 */}
      <Card title="资源仓库" className="mb-4">
        <div className="space-y-4">
          <div>
            <div className="mb-2 text-sm text-gray-600">
              从资源仓库加载弹幕源文件,或上传离线包进行安装
            </div>
            <div className={`flex gap-2 ${isMobile ? 'flex-col' : 'flex-row'}`}>
              <Input
                placeholder="请输入GitHub仓库链接，例如：https://github.com/username/repo"
                value={resourceRepoUrl}
                onChange={(e) => setResourceRepoUrl(e.target.value)}
              />
              {/* 分支/版本选择器 */}
              <Select
                value={selectedBranch}
                onChange={setSelectedBranch}
                loading={refsLoading}
                style={{ width: isMobile ? '100%' : 180 }}
                placeholder="选择分支或版本"
                onDropdownVisibleChange={(open) => {
                  if (open && repoRefs.branches.length === 0 && repoRefs.tags.length === 0) {
                    loadRepoRefs()
                  }
                }}
              >
                {repoRefs.branches.length > 0 || repoRefs.tags.length > 0 ? (
                  <>
                    {repoRefs.branches.length > 0 && (
                      <Select.OptGroup label="分支">
                        {repoRefs.branches.map(b => (
                          <Select.Option key={`branch-${b}`} value={b}>{b}</Select.Option>
                        ))}
                      </Select.OptGroup>
                    )}
                    {repoRefs.tags.length > 0 && (
                      <Select.OptGroup label="版本标签">
                        {repoRefs.tags.map(t => {
                          // 比较 tag 版本与远程 package.json 中的 min_fetchable_version
                          // 低于该最低可拉取版本的标记为不可用并禁用
                          const parseVer = (v) => (v || '').replace(/^v/i, '').split('.').map(Number)
                          const tagVer = parseVer(t)
                          const minVer = parseVer(versionInfo.minFetchableVersion)
                          const isDisabled = minVer.length >= 3 && tagVer.length >= 3 &&
                            (tagVer[0] < minVer[0] ||
                              (tagVer[0] === minVer[0] && tagVer[1] < minVer[1]) ||
                              (tagVer[0] === minVer[0] && tagVer[1] === minVer[1] && tagVer[2] < minVer[2]))
                          return (
                            <Select.Option key={`tag-${t}`} value={t} disabled={isDisabled}>
                              {t}{isDisabled ? ' (不兼容当前服务器)' : ''}
                            </Select.Option>
                          )
                        })}
                      </Select.OptGroup>
                    )}
                  </>
                ) : (
                  <>
                    <Select.Option value="main">main</Select.Option>
                    <Select.Option value="test">test</Select.Option>
                  </>
                )}
              </Select>
              {isMobile ? (
                <>
                  <Button
                    type="primary"
                    loading={loadingResources}
                    onClick={handleLoadResources}
                    className="w-full"
                  >
                    加载资源
                  </Button>
                  <div className="flex gap-2 w-full">
                    <Button
                      onClick={async () => {
                        if (!resourceRepoUrl.trim()) {
                          messageApi.error('请输入资源仓库链接')
                          return
                        }
                        try {
                          await saveResourceRepo({ repoUrl: resourceRepoUrl })
                          messageApi.success('保存成功')
                          await loadVersionInfo()
                        } catch (error) {
                          messageApi.error(error.response?.data?.detail || '保存失败')
                        }
                      }}
                      className="flex-1"
                      style={{ flex: 1, height: '30px' }}
                    >
                      保存
                    </Button>
                    <Upload
                      beforeUpload={handleUploadPackage}
                      accept=".zip,.tar.gz,.tgz"
                      showUploadList={false}
                      disabled={uploadingPackage}
                      className="flex-1"
                      style={{ flex: 1, width: '100%' }}
                    >
                      <Button loading={uploadingPackage} disabled={uploadingPackage} className="w-full" style={{ width: '100%', minHeight: '10px', height: '30px' }}>
                        离线包上传
                      </Button>
                    </Upload>
                  </div>
                </>
              ) : (
                <>
                  <Button
                    onClick={async () => {
                      if (!resourceRepoUrl.trim()) {
                        messageApi.error('请输入资源仓库链接')
                        return
                      }
                      try {
                        await saveResourceRepo({ repoUrl: resourceRepoUrl })
                        messageApi.success('保存成功')
                        await loadVersionInfo()
                      } catch (error) {
                        messageApi.error(error.response?.data?.detail || '保存失败')
                      }
                    }}
                  >
                    保存
                  </Button>
                  <Button
                    type="primary"
                    loading={loadingResources}
                    onClick={handleLoadResources}
                  >
                    加载资源
                  </Button>
                  <Upload
                    beforeUpload={handleUploadPackage}
                    accept=".zip,.tar.gz,.tgz"
                    showUploadList={false}
                    disabled={uploadingPackage}
                  >
                    <Button loading={uploadingPackage} disabled={uploadingPackage}>
                      离线包上传
                    </Button>
                  </Upload>
                </>
              )}
            </div>
          </div>

          {/* 下载进度条 */}
          {downloadProgress.visible && (
            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm text-gray-600">
                  {downloadProgress.isRestarting && (
                    <span className="inline-block mr-2 animate-spin">⏳</span>
                  )}
                  {downloadProgress.message}
                </span>
                {!downloadProgress.isRestarting && (
                  <Button
                    size="small"
                    danger
                    onClick={async () => {
                      if (currentDownloadTaskId.current) {
                        try {
                          await cancelScraperDownload(currentDownloadTaskId.current)
                          messageApi.warning('已取消下载')
                        } catch (e) {
                          console.error('取消下载失败:', e)
                        }
                        currentDownloadTaskId.current = null
                      }
                      setDownloadProgress({
                        visible: false,
                        current: 0,
                        total: 0,
                        progress: 0,
                        message: '',
                        scraper: '',
                        isRestarting: false
                      })
                      setLoadingResources(false)
                    }}
                  >
                    取消
                  </Button>
                )}
              </div>
              <Progress
                percent={downloadProgress.progress}
                status={downloadProgress.isRestarting ? 'active' : (downloadProgress.progress === 100 ? 'success' : 'active')}
                strokeColor={downloadProgress.isRestarting ? {
                  '0%': '#faad14',
                  '100%': '#52c41a',
                } : {
                  '0%': '#108ee9',
                  '100%': '#87d068',
                }}
              />
            </div>
          )}

          {/* 版本信息 + 操作按钮（合并为一行，始终展示） */}
          <div className={`flex ${isMobile ? 'flex-col gap-4' : 'items-center justify-between'} mb-4`}>
              <Card size="small" className={isMobile ? 'w-full' : ''}>
                <div className="flex flex-col gap-2">
                  {isMobile ? (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        {versionInfo.officialVersion && (
                          <>
                            <Typography.Text className="text-sm text-gray-600">主仓:</Typography.Text>
                            <Typography.Text
                              code
                              style={{ color: '#ce1ea2ff', cursor: versionInfo.officialChangelog ? 'pointer' : 'default' }}
                              onClick={() => versionInfo.officialChangelog && setChangelogModal({ open: true, title: '主仓版本日志', content: versionInfo.officialChangelog })}
                            >
                              {versionInfo.officialVersion}
                            </Typography.Text>
                          </>
                        )}
                        {versionInfo.remoteVersion && (
                          <>
                            <Typography.Text className="text-sm text-gray-600">远程:</Typography.Text>
                            <Typography.Text
                              code
                              style={{ color: '#52c41a', cursor: versionInfo.remoteChangelog ? 'pointer' : 'default' }}
                              onClick={() => versionInfo.remoteChangelog && setChangelogModal({ open: true, title: '远程版本日志', content: versionInfo.remoteChangelog })}
                            >
                              {versionInfo.remoteVersion}
                            </Typography.Text>
                          </>
                        )}
                      </div>
                      <div className="flex gap-3">
                        <div className="flex items-center gap-8">
                          <Typography.Text className="text-sm text-gray-600">本地:</Typography.Text>
                          <Typography.Text
                            code
                            style={{ color: '#1890ff', cursor: versionInfo.localChangelog ? 'pointer' : 'default' }}
                            onClick={() => versionInfo.localChangelog && setChangelogModal({ open: true, title: '本地版本日志', content: versionInfo.localChangelog })}
                          >
                            {versionInfo.localVersion}
                          </Typography.Text>
                        </div>
                        <div className="ml-auto">
                          <Button
                            type="text"
                            onClick={loadVersionInfo}
                            style={{
                              color: '#ff69b4',
                              width: 60,
                              position: 'relative',
                              padding: 0,
                            }}
                          >
                            {loadingVersions ? (
                              <>
                                <Spin
                                  size="small"
                                  style={{
                                    position: 'absolute',
                                    left: '50%',
                                    top: '50%',
                                    transform: 'translate(-50%, -50%)',
                                  }}
                                />
                                <span style={{ opacity: 0 }}>刷新</span>
                              </>
                            ) : '刷新'}
                          </Button>
                        </div>
                      </div>
                      {/* 移动端：自动更新和全量替换开关 */}
                      <div className="flex items-center justify-between mt-2">
                        <div className="flex items-center gap-2">
                          <Typography.Text className="text-sm text-gray-600">自动更新:</Typography.Text>
                          <Switch
                            size="small"
                            checked={autoUpdateEnabled}
                            loading={autoUpdateLoading}
                            checkedChildren="启用"
                            unCheckedChildren="关闭"
                            onChange={handleAutoUpdateToggle}
                          />
                        </div>
                        <div className="flex items-center gap-2">
                          <Tooltip title="启用后从 GitHub Releases 下载压缩包全量替换，适用于 .so 文件更新不生效的情况">
                            <Typography.Text className="text-sm text-gray-600" style={{ cursor: 'help' }}>全量替换:</Typography.Text>
                          </Tooltip>
                          <Switch
                            size="small"
                            checked={fullReplaceEnabled}
                            loading={fullReplaceLoading}
                            checkedChildren="启用"
                            unCheckedChildren="关闭"
                            onChange={handleFullReplaceToggle}
                          />
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-4">
                      {versionInfo.officialVersion && (
                        <div className="flex items-center gap-2">
                          <Typography.Text className="text-sm text-gray-600">主仓版本:</Typography.Text>
                          <Typography.Text
                            code
                            style={{ color: '#ce1ea2ff', cursor: versionInfo.officialChangelog ? 'pointer' : 'default' }}
                            onClick={() => versionInfo.officialChangelog && setChangelogModal({ open: true, title: '主仓版本日志', content: versionInfo.officialChangelog })}
                          >
                            {versionInfo.officialVersion}
                          </Typography.Text>
                        </div>
                      )}
                      {versionInfo.remoteVersion && (
                        <div className="flex items-center gap-2">
                          <Typography.Text className="text-sm text-gray-600">远程版本:</Typography.Text>
                          <Typography.Text
                            code
                            style={{ color: '#52c41a', cursor: versionInfo.remoteChangelog ? 'pointer' : 'default' }}
                            onClick={() => versionInfo.remoteChangelog && setChangelogModal({ open: true, title: '远程版本日志', content: versionInfo.remoteChangelog })}
                          >
                            {versionInfo.remoteVersion}
                          </Typography.Text>
                        </div>
                      )}
                      <div className="flex items-center gap-2">
                        <Typography.Text className="text-sm text-gray-600">本地版本:</Typography.Text>
                        <Typography.Text
                          code
                          style={{ color: '#1890ff', cursor: versionInfo.localChangelog ? 'pointer' : 'default' }}
                          onClick={() => versionInfo.localChangelog && setChangelogModal({ open: true, title: '本地版本日志', content: versionInfo.localChangelog })}
                        >
                          {versionInfo.localVersion}
                        </Typography.Text>
                      </div>
                      <div className="flex items-center gap-2">
                        <Typography.Text className="text-sm text-gray-600">自动更新:</Typography.Text>
                        <Switch
                          size="small"
                          checked={autoUpdateEnabled}
                          loading={autoUpdateLoading}
                          checkedChildren="启用"
                          unCheckedChildren="关闭"
                          onChange={handleAutoUpdateToggle}
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <Tooltip title="启用后从 GitHub Releases 下载压缩包全量替换，适用于 .so 文件更新不生效的情况">
                          <Typography.Text className="text-sm text-gray-600" style={{ cursor: 'help' }}>全量替换:</Typography.Text>
                        </Tooltip>
                        <Switch
                          size="small"
                          checked={fullReplaceEnabled}
                          loading={fullReplaceLoading}
                          checkedChildren="启用"
                          unCheckedChildren="关闭"
                          onChange={handleFullReplaceToggle}
                        />
                      </div>
                      <Button
                        type="text"
                        onClick={loadVersionInfo}
                        style={{
                          color: '#ff69b4',
                          width: 60,
                          position: 'relative',
                          padding: 0,
                        }}
                      >
                        {loadingVersions ? (
                          <>
                            <Spin
                              size="small"
                              style={{
                                position: 'absolute',
                                left: '50%',
                                top: '50%',
                                transform: 'translate(-50%, -50%)',
                              }}
                            />
                            <span style={{ opacity: 0 }}>刷新</span>
                          </>
                        ) : '刷新'}
                      </Button>
                      {/* PC端：更新提示显示在刷新按钮右边 */}
                      {versionInfo.hasUpdate && (
                        <Typography.Text type="warning" style={{ marginLeft: 8 }}>🆙 有更新可用</Typography.Text>
                      )}
                    </div>
                  )}
                  {/* 移动端：更新提示显示在下一行 */}
                  {isMobile && versionInfo.hasUpdate && (
                    <div className="flex items-center gap-2">
                      <Typography.Text type="warning">🆙 有更新可用</Typography.Text>
                    </div>
                  )}
                </div>
              </Card>

              {/* 右侧：源操作按钮 —— 仅在 PC 端显示 */}
              {!isMobile && (
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: 'reload',
                        label: '重载当前源',
                        onClick: async () => {
                          try {
                            setLoading(true)
                            const res = await reloadScrapers()
                            messageApi.success(res.data?.message || '重载成功，正在后台重载...')
                            setTimeout(() => {
                              getInfo()
                              loadVersionInfo()
                            }, 2500)
                          } catch (error) {
                            messageApi.error(error.response?.data?.detail || '重载失败')
                          } finally {
                            setLoading(false)
                          }
                        }
                      },
                      {
                        key: 'backup',
                        label: '备份当前源',
                        onClick: async () => {
                          try {
                            const res = await backupScrapers()
                            messageApi.success(res.data?.message || '备份成功')
                          } catch (error) {
                            messageApi.error(error.response?.data?.detail || '备份失败')
                          }
                        }
                      },
                      {
                        key: 'restore',
                        label: '从备份中还原',
                        onClick: () => {
                          modalApi.confirm({
                            title: '还原弹幕源',
                            content: '确定要从备份还原弹幕源吗？这将覆盖当前的弹幕源文件。',
                            okText: '确认',
                            cancelText: '取消',
                            onOk: async () => {
                              try {
                                const res = await restoreScrapers()
                                messageApi.success(res.data?.message || '还原成功，正在后台重载...')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '还原失败')
                              }
                            },
                          })
                        }
                      },
                      { type: 'divider' },
                      {
                        key: 'deleteBackup',
                        label: '删除备份源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除备份',
                            content: '确定要删除所有备份文件吗？此操作不可恢复。',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteScraperBackup()
                                messageApi.success(res.data?.message || '删除备份成功')
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除备份失败')
                              }
                            },
                          })
                        }
                      },
                      {
                        key: 'deleteCurrent',
                        label: '删除当前源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除当前弹幕源',
                            content: '确定要删除所有当前弹幕源文件吗？此操作不可恢复。',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteCurrentScrapers()
                                messageApi.success(res.data?.message || '删除成功')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除失败')
                              }
                            },
                          })
                        }
                      },
                      {
                        key: 'deleteAll',
                        label: '删除当前&备份源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除所有弹幕源',
                            content: '确定要删除所有当前弹幕源和备份文件吗？此操作不可恢复！',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteAllScrapers()
                                messageApi.success(res.data?.message || '删除成功')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除失败')
                              }
                            },
                          })
                        }
                      },
                    ]
                  }}
                >
                  <Button type="primary">源操作</Button>
                </Dropdown>
              )}
            </div>

          {/* 移动端：源操作按钮 */}
          {
            isMobile && (
              <div className="flex gap-2 flex-wrap mb-4">
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: 'reload',
                        label: '重载当前源',
                        onClick: async () => {
                          try {
                            setLoading(true)
                            const res = await reloadScrapers()
                            messageApi.success(res.data?.message || '重载成功，正在后台重载...')
                            setTimeout(() => {
                              getInfo()
                              loadVersionInfo()
                            }, 2500)
                          } catch (error) {
                            messageApi.error(error.response?.data?.detail || '重载失败')
                          } finally {
                            setLoading(false)
                          }
                        }
                      },
                      {
                        key: 'backup',
                        label: '备份当前源',
                        onClick: async () => {
                          try {
                            const res = await backupScrapers()
                            messageApi.success(res.data?.message || '备份成功')
                          } catch (error) {
                            messageApi.error(error.response?.data?.detail || '备份失败')
                          }
                        }
                      },
                      {
                        key: 'restore',
                        label: '从备份中还原',
                        onClick: () => {
                          modalApi.confirm({
                            title: '还原弹幕源',
                            content: '确定要从备份还原弹幕源吗？这将覆盖当前的弹幕源文件。',
                            okText: '确认',
                            cancelText: '取消',
                            onOk: async () => {
                              try {
                                const res = await restoreScrapers()
                                messageApi.success(res.data?.message || '还原成功，正在后台重载...')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '还原失败')
                              }
                            },
                          })
                        }
                      },
                      { type: 'divider' },
                      {
                        key: 'deleteBackup',
                        label: '删除备份源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除备份',
                            content: '确定要删除所有备份文件吗？此操作不可恢复。',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteScraperBackup()
                                messageApi.success(res.data?.message || '删除备份成功')
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除备份失败')
                              }
                            },
                          })
                        }
                      },
                      {
                        key: 'deleteCurrent',
                        label: '删除当前源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除当前弹幕源',
                            content: '确定要删除所有当前弹幕源文件吗？此操作不可恢复。',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteCurrentScrapers()
                                messageApi.success(res.data?.message || '删除成功')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除失败')
                              }
                            },
                          })
                        }
                      },
                      {
                        key: 'deleteAll',
                        label: '删除当前&备份源',
                        danger: true,
                        onClick: () => {
                          modalApi.confirm({
                            title: '删除所有弹幕源',
                            content: '确定要删除所有当前弹幕源和备份文件吗？此操作不可恢复！',
                            okText: '确认删除',
                            cancelText: '取消',
                            okButtonProps: { danger: true },
                            onOk: async () => {
                              try {
                                const res = await deleteAllScrapers()
                                messageApi.success(res.data?.message || '删除成功')
                                setTimeout(() => {
                                  getInfo()
                                  loadVersionInfo()
                                }, 2500)
                              } catch (error) {
                                messageApi.error(error.response?.data?.detail || '删除失败')
                              }
                            },
                          })
                        }
                      },
                    ]
                  }}
                >
                  <Button type="primary" className="flex-1 min-w-0">源操作</Button>
                </Dropdown>
              </div>
            )
          }
        </div >
      </Card >

      {/* 弹幕搜索源卡片 */}
      < Card loading={loading} title="弹幕搜索源" >
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            strategy={verticalListSortingStrategy}
            items={list.map(item => item.providerName)}
          >
            <List
              itemLayout="vertical"
              size="large"
              dataSource={list}
              renderItem={(item, index) => (
                <SortableItem
                  key={item.providerName}
                  item={item}
                  index={index}
                  biliUserinfo={biliUserinfo}
                  handleChangeStatus={() => handleChangeStatus(item)}
                  handleConfig={() => handleConfig(item)}
                />
              )}
            />
          </SortableContext>
          {/* 拖拽覆盖层 */}
          <DragOverlay>{renderDragOverlay()}</DragOverlay>
        </DndContext>
      </Card >
      <Modal
        title={`配置: ${setname}`}
        open={open}
        onOk={handleSaveSingleScraper}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setOpen(false)}
        destroyOnClose // 确保每次打开时都重新渲染
        forceRender // 确保表单项在Modal打开时就存在
        width={isMobile ? '95%' : '600px'}
        centered
      >
        <Form form={form} layout="vertical">
          {setname !== 'dandanplay' && (
            <Form.Item
              name="useProxy"
              label="使用代理"
              valuePropName="checked"
              className="mb-4"
            >
              <Switch />
            </Form.Item>
          )}

          <Form.Item
            label="搜索超时"
            tooltip="该源的搜索请求超时时间。最低5秒，最大100秒，留空则不限。"
            className="mb-4"
          >
            <div className="flex items-start gap-3">
              <div className="flex-1">
                <Form.Item name={`scraper_${setname}_search_timeout`} noStyle>
                  <Slider
                    min={5}
                    max={100}
                    marks={{ 5: '5s', 15: '15s', 30: '30s', 60: '60s', 100: '100s' }}
                  />
                </Form.Item>
              </div>
              <div style={{ marginTop: 4 }}>
                <Form.Item name={`scraper_${setname}_search_timeout`} noStyle>
                  <InputNumber min={5} max={100} controls={false} style={{ width: 80 }} addonAfter="秒" />
                </Form.Item>
              </div>
            </div>
          </Form.Item>

          {/* dandanplay specific */}
          {setname === 'dandanplay' && (
            <>
              <Form.Item label="认证方式" className="mb-6">
                <div className={`flex ${isMobile ? 'flex-col gap-2' : 'items-center gap-4'}`}>
                  <Switch
                    checkedChildren={
                      <Space>
                        <CloudOutlined />
                        跨域代理
                      </Space>
                    }
                    unCheckedChildren={
                      <Space>
                        <DesktopOutlined />
                        本地功能
                      </Space>
                    }
                    checked={dandanAuthMode === 'proxy'}
                    onChange={checked =>
                      setDandanAuthMode(checked ? 'proxy' : 'local')
                    }
                  />
                  <div className="text-sm text-gray-600">
                    {dandanAuthMode === 'local' ? '使用本地App ID和Secret进行认证' : '通过跨域代理使用API'}
                  </div>
                </div>
              </Form.Item>

              {dandanAuthMode === 'local' && (
                <>
                  <Form.Item
                    name="dandanplayAppId"
                    label={
                      <span>
                        App ID{' '}
                        <a
                          href="https://www.dandanplay.com/dev"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <QuestionCircleOutlined className="cursor-pointer text-gray-400" />
                        </a>
                      </span>
                    }
                    rules={[{ required: true, message: '请输入App ID' }]}
                    className="mb-4"
                  >
                    <Input
                      prefix={<KeyOutlined className="text-gray-400" />}
                      placeholder="请输入App ID"
                    />
                  </Form.Item>

                  <Form.Item
                    name="dandanplayAppSecret"
                    label="App Secret"
                    rules={[{ required: true, message: '请输入App Secret' }]}
                    className="mb-4"
                  >
                    <Input.Password
                      prefix={<LockOutlined className="text-gray-400" />}
                      placeholder="请输入App Secret"
                    />
                  </Form.Item>

                  <Form.Item
                    name="dandanplayAppSecretAlt"
                    label="备用App Secret"
                    tooltip="可选的备用密钥，用于轮换使用以避免频率限制"
                    className="mb-4"
                  >
                    <Input.Password
                      prefix={<LockOutlined className="text-gray-400" />}
                      placeholder="请输入备用App Secret（可选）"
                    />
                  </Form.Item>

                  <Form.Item
                    name="dandanplayApiBaseUrl"
                    label="API基础URL"
                    tooltip="弹弹play API的基础URL，通常无需修改"
                    className="mb-4"
                  >
                    <Input placeholder="默认为 https://api.dandanplay.net" />
                  </Form.Item>
                </>
              )}

              {dandanAuthMode === 'proxy' && (
                <Form.Item
                  name="dandanplayProxyConfig"
                  label="跨域代理配置"
                  rules={[
                    { required: true, message: '请输入代理配置' },
                  ]}
                  className="mb-6"
                >
                  <Input.TextArea rows={isMobile ? 6 : 8} />
                </Form.Item>
              )}

              <Form.Item
                name="dandanplayEpisodeIndexNormalize"
                label="分集序号归一化（多季自动偏移）"
                valuePropName="checked"
                className="mb-4"
                tooltip="开启后，当弹弹play返回的分集序号不以1开头（如多季连续编号的第29~34集）时，自动将其偏移为从第1集开始（第1~6集）。适用于弹弹play中跨季连续编号的番剧。"
              >
                <Switch />
              </Form.Item>
            </>
          )}

          {/* 动态渲染表单项 */}
          {renderDynamicFormItems()}

          {/* 通用部分 分集标题黑名单 记录原始响应 */}
          <Form.Item
            name={`${setname}EpisodeBlacklistRegex`}
            label={
              <div className="flex items-center justify-between w-full">
                <span>分集标题黑名单 (正则)</span>
                <Space size="small">
                  <Button
                    type="link"
                    size="small"
                    loading={loadingCommonBlacklist}
                    onClick={handleFillCommonBlacklist}
                  >
                    填充通用规则
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    loading={loadingDefaultBlacklist}
                    onClick={handleFillDefaultBlacklist}
                  >
                    填充源默认规则
                  </Button>
                  <Tooltip title="AI 生成正则">
                    <Button
                      type="link"
                      size="small"
                      icon={<RobotOutlined />}
                      onClick={() => setAiRegexModalOpen(true)}
                    >
                      AI 生成
                    </Button>
                  </Tooltip>
                </Space>
              </div>
            }
            className="mb-4"
          >
            <Input.TextArea rows={6} />
          </Form.Item>
          <div className={`flex ${isMobile ? 'flex-col gap-2' : 'items-center justify-start flex-wrap'} gap-2 mb-4`}>
            <Form.Item
              name={`scraper${setname.charAt(0).toUpperCase()}${setname.slice(1)}LogResponses`}
              label="记录原始响应"
              valuePropName="checked"
              className={isMobile ? "min-w-full !mb-0" : "min-w-[100px] shrink-0 !mb-0"}
            >
              <Switch />
            </Form.Item>
            <div className={`w-full ${isMobile ? 'text-sm' : ''}`}>
              启用后，此源的所有API请求的原始响应将被记录到
              config/logs/scraper_responses.log 文件中，用于调试。
            </div>
          </div>
          {/* bilibili登录信息 */}
          {setname === 'bilibili' && (
            <div className="text-center">
              {biliUserinfo.isLogin ? (
                <div className="text-center">
                  <div className={`flex ${isMobile ? 'flex-col items-center gap-2' : 'items-center justify-center gap-2'} mb-4`}>
                    <img
                      className={`${isMobile ? 'w-8 h-8' : 'w-10 h-10'} rounded-full`}
                      src={biliUserinfo.face}
                    />
                    <span>{biliUserinfo.uname}</span>
                    {biliUserinfo.vipStatus === 1 && (
                      <Tag
                        color={biliUserinfo.vipType === 2 ? '#f50' : '#2db7f5'}
                      >
                        {biliUserinfo.vipType === 2 ? '年度大会员' : '大会员'}
                      </Tag>
                    )}
                  </div>
                  <Button type="primary" danger onClick={handleBiliLogout}>
                    注销登录
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-4 w-full max-w-md mx-auto p-4">
                  <div className="flex flex-col items-center gap-4">
                    <Button
                      disabled={!biliQrcodeChecked}
                      type="primary"
                      loading={biliQrcodeLoading}
                      onClick={handleBiliQrcode}
                    >
                      扫码登录
                    </Button>
                    <div className="flex items-center gap-2">
                      <Checkbox
                        checked={biliQrcodeChecked}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setShowDisclaimerModal(true);
                          } else {
                            setBiliQrcodeChecked(false);
                          }
                        }}
                      />
                      <span
                        className="cursor-pointer text-sm"
                        onClick={() => setShowDisclaimerModal(true)}
                      >
                        我已阅读并同意免责声明
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </Form>
      </Modal>
      <Modal
        title="bilibili扫码登录"
        open={biliLoginOpen}
        footer={null}
        onCancel={() => setBiliLoginOpen(false)}
        width={isMobile ? '90%' : '400px'}
        centered
      >
        <div className="text-center">
          <div className={`relative ${isMobile ? 'w-[150px] h-[150px]' : 'w-[200px] h-[200px]'} mx-auto mb-3`}>
            <QRCodeCanvas
              value={biliQrcode.url}
              size={isMobile ? 150 : 200}
              fgColor="#000"
              level="M"
            />

            {biliQrcodeStatus === 'expire' && (
              <div
                className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 cursor-pointer text-neutral-100"
                onClick={handleBiliQrcode}
              >
                二维码已失效
                <br />
                点击重新获取
              </div>
            )}
            {biliQrcodeStatus === 'mobileConfirm' && (
              <div className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 text-neutral-100">
                已扫描，请在
                <br />
                手机上确认登录
              </div>
            )}
            {biliQrcodeStatus === 'error' && (
              <div
                className="absolute left-0 top-0 w-full h-full p-3 flex items-center justify-center bg-black/80 cursor-pointer text-neutral-100"
                onClick={handleBiliQrcode}
              >
                轮询失败
                <br />
                点击重新获取
              </div>
            )}
          </div>
          <div className={`mb-3 ${isMobile ? 'text-sm px-2' : ''}`}>请使用Bilibili手机客户端扫描二维码</div>
          <Button type="primary" danger onClick={cancelBiliLogin}>
            取消登录
          </Button>
        </div>
      </Modal>
      <Modal
        title="免责声明"
        open={showDisclaimerModal}
        onOk={() => {
          setBiliQrcodeChecked(true)
          setShowDisclaimerModal(false)
        }}
        onCancel={() => setShowDisclaimerModal(false)}
        okText="同意"
        cancelText="取消"
      >
        <div className="text-sm text-left">
          登录接口由{' '}
          <a
            href="https://github.com/SocialSisterYi/bilibili-API-collect"
            target="_blank"
            rel="noopener noreferrer"
          >
            bilibili-API-collect
          </a>{' '}
          提供，为Blibili官方非公开接口。
          您的登录凭据将加密存储在您自己的数据库中。登录行为属用户个人行为，通过该登录获取数据同等于使用您的账号获取，由登录用户自行承担相关责任，与本工具无关。使用本接口登录等同于认同该声明。
        </div>
      </Modal>

      {/* 版本日志弹窗 */}
      <Modal
        title={changelogModal.title}
        open={changelogModal.open}
        onCancel={() => setChangelogModal({ open: false, title: '', content: '' })}
        footer={null}
        width={isMobile ? '95%' : 520}
        centered
      >
        <div className="max-h-[60vh] overflow-y-auto">
          {changelogModal.content ? (
            <div className="text-sm leading-relaxed">
              <ReactMarkdown
                components={{
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:text-blue-600 hover:underline">
                      {children}
                    </a>
                  ),
                  p: ({ children }) => <p className="my-1">{children}</p>,
                  ul: ({ children }) => <ul className="list-disc list-inside my-1 space-y-0.5">{children}</ul>,
                  ol: ({ children }) => <ol className="list-decimal list-inside my-1 space-y-0.5">{children}</ol>,
                  li: ({ children }) => <li className="ml-2">{children}</li>,
                  code: ({ children }) => (
                    <code className="bg-gray-200 dark:bg-gray-700 px-1 py-0.5 rounded text-sm font-mono">{children}</code>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-4 border-blue-400 pl-3 py-1 my-2 bg-blue-50 dark:bg-blue-900/20 rounded-r text-sm">
                      {children}
                    </blockquote>
                  ),
                  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                }}
              >
                {changelogModal.content
                  ? changelogModal.content.replace(/\r\n/g, '\n')
                  : ''}
              </ReactMarkdown>
            </div>
          ) : (
            <Typography.Text type="secondary">暂无版本日志</Typography.Text>
          )}
        </div>
      </Modal>

      <Modal
        title={<><RobotOutlined /> AI 正则生成助手</>}
        open={aiRegexModalOpen}
        onCancel={() => { setAiRegexModalOpen(false); setAiRegexResult('') }}
        footer={null}
        destroyOnClose
        zIndex={1010}
      >
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-600 mb-2">
              用自然语言描述你想过滤的分集标题，AI 会帮你生成对应的正则表达式。
            </div>
            <Input.TextArea
              value={aiRegexDesc}
              onChange={e => setAiRegexDesc(e.target.value)}
              placeholder="例如：过滤掉包含 预告、花絮、特典、PV 的分集"
              rows={3}
              onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleAiGenerate() } }}
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={aiRegexLoading}
              onClick={handleAiGenerate}
            >
              生成
            </Button>
          </div>
          {aiRegexResult && (
            <div>
              <div className="text-sm text-gray-600 mb-1">生成结果：</div>
              <div className="bg-gray-50 border rounded p-3 font-mono text-sm break-all">
                {aiRegexResult}
              </div>
              <div className="flex justify-end mt-3">
                <Space>
                  <Button onClick={() => setAiRegexResult('')}>清除</Button>
                  <Button type="primary" onClick={handleApplyAiRegex}>
                    应用规则
                  </Button>
                </Space>
              </div>
            </div>
          )}
        </div>
      </Modal>
    </div >
  )
}

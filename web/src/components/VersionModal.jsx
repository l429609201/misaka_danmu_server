import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Button, Tag, Spin, Badge, Typography, Divider, Alert, Card, Progress, Row, Col, Statistic, Switch } from 'antd'
import { SyncOutlined, RocketOutlined, CheckCircleOutlined, CloseCircleOutlined, HistoryOutlined, CloudServerOutlined, GithubOutlined } from '@ant-design/icons'
import { checkAppUpdate, getDockerStatus, restartService } from '../apis'
import { useMessage } from '../MessageContext'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import Cookies from 'js-cookie'
import ReleaseHistoryModal from './ReleaseHistoryModal'
import { MyIcon } from './MyIcon'
import ReactMarkdown from 'react-markdown'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

const { Text, Title } = Typography

/**
 * 预处理 GitHub Release 的 changelog 文本，使 ReactMarkdown 能正确渲染。
 * 仅统一换行符为 \n，不做额外的换行替换，以保留 Markdown 列表等结构的正确解析。
 */
const preprocessChangelog = (text) => {
  if (!text) return text
  return text.replace(/\r\n/g, '\n')
}

// Markdown 渲染样式
const markdownComponents = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-primary)' }} className="hover:underline">
      {children}
    </a>
  ),
  p: ({ children }) => <p className="my-1">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-inside my-1 space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside my-1 space-y-0.5">{children}</ol>,
  li: ({ children }) => <li className="ml-2">{children}</li>,
  code: ({ children }) => (
    <code style={{ backgroundColor: 'var(--color-hover)' }} className="px-1 py-0.5 rounded text-sm font-mono">{children}</code>
  ),
  blockquote: ({ children }) => (
    <blockquote style={{ borderColor: 'var(--color-primary)', backgroundColor: 'var(--color-hover)' }} className="border-l-4 pl-3 py-1 my-2 rounded-r text-sm">
      {children}
    </blockquote>
  ),
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
}

export const VersionModal = ({ open, onClose, currentVersion }) => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const [loading, setLoading] = useState(false)
  const [updateInfo, setUpdateInfo] = useState(null)
  const [dockerStatus, setDockerStatus] = useState(null)
  const [dockerStats, setDockerStats] = useState(null)
  const [updating, setUpdating] = useState(false)
  const [updateLogs, setUpdateLogs] = useState([])
  const [updateComplete, setUpdateComplete] = useState(false)
  const [updateUpToDate, setUpdateUpToDate] = useState(false)
  const [updateError, setUpdateError] = useState(null)
  const [updateProgress, setUpdateProgress] = useState(0)
  const [countdown, setCountdown] = useState(null)
  const [releaseHistoryOpen, setReleaseHistoryOpen] = useState(false)
  const [useGithubSource, setUseGithubSource] = useState(() => {
    return localStorage.getItem('updateSource') === 'github'
  })
  const statsAbortController = useRef(null)
  const messageApi = useMessage()

  // 启动 Docker Stats SSE 连接
  const startStatsSSE = useCallback(() => {
    const token = Cookies.get('danmu_token')
    if (!token) return

    // 清理之前的连接
    if (statsAbortController.current) {
      statsAbortController.current.abort()
    }
    statsAbortController.current = new AbortController()

    fetchEventSource('/api/ui/docker/stats', {
      signal: statsAbortController.current.signal,
      headers: {
        Authorization: `Bearer ${token}`,
      },
      onopen: async response => {
        if (!response.ok) {
          console.error('Docker Stats SSE 连接失败:', response.status)
        }
      },
      onmessage: event => {
        try {
          const data = JSON.parse(event.data)
          setDockerStats(data)
        } catch (e) {
          console.error('解析 Docker Stats 数据失败:', e)
        }
      },
      onerror: error => {
        console.error('Docker Stats SSE 错误:', error)
      },
    }).catch(error => {
      if (error.name !== 'AbortError') {
        console.error('Docker Stats SSE 流错误:', error)
      }
    })
  }, [])

  // 停止 Docker Stats SSE 连接
  const stopStatsSSE = useCallback(() => {
    if (statsAbortController.current) {
      statsAbortController.current.abort()
      statsAbortController.current = null
    }
  }, [])

  // 加载更新信息和 Docker 状态
  useEffect(() => {
    if (open) {
      loadData()
    } else {
      // 关闭弹窗时停止 SSE
      stopStatsSSE()
    }
    return () => stopStatsSSE()
  }, [open, stopStatsSSE])

  const loadData = async () => {
    setLoading(true)
    try {
      const [updateRes, dockerRes] = await Promise.all([
        checkAppUpdate(),
        getDockerStatus()
      ])
      setUpdateInfo(updateRes.data)
      setDockerStatus(dockerRes.data)

      // 如果 Docker 已连接，启动 SSE 获取实时统计信息
      if (dockerRes.data?.socketAvailable) {
        startStatsSSE()
      }
    } catch (error) {
      console.error('加载数据失败:', error)
    } finally {
      setLoading(false)
    }
  }



  // 开始更新
  const handleUpdate = async () => {
    if (!dockerStatus?.canUpdate) {
      messageApi.error(t('versionModal.socketUnavailable'))
      return
    }

    setUpdating(true)
    setUpdateLogs([])
    setUpdateComplete(false)
    setUpdateUpToDate(false)
    setUpdateError(null)
    setUpdateProgress(0)
    setCountdown(null)

    const token = Cookies.get('danmu_token')

    try {
      const source = useGithubSource ? 'github' : 'docker'
      await fetchEventSource(`/api/ui/update/stream?source=${source}`, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
        onmessage(event) {
          try {
            const data = JSON.parse(event.data)
            setUpdateLogs(prev => [...prev, data.status])

            // 更新进度条
            if (data.progress != null) {
              setUpdateProgress(data.progress)
            }

            if (data.event === 'DONE') {
              setUpdateComplete(true)
              setUpdating(false)
              setUpdateProgress(100)
              // 轮询检测服务恢复，而非固定倒计时
              const waitForRestart = async () => {
                const maxWait = 120
                let waited = 0
                setCountdown(-1) // 标记正在等待重启

                // 辅助：替换最后一行日志（避免刷屏）
                const updateLastLog = (msg) => {
                  setUpdateLogs(prev => {
                    const copy = [...prev]
                    if (copy.length > 0 && (copy[copy.length - 1].startsWith('⏳') || copy[copy.length - 1].startsWith('✅') || copy[copy.length - 1].startsWith('⚠️'))) {
                      copy[copy.length - 1] = msg
                    } else {
                      copy.push(msg)
                    }
                    return copy
                  })
                }

                // 第一阶段：等待服务停止（最多30秒）
                for (let i = 0; i < 30; i++) {
                  waited++
                  updateLastLog(`⏳ ${t('versionModal.waitContainerStopping', { seconds: waited })}`)
                  try {
                    const res = await fetch('/api/ui/version', { signal: AbortSignal.timeout(3000) })
                    if (!res.ok) break
                  } catch {
                    break // 服务已停止
                  }
                  await new Promise(r => setTimeout(r, 1000))
                }

                // 第二阶段：等待服务恢复
                for (let i = 0; i < 60; i++) {
                  waited++
                  setCountdown(maxWait - waited)
                  updateLastLog(`⏳ ${t('versionModal.waitServiceRecovering', { seconds: waited })}`)
                  try {
                    const res = await fetch('/api/ui/version', { signal: AbortSignal.timeout(3000) })
                    if (res.ok) {
                      updateLastLog(`✅ ${t('versionModal.serviceRecovered')}`)
                      await new Promise(r => setTimeout(r, 500))
                      window.location.reload()
                      return
                    }
                  } catch { /* 还没恢复 */ }
                  await new Promise(r => setTimeout(r, 2000))
                }

                // 超时
                updateLastLog(`⚠️ ${t('versionModal.waitTimeout')}`)
                setCountdown(null)
              }
              waitForRestart()
            } else if (data.event === 'UP_TO_DATE') {
              setUpdateUpToDate(true)
              setUpdating(false)
              setUpdateProgress(100)
            } else if (data.event === 'ERROR') {
              setUpdateError(data.status)
              setUpdating(false)
            }
          } catch (e) {
            console.error('解析更新消息失败:', e)
          }
        },
        onerror(err) {
          console.error('更新流错误:', err)
          setUpdateError(t('versionModal.connectionInterrupted'))
          setUpdating(false)
        },
        onclose() {
          setUpdating(false)
        }
      })
    } catch (error) {
      console.error('更新失败:', error)
      setUpdateError(error.message || t('versionModal.updateFailed'))
      setUpdating(false)
    }
  }

  // 重启服务
  const handleRestart = async () => {
    try {
      const res = await restartService()
      messageApi.success(res.data.message)
      onClose()
    } catch (error) {
      messageApi.error(t('versionModal.restartFailed') + ': ' + (error.message || t('versionModal.unknownError')))
    }
  }

  // 渲染更新日志
  const renderChangelog = () => {
    if (!updateInfo?.changelog) return null

    return (
      <div className={isMobile ? 'flex-1 min-h-0 overflow-y-auto rounded-lg p-4 mt-2' : 'max-h-[300px] overflow-y-auto rounded-lg p-4 mt-4'} style={{ backgroundColor: 'var(--color-hover)' }}>
        <Title level={5}>{t('versionModal.changelog')}</Title>
        <div className="text-sm">
          <ReactMarkdown components={markdownComponents}>
            {preprocessChangelog(updateInfo.changelog)}
          </ReactMarkdown>
        </div>
      </div>
    )
  }

  return (
    <Modal
      title={t('versionModal.title')}
      open={open}
      onCancel={onClose}
      footer={null}
      width={isMobile ? '95%' : 600}
      styles={{ body: { maxHeight: isMobile ? 'calc(100vh - 120px)' : 'none', overflow: isMobile ? 'hidden' : 'visible', display: 'flex', flexDirection: 'column' } }}
    >
      <Spin spinning={loading}>
        <div className={isMobile ? 'flex flex-col' : 'space-y-4'} style={isMobile ? { maxHeight: 'calc(100vh - 160px)' } : {}}>
          {/* 当前版本 */}
          <div className="flex items-center justify-between">
            <Text>{t('versionModal.currentVersion')}</Text>
            <Tag color="blue">{currentVersion}</Tag>
          </div>

          {/* 最新版本 */}
          {updateInfo && (
            <div className="flex items-center justify-between">
              <Text>{t('versionModal.latestVersion')}</Text>
              <div className="flex items-center gap-2">
                {updateInfo.hasUpdate ? (
                  <Tag color="green">{updateInfo.latestVersion}</Tag>
                ) : (
                  <Tag>{updateInfo.latestVersion || t('versionModal.checking')}</Tag>
                )}
                {updateInfo.hasUpdate && <Badge status="processing" text={t('versionModal.hasNewVersion')} />}
              </div>
            </div>
          )}

          {/* Docker 状态 */}
          <Divider />
          <div className="flex items-center justify-between">
            <Text>{t('versionModal.dockerStatus')}</Text>
            {dockerStatus?.socketAvailable ? (
              <Tag icon={<CheckCircleOutlined />} color="success">{t('versionModal.connected')}</Tag>
            ) : (
              <Tag icon={<CloseCircleOutlined />} color="default">{t('versionModal.disconnected')}</Tag>
            )}
          </div>

          {!dockerStatus?.socketAvailable && (
            <Alert
              type="info"
              showIcon
              message={t('versionModal.socketNotMapped')}
              description={t('versionModal.socketNotMappedDesc')}
            />
          )}

          {/* 容器资源统计卡片 */}
          {dockerStats?.available && (
            <Card
              size="small"
              className="!mt-4"
              title={
                <div className="flex items-center gap-2">
                  <CloudServerOutlined />
                  <span>{dockerStats.containerName || t('versionModal.containerStatus')}</span>
                  <Tag color={dockerStats.status === 'running' ? 'success' : 'warning'} className="!ml-2">
                    {{ running: t('versionModal.statusRunning'), exited: t('versionModal.statusExited'), paused: t('versionModal.statusPaused'), restarting: t('versionModal.statusRestarting'), created: t('versionModal.statusCreated'), dead: t('versionModal.statusDead') }[dockerStats.status] || dockerStats.status}
                  </Tag>
                </div>
              }
            >
              <Row gutter={[16, 12]}>
                <Col span={12}>
                  <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>{t('versionModal.cpuUsage')}</div>
                  <Progress
                    percent={dockerStats.cpu?.percent || 0}
                    size="small"
                    status={dockerStats.cpu?.percent > 80 ? 'exception' : 'normal'}
                    format={(percent) => `${percent}%`}
                  />
                </Col>
                <Col span={12}>
                  <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>{t('versionModal.memoryUsage')} ({dockerStats.memory?.limitFormatted || '-'})</div>
                  <Progress
                    percent={dockerStats.memory?.percent || 0}
                    size="small"
                    status={dockerStats.memory?.percent > 80 ? 'exception' : 'normal'}
                    format={() => `${dockerStats.memory?.usageFormatted || '0 B'}`}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={<span>{t('versionModal.networkRx')} <span className="text-green-500">↓{dockerStats.network?.rxRateFormatted || '0 B/s'}</span></span>}
                    value={dockerStats.network?.rxFormatted || '0 B'}
                    valueStyle={{ fontSize: '14px' }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={<span>{t('versionModal.networkTx')} <span className="text-blue-500">↑{dockerStats.network?.txRateFormatted || '0 B/s'}</span></span>}
                    value={dockerStats.network?.txFormatted || '0 B'}
                    valueStyle={{ fontSize: '14px' }}
                  />
                </Col>
              </Row>
            </Card>
          )}

          {/* 更新日志 */}
          {renderChangelog()}

          {/* 更新进度 */}
          {(updating || updateLogs.length > 0) && (
            <div className="mt-4">
              <Divider>{t('versionModal.updateProgress')}</Divider>
              {/* 进度条 */}
              {(updating || updateProgress > 0) && (
                <Progress
                  percent={updateProgress}
                  status={updateError ? 'exception' : (updateComplete || updateUpToDate) ? 'success' : 'active'}
                  strokeWidth={10}
                  className="mb-3"
                />
              )}
              <div
                className="rounded-lg p-4 max-h-[200px] overflow-y-auto font-mono text-sm"
                style={{
                  backgroundColor: 'var(--color-hover, #f5f5f5)',
                  color: 'var(--color-text, #333)',
                }}
              >
                {updateLogs.map((log, index) => (
                  <div key={index}>
                    {log.startsWith('⏳') ? (
                      <><span className="inline-block mr-1 animate-spin">⏳</span>{log.slice(1)}</>
                    ) : log}
                  </div>
                ))}
                {updating && <Spin size="small" className="ml-2" />}
              </div>
            </div>
          )}

          {/* 更新结果：有新版本已更新 */}
          {updateComplete && (
            <Alert
              type="success"
              showIcon
              message={t('versionModal.updateComplete')}
              description={
                countdown === -1
                  ? t('versionModal.waitingContainerStop')
                  : countdown != null && countdown > 0
                    ? t('versionModal.waitingServiceRecover', { seconds: countdown })
                    : t('versionModal.updateDoneAutoRefresh')
              }
              className="mt-3"
            />
          )}

          {/* 更新结果：已是最新 */}
          {updateUpToDate && (
            <Alert
              type="info"
              showIcon
              message={t('versionModal.noUpdate')}
              description={t('versionModal.noUpdateDesc')}
              className="mt-3"
            />
          )}

          {updateError && (
            <Alert
              type="error"
              showIcon
              message={t('versionModal.updateFailed')}
              description={updateError}
            />
          )}

          {/* 操作按钮 */}
          <Divider className="!my-2" />
          {isMobile ? (
            <div className="flex flex-col gap-2">
              {/* 第一行：更新日志 + 刷新 + Release */}
              <div className="flex gap-2 items-center">
                <Button
                  onClick={() => setReleaseHistoryOpen(true)}
                  icon={<HistoryOutlined />}
                  size="small"
                >
                  {t('versionModal.changelog')}
                </Button>
                <Button onClick={() => loadData()} icon={<SyncOutlined />} size="small">
                  {t('common.refresh')}
                </Button>
                {updateInfo?.releaseUrl && (
                  <Button
                    href={updateInfo.releaseUrl}
                    target="_blank"
                    size="small"
                  >
                    Release
                  </Button>
                )}
              </div>
              {/* 第二行：左边切换开关 + 右边检查并更新 */}
              {dockerStatus?.canUpdate && (
                <div className="flex items-center justify-between">
                  <Switch
                    checked={useGithubSource}
                    checkedChildren={<><GithubOutlined /> GitHub</>}
                    unCheckedChildren={<><MyIcon icon="Docker2" size={14} className="mr-0.5 align-middle" /> Docker</>}
                    onChange={v => {
                      setUseGithubSource(v)
                      localStorage.setItem('updateSource', v ? 'github' : 'docker')
                    }}
                  />
                  <Button
                    type="primary"
                    icon={<RocketOutlined />}
                    onClick={handleUpdate}
                    loading={updating}
                    disabled={updateComplete}
                    size="small"
                  >
                    {updateInfo?.hasUpdate ? t('versionModal.updateAndRestart') : t('versionModal.checkAndUpdate')}
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-2">
                <Button
                  onClick={() => setReleaseHistoryOpen(true)}
                  icon={<HistoryOutlined />}
                >
                  {t('versionModal.changelog')}
                </Button>
                {dockerStatus?.canUpdate && (
                  <Switch
                    checked={useGithubSource}
                    checkedChildren={<><GithubOutlined /> GitHub</>}
                    unCheckedChildren={<><MyIcon icon="Docker2" size={14} className="mr-0.5 align-middle" /> Docker</>}
                    onChange={v => {
                      setUseGithubSource(v)
                      localStorage.setItem('updateSource', v ? 'github' : 'docker')
                    }}
                  />
                )}
              </div>
              <div className="flex gap-2">
                <Button onClick={() => loadData()} icon={<SyncOutlined />}>
                  {t('common.refresh')}
                </Button>
                {dockerStatus?.canUpdate && (
                  <Button
                    type="primary"
                    icon={<RocketOutlined />}
                    onClick={handleUpdate}
                    loading={updating}
                    disabled={updateComplete}
                  >
                    {updateInfo?.hasUpdate ? t('versionModal.updateAndRestart') : t('versionModal.checkAndUpdate')}
                  </Button>
                )}
                {updateInfo?.releaseUrl && (
                  <Button
                    href={updateInfo.releaseUrl}
                    target="_blank"
                  >
                    Release
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      </Spin>

      {/* 更新日志弹窗 */}
      <ReleaseHistoryModal
        open={releaseHistoryOpen}
        onClose={() => setReleaseHistoryOpen(false)}
      />
    </Modal>
  )
}

export default VersionModal


import { useState, useEffect, useRef, useCallback } from 'react'
import { Modal, Button, Tag, Spin, Badge, Typography, Divider, Alert, Card, Progress, Row, Col, Statistic } from 'antd'
import { SyncOutlined, RocketOutlined, CheckCircleOutlined, CloseCircleOutlined, HistoryOutlined, CloudServerOutlined } from '@ant-design/icons'
import { checkAppUpdate, getDockerStatus, restartService } from '../apis'
import { useMessage } from '../MessageContext'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import Cookies from 'js-cookie'
import ReleaseHistoryModal from './ReleaseHistoryModal'
import ReactMarkdown from 'react-markdown'

const { Text, Title } = Typography

/**
 * 预处理 GitHub Release 的 changelog 文本，使 ReactMarkdown 能正确渲染换行。
 * GitHub Release body 使用 \r\n 单换行，Markdown 中单换行不会产生实际换行效果，
 * 需要转换为双换行（段落分隔）才能正确显示。
 */
const preprocessChangelog = (text) => {
  if (!text) return text
  return text
    .replace(/\r\n/g, '\n')       // 统一换行符
    .replace(/\n(?!\n)/g, '\n\n') // 单换行 → 双换行（保留已有的双换行）
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
  const [loading, setLoading] = useState(false)
  const [updateInfo, setUpdateInfo] = useState(null)
  const [dockerStatus, setDockerStatus] = useState(null)
  const [dockerStats, setDockerStats] = useState(null)
  const [updating, setUpdating] = useState(false)
  const [updateLogs, setUpdateLogs] = useState([])
  const [updateComplete, setUpdateComplete] = useState(false)
  const [updateError, setUpdateError] = useState(null)
  const [releaseHistoryOpen, setReleaseHistoryOpen] = useState(false)
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
      messageApi.error('Docker 套接字 不可用，无法执行更新')
      return
    }

    setUpdating(true)
    setUpdateLogs([])
    setUpdateComplete(false)
    setUpdateError(null)

    const token = Cookies.get('danmu_token')
    
    try {
      await fetchEventSource('/api/ui/update/stream', {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
        },
        onmessage(event) {
          try {
            const data = JSON.parse(event.data)
            setUpdateLogs(prev => [...prev, data.status])
            
            if (data.event === 'DONE' || data.event === 'UP_TO_DATE') {
              setUpdateComplete(true)
              setUpdating(false)
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
          setUpdateError('连接中断')
          setUpdating(false)
        },
        onclose() {
          setUpdating(false)
        }
      })
    } catch (error) {
      console.error('更新失败:', error)
      setUpdateError(error.message || '更新失败')
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
      messageApi.error('重启失败: ' + (error.message || '未知错误'))
    }
  }

  // 渲染更新日志
  const renderChangelog = () => {
    if (!updateInfo?.changelog) return null

    return (
      <div className="max-h-[300px] overflow-y-auto rounded-lg p-4 mt-4" style={{ backgroundColor: 'var(--color-hover)' }}>
        <Title level={5}>更新日志</Title>
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
      title="版本信息"
      open={open}
      onCancel={onClose}
      footer={null}
      width={600}
    >
      <Spin spinning={loading}>
        <div className="space-y-4">
          {/* 当前版本 */}
          <div className="flex items-center justify-between">
            <Text>当前版本</Text>
            <Tag color="blue">{currentVersion}</Tag>
          </div>

          {/* 最新版本 */}
          {updateInfo && (
            <div className="flex items-center justify-between">
              <Text>最新版本</Text>
              <div className="flex items-center gap-2">
                {updateInfo.hasUpdate ? (
                  <Tag color="green">{updateInfo.latestVersion}</Tag>
                ) : (
                  <Tag>{updateInfo.latestVersion || '检查中...'}</Tag>
                )}
                {updateInfo.hasUpdate && <Badge status="processing" text="有新版本" />}
              </div>
            </div>
          )}

          {/* Docker 状态 */}
          <Divider />
          <div className="flex items-center justify-between">
            <Text>Docker 状态</Text>
            {dockerStatus?.socketAvailable ? (
              <Tag icon={<CheckCircleOutlined />} color="success">已连接</Tag>
            ) : (
              <Tag icon={<CloseCircleOutlined />} color="default">未连接</Tag>
            )}
          </div>

          {!dockerStatus?.socketAvailable && (
            <Alert
              type="info"
              showIcon
              message="Docker 套接字 未映射"
              description="如需使用一键更新功能，请在 docker-compose.yml 中添加 /var/run/docker.sock:/var/run/docker.sock 路径映射"
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
                  <span>{dockerStats.containerName || '容器状态'}</span>
                  <Tag color={dockerStats.status === 'running' ? 'success' : 'warning'} className="!ml-2">
                    {{ running: '运行中', exited: '已停止', paused: '已暂停', restarting: '重启中', created: '已创建', dead: '已终止' }[dockerStats.status] || dockerStats.status}
                  </Tag>
                </div>
              }
            >
              <Row gutter={[16, 12]}>
                <Col span={12}>
                  <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>CPU 使用率</div>
                  <Progress
                    percent={dockerStats.cpu?.percent || 0}
                    size="small"
                    status={dockerStats.cpu?.percent > 80 ? 'exception' : 'normal'}
                    format={(percent) => `${percent}%`}
                  />
                </Col>
                <Col span={12}>
                  <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>内存使用 ({dockerStats.memory?.limitFormatted || '-'})</div>
                  <Progress
                    percent={dockerStats.memory?.percent || 0}
                    size="small"
                    status={dockerStats.memory?.percent > 80 ? 'exception' : 'normal'}
                    format={() => `${dockerStats.memory?.usageFormatted || '0 B'}`}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={<span>网络接收 <span className="text-green-500">↓{dockerStats.network?.rxRateFormatted || '0 B/s'}</span></span>}
                    value={dockerStats.network?.rxFormatted || '0 B'}
                    valueStyle={{ fontSize: '14px' }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={<span>网络发送 <span className="text-blue-500">↑{dockerStats.network?.txRateFormatted || '0 B/s'}</span></span>}
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
              <Divider>更新进度</Divider>
              <div className="bg-gray-900 text-green-400 rounded-lg p-4 max-h-[200px] overflow-y-auto font-mono text-sm">
                {updateLogs.map((log, index) => (
                  <div key={index}>{log}</div>
                ))}
                {updating && <Spin size="small" className="ml-2" />}
              </div>
            </div>
          )}

          {/* 更新结果 */}
          {updateComplete && (
            <Alert
              type="success"
              showIcon
              message="更新完成"
              description="容器将在后台重启，请稍后刷新页面"
            />
          )}

          {updateError && (
            <Alert
              type="error"
              showIcon
              message="更新失败"
              description={updateError}
            />
          )}

          {/* 操作按钮 */}
          <Divider />
          <div className="flex justify-between">
            <Button
              onClick={() => setReleaseHistoryOpen(true)}
              icon={<HistoryOutlined />}
            >
              更新日志
            </Button>
            <div className="flex gap-2">
              <Button onClick={() => loadData()} icon={<SyncOutlined />}>
                刷新
              </Button>
              {updateInfo?.hasUpdate && dockerStatus?.canUpdate && (
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  onClick={handleUpdate}
                  loading={updating}
                  disabled={updateComplete}
                >
                  开始更新
                </Button>
              )}
              {updateInfo?.releaseUrl && (
                <Button
                  href={updateInfo.releaseUrl}
                  target="_blank"
                >
                  查看 Release
                </Button>
              )}
            </div>
          </div>
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


import { useEffect, useRef, useState } from 'react'
import { Modal, Drawer, Button, Tooltip, message, Empty, Switch, Card } from 'antd'
import { CopyOutlined, ExportOutlined, ClearOutlined, VerticalAlignBottomOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

export default function RealtimeLogModal({ open, onClose }) {
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const abortRef = useRef(null)
  const containerRef = useRef(null)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  useEffect(() => {
    if (!open) return
    const token = Cookies.get('danmu_token')
    if (!token) { messageApi.error('未登录'); return }

    const ctrl = new AbortController()
    abortRef.current = ctrl

    fetchEventSource('/api/ui/logs/stream', {
      signal: ctrl.signal,
      headers: { Authorization: `Bearer ${token}` },
      onopen: async (res) => { if (res.ok) setConnected(true); else throw new Error(`连接失败: ${res.status}`) },
      onmessage: (event) => {
        const msg = event.data.trim()
        if (!msg) return
        setLogs(prev => [msg, ...prev].slice(0, 200))
      },
      onerror: (err) => { setConnected(false); throw err },
    }).catch(e => { if (e.name !== 'AbortError') console.error('SSE错误:', e) })

    return () => { ctrl.abort(); setConnected(false) }
  }, [open])

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = 0
    }
  }, [logs, autoScroll])

  const handleClose = () => {
    abortRef.current?.abort()
    setLogs([])
    setConnected(false)
    onClose()
  }

  const exportLogs = () => {
    const blob = new Blob([logs.slice().reverse().join('\r\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `realtime-logs-${dayjs().format('YYYY-MM-DD_HH-mm-ss')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const copyLogLine = async (logText) => {
    try {
      await navigator.clipboard.writeText(logText)
      messageApi.success('日志已复制到剪贴板')
    } catch {
      const textArea = document.createElement('textarea')
      textArea.value = logText
      document.body.appendChild(textArea)
      textArea.select()
      try { document.execCommand('copy'); messageApi.success('日志已复制到剪贴板') }
      catch { messageApi.error('复制失败') }
      document.body.removeChild(textArea)
    }
  }

  const titleNode = (
    <div className="flex items-center gap-2">
      <span>实时日志</span>
      <span className={`inline-block w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-400'}`} />
      <span className="text-xs text-gray-400">{connected ? '已连接' : '未连接'}</span>
    </div>
  )

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title="清空"><Button size="small" type="text" icon={<ClearOutlined />} onClick={() => setLogs([])} /></Tooltip>
      <Tooltip title="导出"><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
      <Tooltip title="滚动到顶部">
        <Button size="small" type="text" icon={<VerticalAlignBottomOutlined className="rotate-180" />} onClick={() => {
          if (containerRef.current) containerRef.current.scrollTop = 0
        }} />
      </Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">自动滚动</span>
        <Switch size="small" checked={autoScroll} onChange={setAutoScroll} />
      </div>
      {!isMobile && (
        <div className="flex gap-2">
          <Tooltip title="清空"><Button icon={<ClearOutlined />} onClick={() => setLogs([])} /></Tooltip>
          <Tooltip title="导出"><Button icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
          <Tooltip title="滚动到顶部">
            <Button icon={<VerticalAlignBottomOutlined className="rotate-180" />} onClick={() => {
              if (containerRef.current) containerRef.current.scrollTop = 0
            }} />
          </Tooltip>
        </div>
      )}
    </div>
  )

  const logContent = (
    <Card className={isMobile ? 'flex-1 overflow-hidden flex flex-col' : ''} styles={{ body: { padding: isMobile ? 8 : 12, ...(isMobile ? { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' } : {}) } }}>
      <div
        ref={containerRef}
        className={`${isMobile ? 'flex-1 overflow-y-auto overflow-x-hidden' : 'max-h-[60vh] overflow-y-auto overflow-x-hidden'}`}
      >
        {logs.length === 0 ? (
          <div className="flex items-center justify-center" style={{ height: isMobile ? '40vh' : '40vh' }}>
            <Empty description={<span className="text-gray-400">等待日志...</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : (
          logs.map((line, i) => (
            <div
              key={i}
              className={`my-1 p-2 rounded group ${isMobile ? 'text-xs' : 'text-sm'} bg-base-hover border-l-2 border-primary hover:bg-base-hover-hover transition-colors`}
            >
              <div className="flex items-start justify-between gap-2">
                <pre className="whitespace-pre-wrap break-words m-0 font-mono flex-1 min-w-0">
                  {line}
                </pre>
                <Button
                  type="text"
                  size="small"
                  icon={<CopyOutlined />}
                  className={`shrink-0 opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'opacity-60' : ''}`}
                  onClick={(e) => { e.stopPropagation(); copyLogLine(line) }}
                  title="复制日志"
                />
              </div>
            </div>
          ))
        )}
      </div>
    </Card>
  )

  return (
    <>
      {contextHolder}
      {isMobile ? (
        <Drawer
          title={titleNode}
          placement="bottom"
          height="85%"
          open={open}
          onClose={handleClose}
          extra={actionButtons}
          footer={footerNode}
          destroyOnClose
          styles={{ body: { overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: 12 } }}
        >
          {logContent}
        </Drawer>
      ) : (
        <Modal
          title={titleNode}
          open={open}
          onCancel={handleClose}
          width="90%"
          style={{ maxWidth: 900, top: 40 }}
          footer={footerNode}
          destroyOnClose
        >
          {logContent}
        </Modal>
      )}
    </>
  )
}


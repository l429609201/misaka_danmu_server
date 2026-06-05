import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Drawer, Button, Tooltip, message, Empty, Switch, Card, Segmented, Input } from 'antd'
import { CopyOutlined, ExportOutlined, ClearOutlined, VerticalAlignBottomOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

export default function RealtimeLogModal({ open, onClose }) {
  const { t } = useTranslation()
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const [autoScroll, setAutoScroll] = useState(true)
  const [logLevel, setLogLevel] = useState('INFO')
  const [searchText, setSearchText] = useState('')
  const abortRef = useRef(null)
  const containerRef = useRef(null)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  useEffect(() => {
    if (!open) return
    const token = Cookies.get('danmu_token')
    if (!token) { messageApi.error(t('realtimeLog.notLoggedIn')); return }

    const ctrl = new AbortController()
    abortRef.current = ctrl

    fetchEventSource('/api/ui/logs/stream', {
      signal: ctrl.signal,
      headers: { Authorization: `Bearer ${token}` },
      onopen: async (res) => { if (res.ok) setConnected(true); else throw new Error(`${t('realtimeLog.connectFailed')}: ${res.status}`) },
      onmessage: (event) => {
        const msg = event.data.trim()
        if (!msg) return
        setLogs(prev => [...prev, msg].slice(-200))
      },
      onerror: (err) => { setConnected(false); throw err },
    }).catch(e => { if (e.name !== 'AbortError') console.error('SSE错误:', e) })

    return () => { ctrl.abort(); setConnected(false) }
  }, [open])

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleClose = () => {
    abortRef.current?.abort()
    setLogs([])
    setConnected(false)
    onClose()
  }

  const exportLogs = () => {
    const blob = new Blob([logs.join('\r\n')], { type: 'text/plain' })
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
      messageApi.success(t('realtimeLog.copied'))
    } catch {
      const textArea = document.createElement('textarea')
      textArea.value = logText
      document.body.appendChild(textArea)
      textArea.select()
      try { document.execCommand('copy'); messageApi.success(t('realtimeLog.copied')) }
      catch { messageApi.error(t('realtimeLog.copyFailed')) }
      document.body.removeChild(textArea)
    }
  }

  // 从一行日志文本中提取级别名称
  const getLineLevelName = (line) => {
    const m = line.match(/\[(DEBUG|INFO|WARNING|ERROR)\]/)
    return m ? m[1] : 'INFO'
  }

  // 各筛选级别包含的日志范围
  // INFO: INFO + ERROR
  // WARN: INFO + WARNING + ERROR
  // DEBUG: 全部
  const LEVEL_INCLUDES = {
    INFO:  new Set(['INFO', 'ERROR']),
    WARN:  new Set(['INFO', 'WARNING', 'ERROR']),
    DEBUG: new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
  }

  // 当前选中级别对应的包含集合
  const allowedLevels = LEVEL_INCLUDES[logLevel] || LEVEL_INCLUDES.INFO

  // 判断某行日志是否应该显示
  const isLevelAllowed = (line) => allowedLevels.has(getLineLevelName(line))

  // 根据选中级别过滤日志条目
  const filterLog = (entry) => {
    if (logLevel === 'DEBUG') return entry // DEBUG 模式显示全部

    const lines = entry.split('\n')
    // 检测是否为缓冲日志块（包含 ┌─── 或 └───）
    const isBlock = lines.some(l => l.includes('┌───') || l.includes('└───'))

    if (!isBlock) {
      // 普通单行/多行日志
      return isLevelAllowed(entry) ? entry : null
    }

    // 缓冲块：逐行过滤，保留 header/footer
    const filtered = lines.filter(l => {
      if (l.includes('┌───') || l.includes('└───') || l.trim() === '') return true
      return isLevelAllowed(l)
    })

    // 如果只剩 header 和 footer，隐藏整个块
    const contentLines = filtered.filter(l => !l.includes('┌───') && !l.includes('└───') && l.trim() !== '')
    return contentLines.length > 0 ? filtered.join('\n') : null
  }

  // 按级别返回边框色和背景色（INFO 走 className 默认）
  const getLevelColors = (line) => {
    const m = line.match(/\[(DEBUG|INFO|WARNING|ERROR)\]/)
    if (!m) return {}
    switch (m[1]) {
      case 'ERROR': return { border: '#ef4444', bg: 'rgba(239,68,68,0.06)' }
      case 'WARNING': return { border: '#f59e0b', bg: 'rgba(245,158,11,0.06)' }
      case 'DEBUG': return { border: '#1d4ed8', bg: 'rgba(29,78,216,0.06)' }
      default: return {}
    }
  }

  // 隐去日志文本中的级别标签
  const stripLevelTag = (text) => text.replace(/\s*\[(DEBUG|INFO|WARNING|ERROR)\]\s*/, ' ')

  // 关键词过滤（在级别过滤之上叠加）
  const filteredLogs = useMemo(() => {
    if (!searchText.trim()) return logs
    const kw = searchText.toLowerCase()
    return logs.filter(line => line.toLowerCase().includes(kw))
  }, [logs, searchText])

  // Segmented 选中项按级别变色（INFO 用 antd 默认样式）
  const segColor = { WARN: '#f59e0b', DEBUG: '#1d4ed8' }[logLevel]

  const titleNode = (
    <div className="flex items-center gap-2">
      <span>{t('realtimeLog.title')}</span>
      <span className={`inline-block w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-400'}`} />
      <span className="text-xs text-gray-400">{connected ? t('realtimeLog.connected') : t('realtimeLog.disconnected')}</span>
      {!isMobile && (
        <>
          <span className="text-gray-300 mx-1">|</span>
          {segColor && <style key={`seg-style-${logLevel}`}>{`.log-seg .ant-segmented-item-selected { background: ${segColor} !important; color: #fff !important; }`}</style>}
          <div className="log-seg">
            <Segmented
              size="small"
              options={['INFO', 'WARN', 'DEBUG']}
              value={logLevel}
              onChange={setLogLevel}
            />
          </div>
          <Input
            size="small"
            placeholder={t('realtimeLog.searchPlaceholder')}
            prefix={<SearchOutlined className="text-gray-400" />}
            allowClear
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            style={{ width: 180 }}
          />
        </>
      )}
    </div>
  )

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title={t('realtimeLog.clear')}><Button size="small" type="text" icon={<ClearOutlined />} onClick={() => setLogs([])} /></Tooltip>
      <Tooltip title={t('realtimeLog.export')}><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
      <Tooltip title={t('realtimeLog.scrollBottom')}>
        <Button size="small" type="text" icon={<VerticalAlignBottomOutlined />} onClick={() => {
          if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight
        }} />
      </Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">{t('realtimeLog.autoScroll')}</span>
        <Switch size="small" checked={autoScroll} onChange={setAutoScroll} />
      </div>
      {!isMobile && (
        <div className="flex gap-2">
          <Tooltip title={t('realtimeLog.clear')}><Button icon={<ClearOutlined />} onClick={() => setLogs([])} /></Tooltip>
          <Tooltip title={t('realtimeLog.export')}><Button icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
          <Tooltip title={t('realtimeLog.scrollBottom')}>
            <Button icon={<VerticalAlignBottomOutlined />} onClick={() => {
              if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight
            }} />
          </Tooltip>
        </div>
      )}
    </div>
  )

  const logContent = (
    <div className={isMobile ? 'flex-1 overflow-hidden flex flex-col gap-2' : 'flex flex-col gap-2'}>
      {isMobile && (
        <div className="flex items-center gap-2 flex-wrap">
          <div className="log-seg">
            <Segmented
              size="small"
              options={['INFO', 'WARN', 'DEBUG']}
              value={logLevel}
              onChange={setLogLevel}
            />
          </div>
          <Input
            size="small"
            placeholder={t('realtimeLog.searchPlaceholder')}
            prefix={<SearchOutlined className="text-gray-400" />}
            allowClear
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            style={{ flex: 1, minWidth: 120 }}
          />
        </div>
      )}
      <Card className={isMobile ? 'flex-1 overflow-hidden flex flex-col' : ''} styles={{ body: { padding: isMobile ? 8 : 12, ...(isMobile ? { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' } : {}) } }}>
        <div
          ref={containerRef}
          className={`${isMobile ? 'flex-1 overflow-y-auto overflow-x-hidden' : 'max-h-[60vh] overflow-y-auto overflow-x-hidden'}`}
        >
          {filteredLogs.length === 0 ? (
            <div className="flex items-center justify-center" style={{ height: isMobile ? '40vh' : '40vh' }}>
              <Empty description={<span className="text-gray-400">{logs.length === 0 ? t('realtimeLog.waitingLog') : t('realtimeLog.noMatchLog')}</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          ) : (
            filteredLogs.map((line, i) => {
              const filtered = filterLog(line)
              if (!filtered) return null
              const lc = getLevelColors(filtered)
              const displayText = stripLevelTag(filtered)
              return (
                <div
                  key={i}
                  className={`my-1 p-2 rounded group ${isMobile ? 'text-xs' : 'text-sm'} ${lc.border ? '' : 'bg-base-hover'} border-l-2 ${lc.border ? '' : 'border-primary'} hover:bg-base-hover-hover transition-colors`}
                  style={{ ...(lc.border ? { borderLeftColor: lc.border } : {}), ...(lc.bg ? { backgroundColor: lc.bg } : {}) }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <pre className="whitespace-pre-wrap break-words m-0 font-mono flex-1 min-w-0">
                      {searchText ? highlightText(displayText, searchText) : displayText}
                    </pre>
                    <Button
                      type="text"
                      size="small"
                      icon={<CopyOutlined />}
                      className={`shrink-0 opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'opacity-60' : ''}`}
                      onClick={(e) => { e.stopPropagation(); copyLogLine(filtered) }}
                      title={t('realtimeLog.copyLog')}
                    />
                  </div>
                </div>
              )
            })
          )}
        </div>
      </Card>
    </div>
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



function highlightText(text, keyword) {
  if (!keyword) return text
  const regex = new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return parts.map((part, i) =>
    regex.test(part) ? <mark key={i} className="bg-yellow-300 dark:bg-yellow-600 px-0.5 rounded">{part}</mark> : part
  )
}

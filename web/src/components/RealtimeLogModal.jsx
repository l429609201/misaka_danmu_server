import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Drawer, Button, Tooltip, message, Empty, Switch, Segmented, Input } from 'antd'
import { CopyOutlined, ExportOutlined, ClearOutlined, VerticalAlignBottomOutlined, SearchOutlined, PauseOutlined, CaretRightOutlined } from '@ant-design/icons'
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
  const [paused, setPaused] = useState(false)
  const [unread, setUnread] = useState(0)
  const followRef = useRef(true)
  const [logLevel, setLogLevel] = useState('INFO')
  const [searchText, setSearchText] = useState('')
  const abortRef = useRef(null)
  const containerRef = useRef(null)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  useEffect(() => {
    if (!open || paused) return
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
        setLogs(prev => [...prev, msg].slice(-600))
        if (!followRef.current) setUnread(prev => prev + 1)
      },
      onerror: (err) => { setConnected(false); throw err },
    }).catch(e => { if (e.name !== 'AbortError') console.error('SSE错误:', e) })

    return () => { ctrl.abort(); abortRef.current = null; setConnected(false) }
  }, [open, paused, messageApi, t])

  useEffect(() => {
    followRef.current = autoScroll
    if (autoScroll) setUnread(0)
  }, [autoScroll])

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleClose = () => {
    abortRef.current?.abort()
    setLogs([])
    setConnected(false)
    setPaused(false)
    setUnread(0)
    setAutoScroll(true)
    onClose()
  }

  const togglePaused = () => {
    if (paused) {
      // SSE 重连会先重放服务器的近期日志，清空旧快照以免重复。
      setLogs([])
      setUnread(0)
      setAutoScroll(true)
    }
    setPaused(!paused)
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
    const m = line.match(/^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*(?:\[[^\]]+\]\s*)?\[(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]/m)
      || line.match(/^\[(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]/m)
    return m ? (m[1] === 'WARN' ? 'WARNING' : m[1]) : 'INFO'
  }

  // INFO 显示常规信息与更严重日志；WARN 仅显示警告及以上。
  const LEVEL_INCLUDES = {
    INFO: new Set(['INFO', 'WARNING', 'ERROR', 'CRITICAL']),
    WARN: new Set(['WARNING', 'ERROR', 'CRITICAL']),
    DEBUG: new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']),
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

  // 级别色与历史日志一致，INFO 使用主题粉色。
  const getLevelColors = (line) => {
    const level = getLineLevelName(line)
    switch (level) {
      case 'CRITICAL':
      case 'ERROR': return { border: '#ef4444', bg: 'rgba(239,68,68,0.06)' }
      case 'WARNING': return { border: '#f59e0b', bg: 'rgba(245,158,11,0.06)' }
      case 'DEBUG': return { border: '#1d4ed8', bg: 'rgba(29,78,216,0.06)' }
      default: return {}
    }
  }

  const stripLevelTag = (text) => text.split('\n').map(line =>
    line.replace(/^(\s*(?:\[\d{4}-\d{2}-\d{2}[^\]]*\]\s*)?(?:\[[^\]]+\]\s*)?)\[(?:DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]\s*/, '$1')
  ).join('\n')

  const filteredLogs = useMemo(() => {
    const keyword = searchText.trim().toLowerCase()
    return logs.map(line => filterLog(line)).filter(line => line && (!keyword || line.toLowerCase().includes(keyword)))
    // filterLog 依赖当前级别，切换级别后必须重新计算空态和计数。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logs, searchText, logLevel])

  const segColor = { WARN: '#f59e0b', DEBUG: '#1d4ed8' }[logLevel]

  const titleNode = (
    <div className="flex items-center gap-2">
      <span>{t('realtimeLog.title')}</span>
      <span className={`inline-block w-2 h-2 rounded-full ${paused ? 'bg-gray-400' : connected ? 'bg-green-500' : 'bg-red-400'}`} />
      <span className="text-xs text-gray-400">{paused ? t('realtimeLog.paused') : connected ? t('realtimeLog.connected') : t('realtimeLog.disconnected')}</span>
    </div>
  )

  const scrollToLatest = () => {
    setAutoScroll(true)
    setUnread(0)
    if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight
  }

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title={paused ? t('realtimeLog.resume') : t('realtimeLog.pause')}>
        <Button size="small" type="text" icon={paused ? <CaretRightOutlined /> : <PauseOutlined />} onClick={togglePaused} />
      </Tooltip>
      <Tooltip title={t('realtimeLog.clear')}><Button size="small" type="text" icon={<ClearOutlined />} onClick={() => { setLogs([]); setUnread(0) }} /></Tooltip>
      <Tooltip title={t('realtimeLog.copyAll')}><Button size="small" type="text" icon={<CopyOutlined />} disabled={!filteredLogs.length} onClick={() => copyLogLine(filteredLogs.join('\n'))} /></Tooltip>
      <Tooltip title={t('realtimeLog.export')}><Button size="small" type="text" icon={<ExportOutlined />} disabled={!logs.length} onClick={exportLogs} /></Tooltip>
      <Tooltip title={t('realtimeLog.scrollBottom')}>
        <Button size="small" type="text" icon={<VerticalAlignBottomOutlined />} onClick={scrollToLatest} />
      </Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between gap-2">
      <label className="flex items-center gap-2 text-xs text-gray-500">
        <span>{t('realtimeLog.autoScroll')}</span>
        <Switch size="small" checked={autoScroll} onChange={setAutoScroll} />
      </label>
      {unread > 0 && <Button size="small" type="link" icon={<VerticalAlignBottomOutlined />} onClick={scrollToLatest}>
        {t('realtimeLog.unread', { count: unread })}
      </Button>}
    </div>
  )

  const logContent = (
    <div className={isMobile ? 'flex-1 min-h-0 flex flex-col gap-2' : 'flex flex-col gap-2'}>
      {segColor && <style>{`.realtime-log-levels .ant-segmented-item-selected { background: ${segColor} !important; color: #fff !important; }`}</style>}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="realtime-log-levels"><Segmented size="small" options={['INFO', 'WARN', 'DEBUG']} value={logLevel} onChange={setLogLevel} /></div>
        <Input
          size="small"
          placeholder={t('realtimeLog.searchPlaceholder')}
          prefix={<SearchOutlined />}
          allowClear
          value={searchText}
          onChange={e => setSearchText(e.target.value)}
          className="min-w-[140px] flex-1"
        />
        <span className="text-xs text-gray-500 whitespace-nowrap">
          {t('realtimeLog.count', { total: logs.length, visible: filteredLogs.length })}
        </span>
        {!isMobile && actionButtons}
        {isMobile && <div className="w-full flex justify-end">{actionButtons}</div>}
      </div>
      <div
        ref={containerRef}
        onScroll={e => {
          const el = e.currentTarget
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 32
          if (atBottom !== autoScroll) setAutoScroll(atBottom)
        }}
        className={`min-h-0 overflow-y-auto border border-solid border-border rounded bg-base-card p-2 ${isMobile ? 'flex-1' : 'h-[min(60vh,620px)]'}`}
      >
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-full min-h-[200px]">
            <Empty description={logs.length === 0 ? t('realtimeLog.waitingLog') : t('realtimeLog.noMatchLog')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : filteredLogs.map((line, i) => {
          const match = line.match(/^\[(\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d)\]\s*(?:\[([^\]]+)\]\s*)?\[(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]\s*/)
          const level = match?.[3] || getLineLevelName(line)
          const [datePart, clockPart] = match ? match[1].split(/\s+/) : ['—', '--:--:--']
          const source = match?.[2]
          const text = match ? line.slice(match[0].length) : stripLevelTag(line)
          const colors = getLevelColors(line)
          return (
            <div key={i} className={`group relative flex flex-wrap sm:flex-nowrap gap-x-2 gap-y-1 items-start my-1 pl-2 pr-9 py-2 rounded border-l-2 ${colors.border ? '' : 'border-primary bg-base-hover'} hover:brightness-95 text-xs`} style={colors.border ? { borderLeftColor: colors.border, backgroundColor: colors.bg } : undefined}>
              <time className="self-center shrink-0 w-[86px] flex flex-col text-center font-mono text-gray-500 leading-4 whitespace-nowrap" dateTime={match?.[1]?.replace(/\s+/, 'T')}>
                <span>{searchText ? highlightText(datePart, searchText) : datePart}</span>
                <span>{searchText ? highlightText(clockPart, searchText) : clockPart}</span>
              </time>
              <span className="self-center shrink-0 font-mono text-[11px] w-[66px] text-center rounded-sm leading-5" style={colors.border ? { color: colors.border } : undefined}>{searchText ? highlightText(level, searchText) : level}</span>
              {source && <span className="min-w-0 max-w-[130px] truncate font-mono text-gray-500 leading-5" title={source}>{searchText ? highlightText(source, searchText) : source}</span>}
              <pre className="m-0 min-w-0 flex-1 basis-full sm:basis-0 whitespace-pre-wrap break-words font-mono leading-5">{searchText ? highlightText(text, searchText) : text}</pre>
              <Tooltip title={t('realtimeLog.copyLog')}><Button type="text" size="small" icon={<CopyOutlined />} className={`absolute right-1 top-1 ${isMobile ? 'opacity-70' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`} onClick={() => copyLogLine(line)} /></Tooltip>
            </div>
          )
        })}
      </div>
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

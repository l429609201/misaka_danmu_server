import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Drawer, Button, Tooltip, message, Empty, Input, Spin, Select, Segmented } from 'antd'
import { CopyOutlined, ExportOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getLogs, getLogFiles, getLogFileContent } from '../apis'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

// 内存日志的特殊标识
const MEMORY_LOG_KEY = '__memory__'
// 每批加载行数（前端请求后端时的 tail 参数）
const BATCH_SIZE = 200

// ─── 模块级工具函数 ─────────────────────────────────────────────────────────

const LOG_HEADER_RE = /^\s*\[(\d{4}-\d\d-\d\d\s+\d\d:\d\d:\d\d)\]\s*(?:\[([^\]]+)\]\s*)?\[(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]\s*/
const LEVEL_INCLUDES = {
  INFO: new Set(['INFO', 'WARNING', 'ERROR', 'CRITICAL']),
  WARN: new Set(['WARNING', 'ERROR', 'CRITICAL']),
  DEBUG: new Set(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']),
}

const getLineLevel = (line) => {
  const level = line.match(LOG_HEADER_RE)?.[3] || line.match(/^\[(DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL)\]/)?.[1]
  return level === 'WARN' ? 'WARNING' : level
}

const getLevelColors = (line) => {
  switch (getLineLevel(line)) {
    case 'CRITICAL':
    case 'ERROR': return { border: '#ef4444', bg: 'rgba(239,68,68,0.06)' }
    case 'WARNING': return { border: '#f59e0b', bg: 'rgba(245,158,11,0.06)' }
    case 'DEBUG': return { border: '#1d4ed8', bg: 'rgba(29,78,216,0.06)' }
    default: return {}
  }
}

// 一条完整日志的起始行特征：以 [YYYY-MM-DD HH:mm:ss] 时间戳开头
const LOG_HEAD_RE = /^\s*\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/
// 缓冲日志块的首/尾标记（与实时日志 RealtimeLogModal 的判定保持一致）
const BLOCK_START = '┌───'
const BLOCK_END = '└───'

/**
 * 把后端返回的扁平行数组合并为「逻辑日志条目」。
 *
 * why：后端按 \n 逐行返回，前端若逐行渲染会把一条日志拆成多张卡片。
 * 实时日志走 SSE，一个 event 天然就是一整条，所以显示正常；历史日志
 * 从文件读取，必须自己还原条目边界。
 *
 * 需要合并的两类结构：
 *  1. 多行日志（异常堆栈、计时报告明细）——续行不带 [时间戳] 前缀；
 *  2. 缓冲日志块 ┌───…└───（如各弹幕源的搜索汇总）——块内每条子日志
 *     **都带自己的时间戳**，仅靠前缀无法识别，必须按 ┌/└ 括号配对，
 *     否则整块会被拆成十几张卡片。
 *
 * 规则（优先级从高到低）：
 *  - 处于块内（见到 ┌─── 尚未见到 └───）：所有行一律并入当前条目，
 *    直到 └─── 收尾；
 *  - 命中 ┌───：以它为起点开启新条目并进入块内状态；
 *  - 命中 LOG_HEAD_RE：开启新条目；
 *  - 其余：作为续行追加到当前条目。
 *
 * 边界：文件中段加载时首行可能是续行或位于块中部，此时归入一个"孤儿"
 * 条目而非丢弃；若块只有 ┌─── 没有 └───（日志被截断），块状态在遇到
 * 下一个 ┌─── 时自然重置，不会把后续日志无限吞进同一张卡片。
 */
const groupLogLines = (lines) => {
  const entries = []
  let inBlock = false

  const appendToLast = (line) => {
    if (entries.length === 0) entries.push(line)
    else entries[entries.length - 1] += '\n' + line
  }

  for (const line of lines) {
    const isBlockStart = line.includes(BLOCK_START)
    const isBlockEnd = line.includes(BLOCK_END)

    if (isBlockStart) {
      // 块头：┌─── 往往紧跟在同一条日志的正文后（如 "... - -" 换行接 ┌───），
      // 若上一条无时间戳前缀则视为同属一条，否则另起新卡片
      if (!LOG_HEAD_RE.test(line) && entries.length > 0) appendToLast(line)
      else entries.push(line)
      inBlock = !isBlockEnd
      continue
    }

    if (inBlock) {
      appendToLast(line)
      if (isBlockEnd) inBlock = false
      continue
    }

    if (LOG_HEAD_RE.test(line) || entries.length === 0) entries.push(line)
    else appendToLast(line)
  }
  return entries
}

const filterLogEntry = (entry, level) => {
  if (level === 'DEBUG') return entry
  const allowed = LEVEL_INCLUDES[level]
  if (!entry.includes(BLOCK_START) && !entry.includes(BLOCK_END)) {
    const detected = getLineLevel(entry)
    return (allowed.has(detected) || (level === 'INFO' && !detected)) ? entry : null
  }
  let keep = false
  let visible = false
  const filtered = entry.split('\n').filter(line => {
    if (line.includes(BLOCK_START) || line.includes(BLOCK_END) || !line.trim()) return true
    if (LOG_HEAD_RE.test(line)) {
      keep = allowed.has(getLineLevel(line))
      if (keep) visible = true
    }
    return keep
  })
  return visible ? filtered.join('\n') : (level === 'INFO' && !entry.split('\n').some(line => LOG_HEADER_RE.test(line)) ? entry : null)
}

const highlightText = (text, keyword) => {
  if (!keyword) return text
  const regex = new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  return text.split(regex).map((part, index) =>
    regex.test(part) ? <mark key={index} className="bg-yellow-300 dark:bg-yellow-600 px-0.5 rounded">{part}</mark> : part
  )
}

// ─── 日志列表（全展开 + 滚动到顶触发加载更多）────────────────────────────────
function LogList({ entries, hasMore, loadingMore, onLoadMore, isMobile, onCopyLine, copyLabel, keyword, loadMoreLabel, emptyLabel }) {
  const containerRef = useRef(null)
  const didScrollToLatestRef = useRef(false)
  // 记录上次 loadMore 时的滚动高度，加载完成后还原位置，避免列表跳动
  const prevScrollHeightRef = useRef(0)

  // 分页 prepend 恢复旧视口；新查询首次得到结果时展示最新记录。
  useEffect(() => {
    const el = containerRef.current
    if (!el || !entries.length) return
    if (!didScrollToLatestRef.current) {
      el.scrollTop = el.scrollHeight
      didScrollToLatestRef.current = true
      return
    }
    if (!prevScrollHeightRef.current) return
    const diff = el.scrollHeight - prevScrollHeightRef.current
    if (diff > 0) el.scrollTop = diff
    prevScrollHeightRef.current = 0
  }, [entries])

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el || loadingMore || !hasMore) return
    // 滚动到顶部 100px 内触发加载更多（旧日志在顶部）
    if (el.scrollTop <= 100) {
      prevScrollHeightRef.current = el.scrollHeight
      onLoadMore()
    }
  }, [hasMore, loadingMore, onLoadMore])

  return (
    <div
      ref={containerRef}
      className={isMobile
        ? 'flex-1 min-h-0 overflow-y-auto overflow-x-hidden'
        : 'h-[min(55vh,560px)] overflow-y-auto overflow-x-hidden'}
      onScroll={handleScroll}
    >
      {/* 顶部加载更多指示器 */}
      {(hasMore || loadingMore) && (
        <div className="flex justify-center py-1">
          <Button size="small" type="text" onClick={onLoadMore} loading={loadingMore}>{loadMoreLabel}</Button>
        </div>
      )}
      {entries.length === 0 && <div className="flex items-center justify-center h-full min-h-[200px]">
        <Empty description={emptyLabel} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </div>}
      {entries.map((line, i) => {
        const colors = getLevelColors(line)
        const match = line.match(LOG_HEADER_RE)
        const [datePart, clockPart] = match ? match[1].split(/\s+/) : ['—', '--:--:--']
        const source = match?.[2]
        const detectedLevel = getLineLevel(line)
        const level = match?.[3] || detectedLevel || '—'
        const text = match ? line.slice(match[0].length) : line
        return (
          <div
            key={i}
            className={`group relative flex flex-wrap sm:flex-nowrap gap-x-2 gap-y-1 items-start my-1 pl-2 pr-9 py-2 rounded border-l-2 text-xs ${!detectedLevel ? 'border-border' : colors.border ? '' : 'border-primary bg-base-hover'} hover:brightness-95`}
            style={colors.border ? { borderLeftColor: colors.border, backgroundColor: colors.bg } : undefined}
          >
            <time className="self-center shrink-0 w-[86px] flex flex-col text-center font-mono text-gray-500 leading-4 whitespace-nowrap" dateTime={match?.[1]?.replace(/\s+/, 'T')}>
              <span>{highlightText(datePart, keyword)}</span>
              <span>{highlightText(clockPart, keyword)}</span>
            </time>
            <span className="self-center shrink-0 font-mono text-[11px] w-[66px] text-center rounded-sm leading-5" style={colors.border ? { color: colors.border } : undefined}>{highlightText(level, keyword)}</span>
            {source && <span className="min-w-0 max-w-[130px] truncate font-mono text-gray-500 leading-5" title={source}>{highlightText(source, keyword)}</span>}
            <pre className="m-0 min-w-0 flex-1 basis-full sm:basis-0 whitespace-pre-wrap break-words font-mono leading-5">{highlightText(text, keyword)}</pre>
            <Tooltip title={copyLabel}><Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              className={`absolute right-1 top-1 ${isMobile ? 'opacity-70' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`}
              onClick={() => onCopyLine(line)}
            /></Tooltip>
          </div>
        )
      })}
    </div>
  )
}

export default function HistoryLogModal({ open, onClose }) {
  const { t } = useTranslation()
  // logs：当前已加载的行（最旧→最新顺序）
  const [logs, setLogs] = useState([])
  // hasMore：后端告知是否还有更旧的数据
  const [hasMore, setHasMore] = useState(false)
  // total：本次关键词下后端匹配总行数
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  // search：防抖后传给后端的关键词（不再前端 filter）
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const searchTimerRef = useRef(null)
  const requestIdRef = useRef(0)
  const [logFiles, setLogFiles] = useState([])
  const [selectedFile, setSelectedFile] = useState(MEMORY_LOG_KEY)
  const [logLevel, setLogLevel] = useState('INFO')
  const [listVersion, setListVersion] = useState(0)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)
  const entries = useMemo(() => groupLogLines(logs), [logs])
  const visibleEntries = useMemo(() => entries.map(entry => filterLogEntry(entry, logLevel)).filter(Boolean), [entries, logLevel])

  // 加载日志文件列表
  const fetchLogFiles = useCallback(() => {
    getLogFiles()
      .then(res => {
        const files = Array.isArray(res) ? res : (res?.data ?? [])
        setLogFiles(files)
      })
      .catch(() => {})
  }, [])

  // 首次/切换文件/切换关键词时的全新加载（offset=0，替换列表）
  const fetchLogs = useCallback((file = selectedFile, kw = search) => {
    const requestId = ++requestIdRef.current
    setListVersion(prev => prev + 1)
    setLoading(true)
    setLoadingMore(false)
    setLogs([])
    setHasMore(false)
    setTotal(0)
    if (file === MEMORY_LOG_KEY) {
      getLogs()
        .then(res => {
          if (requestId !== requestIdRef.current) return
          const lines = Array.isArray(res) ? res : (res?.data ?? [])
          // 内存日志全量返回，前端做一次关键词过滤即可。
          const filtered = kw ? lines.filter(l => l.toLowerCase().includes(kw.toLowerCase())) : lines
          setLogs(filtered)
          setTotal(filtered.length)
          setHasMore(false)
        })
        .catch(() => { if (requestId === requestIdRef.current) messageApi.error(t('historyLog.fetchFailed')) })
        .finally(() => { if (requestId === requestIdRef.current) setLoading(false) })
    } else {
      getLogFileContent(file, { tail: BATCH_SIZE, keyword: kw, offset: 0 })
        .then(res => {
          if (requestId !== requestIdRef.current) return
          const data = res?.data ?? res
          setLogs(data?.lines ?? [])
          setHasMore(data?.hasMore ?? false)
          setTotal(data?.total ?? 0)
        })
        .catch(() => { if (requestId === requestIdRef.current) messageApi.error(t('historyLog.fetchFileFailed')) })
        .finally(() => { if (requestId === requestIdRef.current) setLoading(false) })
    }
  }, [selectedFile, search, messageApi, t])

  // 加载更多（滚动到顶时追加更旧的数据，offset = 已加载行数）
  const fetchMore = useCallback(() => {
    if (loading || loadingMore || !hasMore || selectedFile === MEMORY_LOG_KEY) return
    const requestId = requestIdRef.current
    setLoadingMore(true)
    getLogFileContent(selectedFile, { tail: BATCH_SIZE, keyword: search, offset: logs.length })
      .then(res => {
        if (requestId !== requestIdRef.current) return
        const data = res?.data ?? res
        const newLines = data?.lines ?? []
        // prepend 到列表头部（更旧的在上方）。
        setLogs(prev => [...newLines, ...prev])
        setHasMore(newLines.length > 0 && (data?.hasMore ?? false))
        setTotal(data?.total ?? 0)
      })
      .catch(() => {})
      .finally(() => { if (requestId === requestIdRef.current) setLoadingMore(false) })
  }, [loading, loadingMore, hasMore, selectedFile, search, logs.length])

  useEffect(() => {
    const requestVersion = requestIdRef
    const searchTimer = searchTimerRef
    if (open) {
      setSelectedFile(MEMORY_LOG_KEY)
      setSearchInput('')
      setSearch('')
      setLogLevel('INFO')
      fetchLogFiles()
    }
    return () => {
      ++requestVersion.current
      clearTimeout(searchTimer.current)
    }
  }, [open, fetchLogFiles])

  useEffect(() => {
    if (open) fetchLogs(selectedFile, search)
  }, [open, selectedFile, search, fetchLogs])

  // 搜索输入防抖 300ms 后触发后端查询
  const handleSearchChange = (e) => {
    const val = e.target.value
    setSearchInput(val)
    clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => setSearch(val), 300)
  }

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const exportLogs = () => {
    const data = logs.join('\r\n')
    const blob = new Blob([data], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `history-logs-${dayjs().format('YYYY-MM-DD_HH-mm-ss')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const copyLogLine = async (logText) => {
    try {
      await navigator.clipboard.writeText(logText)
      messageApi.success(t('historyLog.copied'))
    } catch {
      const textArea = document.createElement('textarea')
      textArea.value = logText
      document.body.appendChild(textArea)
      textArea.select()
      try { document.execCommand('copy'); messageApi.success(t('historyLog.copied')) }
      catch { messageApi.error(t('historyLog.copyFailed')) }
      document.body.removeChild(textArea)
    }
  }

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(visibleEntries.join('\n'))
      messageApi.success(t('historyLog.copiedAll'))
    } catch { messageApi.error(t('historyLog.copyFailed')) }
  }

  const handleRefresh = () => fetchLogs(selectedFile, search)

  const fileOptions = [
    { label: t('historyLog.memoryLog'), value: MEMORY_LOG_KEY },
    ...logFiles.map(f => ({
      label: `${f.name} (${formatSize(f.size)})`,
      value: f.name,
    })),
  ]

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title={t('historyLog.refresh')}><Button size="small" type="text" icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading} /></Tooltip>
      <Tooltip title={t('historyLog.copyAll')}><Button size="small" type="text" icon={<CopyOutlined />} onClick={copyAll} disabled={!visibleEntries.length} /></Tooltip>
      <Tooltip title={t('historyLog.export')}><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} disabled={!logs.length} /></Tooltip>
    </div>
  )

  const footerNode = (
    <span className="text-xs text-gray-500">{t('historyLog.loadedCount', { count: logs.length, total })}</span>
  )

  const segColor = { WARN: '#f59e0b', DEBUG: '#1d4ed8' }[logLevel]

  const logContent = (
    <div className={isMobile ? 'flex-1 min-h-0 flex flex-col gap-2' : 'flex flex-col gap-2'}>
      {segColor && <style>{`.history-log-levels .ant-segmented-item-selected { background: ${segColor} !important; color: #fff !important; }`}</style>}
      <div className="flex items-center gap-2 flex-wrap">
        <Select
          value={selectedFile}
          onChange={setSelectedFile}
          options={fileOptions}
          size="small"
          className={isMobile ? 'min-w-[130px] flex-1' : ''}
          style={isMobile ? undefined : { width: 220 }}
        />
        <Input
          placeholder={t('historyLog.searchPlaceholder')}
          prefix={<SearchOutlined className="text-gray-400" />}
          value={searchInput}
          onChange={handleSearchChange}
          allowClear
          onClear={() => { setSearchInput(''); setSearch('') }}
          size="small"
          className="min-w-[140px] flex-1"
        />
        <div className="history-log-levels"><Segmented size="small" options={['INFO', 'WARN', 'DEBUG']} value={logLevel} onChange={setLogLevel} /></div>
        <span className="text-xs text-gray-500 whitespace-nowrap">{t('historyLog.visibleCount', { count: visibleEntries.length })}</span>
        {!isMobile && actionButtons}
        {isMobile && <div className="w-full flex justify-end">{actionButtons}</div>}
      </div>
      <div className={`relative min-h-0 border border-solid border-border rounded bg-base-card p-2 ${isMobile ? 'flex-1 flex flex-col' : ''}`}>
        {loading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-base-card/70"><Spin /></div>}
        <LogList
          key={listVersion}
          entries={visibleEntries}
          hasMore={hasMore}
          loadingMore={loadingMore}
          onLoadMore={fetchMore}
          isMobile={isMobile}
          onCopyLine={copyLogLine}
          copyLabel={t('historyLog.copyLog')}
          keyword={search}
          loadMoreLabel={t('historyLog.loadMore')}
          emptyLabel={logs.length === 0 && !search ? t('historyLog.noLog') : t('historyLog.noMatchLog')}
        />
      </div>
    </div>
  )

  return (
    <>
      {contextHolder}
      {isMobile ? (
        <Drawer
          title={t('historyLog.title')}
          placement="bottom"
          height="85%"
          open={open}
          onClose={onClose}
          footer={footerNode}
          destroyOnClose
          styles={{ body: { overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: 12 } }}
        >
          {logContent}
        </Drawer>
      ) : (
        <Modal
          title={t('historyLog.title')}
          open={open}
          onCancel={onClose}
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


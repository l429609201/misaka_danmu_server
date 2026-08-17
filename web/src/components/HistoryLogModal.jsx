import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Drawer, Button, Tooltip, message, Empty, Input, Spin, Select, Card } from 'antd'
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

const getLevelColors = (line) => {
  const m = line.match(/\[(DEBUG|INFO|WARNING|ERROR)\]/)
  if (!m) return {}
  switch (m[1]) {
    case 'ERROR':   return { border: '#ef4444', bg: 'rgba(239,68,68,0.06)' }
    case 'WARNING': return { border: '#f59e0b', bg: 'rgba(245,158,11,0.06)' }
    case 'DEBUG':   return { border: '#1d4ed8', bg: 'rgba(29,78,216,0.06)' }
    default: return {}
  }
}

// 隐去级别标签。why：合并后一个条目可能含多行（缓冲块内每条子日志都带
// 自己的标签），故逐行各去一次，而非全局 replace —— 全局会误伤正文里
// 恰好出现的 [INFO] 等字样。
const stripLevelTag = (text) =>
  text
    .split('\n')
    .map(line => line.replace(/\s*\[(DEBUG|INFO|WARNING|ERROR)\]\s*/, ' '))
    .join('\n')

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
      inBlock = true
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

// ─── 日志列表（全展开 + 滚动到顶触发加载更多）────────────────────────────────
function LogList({ lines, hasMore, loadingMore, onLoadMore, isMobile, onCopyLine, copyLabel }) {
  const containerRef = useRef(null)
  // 把扁平行合并为逻辑条目，使多行日志（计时报告/异常堆栈）显示为一张卡片
  const entries = useMemo(() => groupLogLines(lines), [lines])
  // 记录上次 loadMore 时的滚动高度，加载完成后还原位置，避免列表跳动
  const prevScrollHeightRef = useRef(0)

  // why：新数据 prepend 到顶部后，浏览器会把视口定位到新内容顶部（滚动位置归零），
  // 需要手动把 scrollTop 恢复为"旧内容顶端"的位置，实现无感加载。
  useEffect(() => {
    const el = containerRef.current
    if (!el || !prevScrollHeightRef.current) return
    const diff = el.scrollHeight - prevScrollHeightRef.current
    if (diff > 0) el.scrollTop = diff
    prevScrollHeightRef.current = 0
  }, [lines])

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
        : 'max-h-[55vh] overflow-y-auto overflow-x-hidden'}
      onScroll={handleScroll}
    >
      {/* 顶部加载更多指示器 */}
      {(hasMore || loadingMore) && (
        <div className="flex justify-center py-2 text-xs text-gray-400">
          {loadingMore ? <Spin size="small" /> : <span className="opacity-50">↑ 向上滚动加载更多</span>}
        </div>
      )}
      {entries.map((line, i) => {
        const lc = getLevelColors(line)
        const displayText = stripLevelTag(line)
        return (
          <div
            key={i}
            className={`my-1 rounded border-l-2 group ${isMobile ? 'text-xs' : 'text-sm'} ${lc.border ? '' : 'bg-base-hover border-primary'} hover:bg-base-hover-hover transition-colors`}
            style={{
              ...(lc.border ? { borderLeftColor: lc.border } : {}),
              ...(lc.bg ? { backgroundColor: lc.bg } : {}),
            }}
          >
            <div className="flex items-start gap-2 px-2 py-2 justify-between">
              <pre className="m-0 min-w-0 flex-1 font-mono whitespace-pre-wrap break-all overflow-x-auto">
                {displayText}
              </pre>
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                className={`shrink-0 opacity-0 group-hover:opacity-100 transition-opacity${isMobile ? ' opacity-60' : ''}`}
                onClick={(e) => { e.stopPropagation(); onCopyLine(line) }}
                title={copyLabel}
              />
            </div>
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
  const [logFiles, setLogFiles] = useState([])
  const [selectedFile, setSelectedFile] = useState(MEMORY_LOG_KEY)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  // 加载日志文件列表
  const fetchLogFiles = () => {
    getLogFiles()
      .then(res => {
        const files = Array.isArray(res) ? res : (res?.data ?? [])
        setLogFiles(files)
      })
      .catch(() => {})
  }

  // 首次/切换文件/切换关键词时的全新加载（offset=0，替换列表）
  const fetchLogs = useCallback((file = selectedFile, kw = search) => {
    setLoading(true)
    setLogs([])
    setHasMore(false)
    setTotal(0)
    if (file === MEMORY_LOG_KEY) {
      getLogs()
        .then(res => {
          const lines = Array.isArray(res) ? res : (res?.data ?? [])
          // 内存日志全量返回，前端做一次关键词过滤即可
          const filtered = kw ? lines.filter(l => l.toLowerCase().includes(kw.toLowerCase())) : lines
          setLogs(filtered)
          setTotal(filtered.length)
          setHasMore(false)
        })
        .catch(() => messageApi.error(t('historyLog.fetchFailed')))
        .finally(() => setLoading(false))
    } else {
      getLogFileContent(file, { tail: BATCH_SIZE, keyword: kw, offset: 0 })
        .then(res => {
          const data = res?.data ?? res
          setLogs(data?.lines ?? [])
          setHasMore(data?.hasMore ?? false)
          setTotal(data?.total ?? 0)
        })
        .catch(() => messageApi.error(t('historyLog.fetchFileFailed')))
        .finally(() => setLoading(false))
    }
  }, [selectedFile, search])

  // 加载更多（滚动到顶时追加更旧的数据，offset = 已加载行数）
  const fetchMore = useCallback(() => {
    if (loadingMore || !hasMore || selectedFile === MEMORY_LOG_KEY) return
    setLoadingMore(true)
    getLogFileContent(selectedFile, { tail: BATCH_SIZE, keyword: search, offset: logs.length })
      .then(res => {
        const data = res?.data ?? res
        const newLines = data?.lines ?? []
        // prepend 到列表头部（更旧的在上方）
        setLogs(prev => [...newLines, ...prev])
        setHasMore(data?.hasMore ?? false)
        setTotal(data?.total ?? 0)
      })
      .catch(() => {})
      .finally(() => setLoadingMore(false))
  }, [loadingMore, hasMore, selectedFile, search, logs.length])

  useEffect(() => {
    if (open) {
      setSelectedFile(MEMORY_LOG_KEY)
      setSearchInput('')
      setSearch('')
      fetchLogFiles()
    }
  }, [open])

  useEffect(() => {
    if (open) fetchLogs(selectedFile, search)
  }, [open, selectedFile, search])

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
      await navigator.clipboard.writeText(logs.join('\n'))
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
      <Tooltip title={t('historyLog.copyAll')}><Button size="small" type="text" icon={<CopyOutlined />} onClick={copyAll} /></Tooltip>
      <Tooltip title={t('historyLog.export')}><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-400">
        {t('historyLog.loadedCount', { count: logs.length, total })}
      </span>
      {!isMobile && (
        <div className="flex gap-2">
          <Tooltip title={t('historyLog.refresh')}><Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading} /></Tooltip>
          <Tooltip title={t('historyLog.copyAll')}><Button icon={<CopyOutlined />} onClick={copyAll} /></Tooltip>
          <Tooltip title={t('historyLog.export')}><Button icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
        </div>
      )}
    </div>
  )

  const logContent = (
    <>
      <div className={isMobile ? 'flex gap-1.5 mb-1.5' : 'flex gap-2 mb-3'}>
        <Select
          value={selectedFile}
          onChange={setSelectedFile}
          options={fileOptions}
          size={isMobile ? 'small' : 'middle'}
          style={isMobile ? { flex: '1 1 0', minWidth: 0 } : { minWidth: 240 }}
        />
        <Input
          placeholder={t('historyLog.searchPlaceholder')}
          prefix={<SearchOutlined className="text-gray-400" />}
          value={searchInput}
          onChange={handleSearchChange}
          allowClear
          onClear={() => { setSearchInput(''); setSearch('') }}
          size={isMobile ? 'small' : 'middle'}
          style={isMobile ? { flex: '1 1 0', minWidth: 0 } : undefined}
        />
      </div>
      <Card className={isMobile ? 'flex-1 min-h-0 flex flex-col' : ''} styles={{ body: { padding: isMobile ? 8 : 12, ...(isMobile ? { flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' } : {}) } }}>
        {logs.length === 0 ? (
          <div className="relative flex items-center justify-center" style={{ height: '30vh' }}>
            {loading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.04)' }}>
                <Spin />
              </div>
            )}
            <Empty description={<span className="text-gray-400">{search ? t('historyLog.noMatchLog') : t('historyLog.noLog')}</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : (
          <div className="relative">
            {loading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center" style={{ backgroundColor: 'rgba(0,0,0,0.04)', minHeight: '4rem' }}>
                <Spin />
              </div>
            )}
            <LogList
              lines={logs}
              hasMore={hasMore}
              loadingMore={loadingMore}
              onLoadMore={fetchMore}
              isMobile={isMobile}
              onCopyLine={copyLogLine}
              copyLabel={t('historyLog.copyLog')}
            />
          </div>
        )}
      </Card>
    </>
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
          extra={actionButtons}
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


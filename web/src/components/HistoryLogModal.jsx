import { useEffect, useState, useMemo, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Drawer, Button, Tooltip, message, Empty, Input, Spin, Select, Card } from 'antd'
import { CopyOutlined, ExportOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getLogs, getLogFiles, getLogFileContent } from '../apis'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

// 内存日志的特殊标识
const MEMORY_LOG_KEY = '__memory__'

// ─── 模块级工具函数（移出组件，避免每次 render 重建）────────────────────────

// 按级别返回边框色和背景色
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

// 隐去日志文本中的级别标签
const stripLevelTag = (text) => text.replace(/\s*\[(DEBUG|INFO|WARNING|ERROR)\]\s*/, ' ')

// ─── 虚拟滚动列表 ────────────────────────────────────────────────────────────
// why：日志行数可达数千条，全量渲染会把所有行都创建成 DOM 节点，导致主线程卡死。
// 虚拟滚动只渲染视口内 + 上下 OVERSCAN 行，其余用空白 div 占位保持正确的滚动条比例。
const ROW_H_PC = 56   // 桌面端每行估算高度（px）
const ROW_H_MB = 44   // 移动端每行估算高度（px）
const OVERSCAN  = 8   // 视口外上下各多渲染的缓冲行数，防快速滚动闪白

function VirtualLogList({ lines, search, isMobile, onCopyLine, copyLabel }) {
  const containerRef = useRef(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [containerH, setContainerH] = useState(600)

  const rowH = isMobile ? ROW_H_MB : ROW_H_PC

  // 监听容器实际高度（窗口缩放 / Drawer 弹出时高度不同）
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    if (el.clientHeight > 0) setContainerH(el.clientHeight)
    const ro = new ResizeObserver(() => {
      if (el.clientHeight > 0) setContainerH(el.clientHeight)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const handleScroll = useCallback((e) => setScrollTop(e.currentTarget.scrollTop), [])

  const startIdx = Math.max(0, Math.floor(scrollTop / rowH) - OVERSCAN)
  const endIdx   = Math.min(lines.length, Math.ceil((scrollTop + containerH) / rowH) + OVERSCAN)

  return (
    <div
      ref={containerRef}
      className={isMobile
        ? 'flex-1 min-h-0 overflow-y-auto overflow-x-hidden'
        : 'max-h-[55vh] overflow-y-auto overflow-x-hidden'}
      onScroll={handleScroll}
    >
      {/* 顶部占位：撑出滚动区域，使滚动条比例与总行数匹配 */}
      <div aria-hidden="true" style={{ height: startIdx * rowH }} />
      {lines.slice(startIdx, endIdx).map((line, rel) => {
        const i = startIdx + rel
        const lc = getLevelColors(line)
        const displayText = stripLevelTag(line)
        return (
          <div
            key={i}
            className={`my-1 p-2 rounded group ${isMobile ? 'text-xs' : 'text-sm'} ${lc.border ? '' : 'bg-base-hover'} border-l-2 ${lc.border ? '' : 'border-primary'} hover:bg-base-hover-hover transition-colors`}
            style={{ ...(lc.border ? { borderLeftColor: lc.border } : {}), ...(lc.bg ? { backgroundColor: lc.bg } : {}) }}
          >
            <div className="flex items-start justify-between gap-2">
              <pre className="whitespace-pre-wrap break-words m-0 font-mono flex-1 min-w-0">
                {search ? highlightText(displayText, search) : displayText}
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
      {/* 底部占位 */}
      <div aria-hidden="true" style={{ height: (lines.length - endIdx) * rowH }} />
    </div>
  )
}

export default function HistoryLogModal({ open, onClose }) {
  const { t } = useTranslation()
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
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

  // 加载日志内容
  const fetchLogs = () => {
    setLoading(true)
    if (selectedFile === MEMORY_LOG_KEY) {
      getLogs()
        .then(res => setLogs(Array.isArray(res) ? res : (res?.data ?? [])))
        .catch(() => messageApi.error(t('historyLog.fetchFailed')))
        .finally(() => setLoading(false))
    } else {
      getLogFileContent(selectedFile)
        .then(res => setLogs(Array.isArray(res) ? res : (res?.data ?? [])))
        .catch(() => messageApi.error(t('historyLog.fetchFileFailed')))
        .finally(() => setLoading(false))
    }
  }

  useEffect(() => {
    if (open) {
      setSelectedFile(MEMORY_LOG_KEY)
      setSearch('')
      fetchLogFiles()
    }
  }, [open])

  useEffect(() => {
    if (open) fetchLogs()
  }, [open, selectedFile])

  const filtered = useMemo(() => {
    if (!search.trim()) return logs
    const kw = search.toLowerCase()
    return logs.filter(line => line.toLowerCase().includes(kw))
  }, [logs, search])

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const exportLogs = () => {
    const data = filtered.join('\r\n')
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
      await navigator.clipboard.writeText(filtered.join('\n'))
      messageApi.success(t('historyLog.copiedAll'))
    } catch { messageApi.error(t('historyLog.copyFailed')) }
  }

  const fileOptions = [
    { label: t('historyLog.memoryLog'), value: MEMORY_LOG_KEY },
    ...logFiles.map(f => ({
      label: `${f.name} (${formatSize(f.size)})`,
      value: f.name,
    })),
  ]

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title={t('historyLog.refresh')}><Button size="small" type="text" icon={<ReloadOutlined />} onClick={fetchLogs} loading={loading} /></Tooltip>
      <Tooltip title={t('historyLog.copyAll')}><Button size="small" type="text" icon={<CopyOutlined />} onClick={copyAll} /></Tooltip>
      <Tooltip title={t('historyLog.export')}><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-400">
        {search ? t('historyLog.filteredFrom', { count: filtered.length, total: logs.length }) : t('historyLog.totalCount', { count: filtered.length })}
      </span>
      {!isMobile && (
        <div className="flex gap-2">
          <Tooltip title={t('historyLog.refresh')}><Button icon={<ReloadOutlined />} onClick={fetchLogs} loading={loading} /></Tooltip>
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
          value={search}
          onChange={e => setSearch(e.target.value)}
          allowClear
          size={isMobile ? 'small' : 'middle'}
          style={isMobile ? { flex: '1 1 0', minWidth: 0 } : undefined}
        />
      </div>
      {/* 修复移动端无法滚动：Spin 作为容器会插入 .ant-spin-nested-loading / .ant-spin-container 两层 div，
          断开 Drawer 的 flex 高度链，导致内部滚动容器算不出高度。改为滚动容器直接挂在 Card.body 下，
          loading 时用绝对定位遮罩覆盖，桌面端逻辑（max-h-[55vh]）保持不变。 */}
      <Card className={isMobile ? 'flex-1 min-h-0 flex flex-col' : ''} styles={{ body: { padding: isMobile ? 8 : 12, ...(isMobile ? { flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' } : {}) } }}>
        {filtered.length === 0 ? (
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
            <VirtualLogList
              lines={filtered}
              search={search}
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

function highlightText(text, keyword) {
  if (!keyword) return text
  const regex = new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return parts.map((part, i) =>
    regex.test(part) ? <mark key={i} className="bg-yellow-300 dark:bg-yellow-600 px-0.5 rounded">{part}</mark> : part
  )
}


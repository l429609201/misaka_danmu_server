import { useEffect, useState, useMemo } from 'react'
import { Modal, Drawer, Button, Tooltip, message, Empty, Input, Spin, Select, Card } from 'antd'
import { CopyOutlined, ExportOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { getLogs, getLogFiles, getLogFileContent } from '../apis'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'

// 内存日志的特殊标识
const MEMORY_LOG_KEY = '__memory__'

export default function HistoryLogModal({ open, onClose }) {
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
        .catch(() => messageApi.error('获取日志失败'))
        .finally(() => setLoading(false))
    } else {
      getLogFileContent(selectedFile)
        .then(res => setLogs(Array.isArray(res) ? res : (res?.data ?? [])))
        .catch(() => messageApi.error('获取日志文件失败'))
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

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(filtered.join('\n'))
      messageApi.success('已复制全部日志')
    } catch { messageApi.error('复制失败') }
  }

  const fileOptions = [
    { label: '内存日志 (实时缓存)', value: MEMORY_LOG_KEY },
    ...logFiles.map(f => ({
      label: `${f.name} (${formatSize(f.size)})`,
      value: f.name,
    })),
  ]

  const actionButtons = (
    <div className="flex gap-1">
      <Tooltip title="刷新"><Button size="small" type="text" icon={<ReloadOutlined />} onClick={fetchLogs} loading={loading} /></Tooltip>
      <Tooltip title="复制全部"><Button size="small" type="text" icon={<CopyOutlined />} onClick={copyAll} /></Tooltip>
      <Tooltip title="导出"><Button size="small" type="text" icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
    </div>
  )

  const footerNode = (
    <div className="flex items-center justify-between">
      <span className="text-xs text-gray-400">
        共 {filtered.length} 条{search ? ` (过滤自 ${logs.length} 条)` : ''}
      </span>
      {!isMobile && (
        <div className="flex gap-2">
          <Tooltip title="刷新"><Button icon={<ReloadOutlined />} onClick={fetchLogs} loading={loading} /></Tooltip>
          <Tooltip title="复制全部"><Button icon={<CopyOutlined />} onClick={copyAll} /></Tooltip>
          <Tooltip title="导出"><Button icon={<ExportOutlined />} onClick={exportLogs} /></Tooltip>
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
          placeholder="搜索..."
          prefix={<SearchOutlined className="text-gray-400" />}
          value={search}
          onChange={e => setSearch(e.target.value)}
          allowClear
          size={isMobile ? 'small' : 'middle'}
          style={isMobile ? { flex: '1 1 0', minWidth: 0 } : undefined}
        />
      </div>
      <Spin spinning={loading} className={isMobile ? 'flex-1 overflow-hidden flex flex-col' : ''}>
        <Card className={isMobile ? 'flex-1 overflow-hidden flex flex-col' : ''} styles={{ body: { padding: isMobile ? 8 : 12, ...(isMobile ? { flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' } : {}) } }}>
          <div
            className={`${isMobile ? 'flex-1 overflow-y-auto overflow-x-hidden' : 'max-h-[55vh] overflow-y-auto overflow-x-hidden'}`}
          >
            {filtered.length === 0 ? (
              <div className="flex items-center justify-center" style={{ height: '30vh' }}>
                <Empty description={<span className="text-gray-400">{search ? '无匹配日志' : '暂无日志'}</span>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
              </div>
            ) : (
              filtered.map((line, i) => (
                <div
                  key={i}
                  className={`my-1 p-2 rounded group ${isMobile ? 'text-xs' : 'text-sm'} bg-base-hover border-l-2 border-primary hover:bg-base-hover-hover transition-colors`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <pre className="whitespace-pre-wrap break-words m-0 font-mono flex-1 min-w-0">
                      {search ? highlightText(line, search) : line}
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
      </Spin>
    </>
  )

  return (
    <>
      {contextHolder}
      {isMobile ? (
        <Drawer
          title="历史日志"
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
          title="历史日志"
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


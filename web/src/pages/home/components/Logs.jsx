import { useEffect } from 'react'
import { getLogs } from '../../../apis'
import { useState } from 'react'
import { useRef } from 'react'
import { Card, Tooltip, message, Button } from 'antd'
import { ExportOutlined, CopyOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import Cookies from 'js-cookie'
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'

export const Logs = () => {
  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState([])
  const abortControllerRef = useRef(null)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)

  useEffect(() => {
    // 获取token
    const token = Cookies.get('danmu_token')
    if (!token) {
      messageApi.error('未登录,无法连接日志流')
      setLoading(false)
      return
    }

    // 创建AbortController用于取消连接
    const abortController = new AbortController()
    abortControllerRef.current = abortController

    // 使用fetch-event-source建立SSE连接（开发环境会通过Vite代理）
    fetchEventSource('/api/ui/logs/stream', {
      signal: abortController.signal,
      headers: {
        Authorization: `Bearer ${token}`,
      },
      onopen: async response => {
        if (response.ok) {
          setLoading(false)
        } else {
          throw new Error(`连接失败: ${response.status}`)
        }
      },
      onmessage: event => {
        const newLog = event.data.trim()
        if (!newLog) return
        setLogs(prevLogs => [newLog, ...prevLogs].slice(0, 200)) // 保持最多200条
      },
      onerror: error => {
        console.error('SSE连接错误:', error)
        messageApi.warning('日志流连接出错')
        setLoading(false)
        throw error // 抛出错误以停止重连
      },
    }).catch(error => {
      if (error.name !== 'AbortError') {
        console.error('SSE流错误:', error)
      }
    })

    // 清理函数
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  const exportLogs = () => {
    const blob = new Blob([logs.slice().reverse().join('\r\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs-${dayjs().format('YYYY-MM-DD_HH-mm-ss')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const copyLogLine = async (logText) => {
    try {
      await navigator.clipboard.writeText(logText)
      messageApi.success('日志已复制到剪贴板')
    } catch (error) {
      // 降级方案：使用传统方法
      const textArea = document.createElement('textarea')
      textArea.value = logText
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        messageApi.success('日志已复制到剪贴板')
      } catch (fallbackError) {
        messageApi.error('复制失败')
      }
      document.body.removeChild(textArea)
    }
  }

  const handleLongPress = (logText) => {
    if (isMobile) {
      copyLogLine(logText)
    }
  }

  return (
    <>
      {contextHolder}
      <div className="my-6">
        <Card
          loading={loading}
          title="日志/状态 (实时)"
          extra={
            <Tooltip title="导出日志">
              <div onClick={exportLogs} className="cursor-pointer hover:text-primary">
                <ExportOutlined />
              </div>
            </Tooltip>
          }
        >
          <div className="max-h-[400px] overflow-y-auto overflow-x-hidden">
            {logs?.map((it, index) => (
              <div 
                key={index} 
                className={`my-1 p-2 rounded group ${isMobile ? 'text-xs' : 'text-sm'} bg-base-hover border-l-2 border-primary hover:bg-base-hover-hover transition-colors`}
                onContextMenu={(e) => {
                  if (isMobile) {
                    e.preventDefault()
                    handleLongPress(it)
                  }
                }}
                onTouchStart={(e) => {
                  if (isMobile) {
                    const timer = setTimeout(() => {
                      handleLongPress(it)
                    }, 500) // 长按500ms触发
                    e.currentTarget.longPressTimer = timer
                  }
                }}
                onTouchEnd={(e) => {
                  if (isMobile && e.currentTarget.longPressTimer) {
                    clearTimeout(e.currentTarget.longPressTimer)
                    delete e.currentTarget.longPressTimer
                  }
                }}
                onTouchMove={(e) => {
                  if (isMobile && e.currentTarget.longPressTimer) {
                    clearTimeout(e.currentTarget.longPressTimer)
                    delete e.currentTarget.longPressTimer
                  }
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <pre className="whitespace-pre-wrap break-words m-0 font-mono flex-1 min-w-0">
                    {it}
                  </pre>
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    className={`shrink-0 opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'opacity-60' : ''}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      copyLogLine(it)
                    }}
                    title="复制日志"
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  )
}

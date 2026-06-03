import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState([])
  const abortControllerRef = useRef(null)
  const [messageApi, contextHolder] = message.useMessage()
  const isMobile = useAtomValue(isMobileAtom)
  const [connected, setConnected] = useState(false)

  const connectSSE = () => {
    // 断开旧连接
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    const token = Cookies.get('danmu_token')
    if (!token) {
      messageApi.error(t('home.notLoggedIn'))
      setLoading(false)
      return
    }

    setLoading(true)
    setConnected(false)

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    fetchEventSource('/api/ui/logs/stream', {
      signal: abortController.signal,
      headers: {
        Authorization: `Bearer ${token}`,
      },
      onopen: async response => {
        if (response.ok) {
          setLoading(false)
          setConnected(true)
        } else {
          throw new Error(`${t('home.connectFailed')}: ${response.status}`)
        }
      },
      onmessage: event => {
        const newLog = event.data.trim()
        if (!newLog) return
        setLogs(prevLogs => [newLog, ...prevLogs].slice(0, 200))
      },
      onerror: error => {
        console.error('SSE连接错误:', error)
        setConnected(false)
        setLoading(false)
        throw error
      },
    }).catch(error => {
      if (error.name !== 'AbortError') {
        console.error('SSE流错误:', error)
        setConnected(false)
      }
    })
  }

  useEffect(() => {
    connectSSE()
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
      messageApi.success(t('home.logCopied'))
    } catch (error) {
      // 降级方案：使用传统方法
      const textArea = document.createElement('textarea')
      textArea.value = logText
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        messageApi.success(t('home.logCopied'))
      } catch (fallbackError) {
        messageApi.error(t('home.copyFailed'))
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
          title={t('home.logStatus')}
          extra={
            <div className="flex items-center gap-3">
              <Tooltip title={connected ? t('realtimeLog.connected') : t('realtimeLog.clickToReconnect')}>
                <div
                  onClick={() => { if (!connected) connectSSE() }}
                  className={`flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-lg transition-colors ${
                    connected
                      ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10'
                      : 'text-red-500 dark:text-red-400 bg-red-50 dark:bg-red-500/10 cursor-pointer hover:bg-red-100 dark:hover:bg-red-500/20'
                  }`}
                >
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'}`} />
                  {connected ? t('realtimeLog.connected') : t('realtimeLog.disconnected')}
                </div>
              </Tooltip>
              <Tooltip title={t('home.exportLog')}>
                <div onClick={exportLogs} className="cursor-pointer hover:text-primary">
                  <ExportOutlined />
                </div>
              </Tooltip>
            </div>
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
                    title={t('home.copyLog')}
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

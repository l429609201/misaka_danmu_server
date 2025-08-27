import { useEffect } from 'react'
import { getLogs } from '../../../apis'
import { useState } from 'react'
import { useRef } from 'react'
import { Card, Tooltip } from 'antd'
import { ExportOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

export const Logs = () => {
  const [loading, setLoading] = useState(true)
  const [logs, setLogs] = useState([])
  const timer = useRef()

  const refreshLogs = async () => {
    try {
      const res = await getLogs()
      setLogs(res.data)
      setLoading(false)
    } catch (error) {
      console.error(error)
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshLogs()
    timer.current = setInterval(refreshLogs, 3000)
    return () => {
      clearInterval(timer.current)
    }
  }, [])

  const exportLogs = () => {
    const blob = new Blob([logs.join('\r\n')], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs-${dayjs().format('YYYY-MM-DD_HH-mm-ss')}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title="日志/状态"
        extra={
          <Tooltip title="导出日志">
            <div onClick={exportLogs}>
              <ExportOutlined />
            </div>
          </Tooltip>
        }
      >
        <div className="max-h-[400px] overflow-y-auto">
          {logs?.map((it, index) => (
            <div key={index}>
              <div className="my-1">
                <pre>{it}</pre>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

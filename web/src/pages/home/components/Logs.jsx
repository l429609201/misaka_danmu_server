import { useEffect } from 'react'
import { getLogs } from '../../../apis'
import { useState } from 'react'
import { useRef } from 'react'
import { Card } from 'antd'

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

  return (
    <div className="my-6">
      <Card loading={loading} title="日志/状态">
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

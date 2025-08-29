import { Button, Card, Input, message } from 'antd'
import { useEffect, useState } from 'react'
import { getCustomDomain, setCustomDomain } from '../../../apis'
import { useMessage } from '../../../MessageContext'

export const Domain = () => {
  const [loading, setLoading] = useState(false)
  const [domain, setDomain] = useState('')
  const messageApi = useMessage()

  useEffect(() => {
    setLoading(true)
    getCustomDomain()
      .then(res => {
        setDomain(res.data?.value ?? '')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleEdit = async () => {
    try {
      await setCustomDomain({ value: domain })
      messageApi.success('保存成功')
    } catch (error) {
      messageApi.error('保存失败')
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="自定义域名设置">
        <div>
          设置后，复制按钮将自动拼接 "http(s)://域名(ip):端口(port)/api/Token值"
          格式的完整URL。
        </div>
        <div className="flex items-center justify-start mt-4">
          <Input
            placeholder="请输入自定义域名"
            value={domain}
            onChange={e => setDomain(e.target.value)}
          />
          <Button type="primary" className="ml-2" onClick={handleEdit}>
            修改
          </Button>
        </div>
      </Card>
    </div>
  )
}

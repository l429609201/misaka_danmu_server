import { Button, Card, InputNumber, message, Switch } from 'antd'
import {
  getDanmuOutputAggregation,
  getDanmuOutputTotal,
  setDanmuOutputAggregation,
  setDanmuOutputTotal,
} from '../../../apis'
import { useEffect, useState } from 'react'
import { useMessage } from '../../../MessageContext'

export const OutputManage = () => {
  const [loading, setLoading] = useState(false)
  const [limit, setLimit] = useState('-1')
  const [enable, setEnable] = useState(false)
  const [saveLoading, setSaveLoading] = useState(false)

  const messageApi = useMessage()

  const getConfig = async () => {
    setLoading(true)
    try {
      const [limitRes, enableRes] = await Promise.all([
        getDanmuOutputTotal(),
        getDanmuOutputAggregation(),
      ])
      setLimit(limitRes.data?.value ?? '-1')
      setEnable(enableRes.data?.value === 'true' ? true : false)
    } catch (e) {
      console.log(e)
      messageApi.error('获取配置失败')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    setSaveLoading(true)
    try {
      await Promise.all([
        setDanmuOutputTotal({ value: `${limit}` }),
        setDanmuOutputAggregation({ value: enable ? 'true' : 'false' }),
      ])
      messageApi.success('保存成功')
    } catch (e) {
      messageApi.error('保存失败')
    } finally {
      setSaveLoading(false)
    }
  }

  useEffect(() => {
    getConfig()
  }, [])

  return (
    <div className="my-6">
      <Card loading={loading} title="弹幕输出控制">
        <div>在这里调整弹幕API的输出行为。</div>
        <div className="my-4">
          <div className="flex items-center justify-start gap-4 mb-2">
            <div>单源输出总数</div>
            <InputNumber value={limit} onChange={v => setLimit(v)} />
          </div>
          <div>
            设置从单个数据源（或聚合后）返回的弹幕最大数量。-1
            表示无限制。为防止客户端卡顿，建议设置一个合理的数值（如
            3000-5000）。弹幕将从视频时长中均匀采样。
          </div>
        </div>
        <div className="my-4">
          <div className="flex items-center justify-start gap-4 mb-2">
            <div>启用弹幕聚合</div>
            <Switch checked={enable} onChange={v => setEnable(v)} />
          </div>
          <div>
            开启后，当播放器请求关联弹幕时（withRelated=true），系统将聚合所有源的弹幕。关闭则只返回当前源的弹幕。
          </div>
        </div>
        <div className="flex items-center justify-end">
          <Button
            type="primary"
            loading={saveLoading}
            onClick={() => {
              handleSave()
            }}
          >
            保存设置
          </Button>
        </div>
      </Card>
    </div>
  )
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Card, Input, Button, Space, Tag, Collapse, Spin, Empty,
  Statistic, Row, Col, Select, InputNumber,
} from 'antd'
import {
  BugOutlined, SearchOutlined, CheckCircleOutlined,
  CloseCircleOutlined, ClockCircleOutlined,
} from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import { matchTrace } from '../../../apis'

export const MatchDebugger = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const [title, setTitle] = useState('')
  const [season, setSeason] = useState(null)
  const [episode, setEpisode] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const handleTrace = async () => {
    if (!title.trim()) return
    setLoading(true)
    setResult(null)
    try {
      const res = await matchTrace({
        title: title.trim(),
        season: season || undefined,
        episode: episode || undefined,
      })
      setResult(res.data)
    } catch (err) {
      setResult({ error: err.message })
    } finally { setLoading(false) }
  }

  const renderStepContent = (step) => {
    if (!step) return null
    return (
      <div className="space-y-2 text-xs">
        {step.input_data && (
          <div>
            <span className="font-semibold text-gray-500">{t('matchDebugger.input')}:</span>
            <pre className="bg-gray-50 dark:bg-gray-800 p-2 rounded mt-1 overflow-auto max-h-40 whitespace-pre-wrap">
              {JSON.stringify(step.input_data, null, 2)}
            </pre>
          </div>
        )}
        {step.output_data && (
          <div>
            <span className="font-semibold text-gray-500">{t('matchDebugger.output')}:</span>
            <pre className="bg-green-50 dark:bg-green-900/20 p-2 rounded mt-1 overflow-auto max-h-60 whitespace-pre-wrap">
              {JSON.stringify(step.output_data, null, 2)}
            </pre>
          </div>
        )}
        {step.details && (
          <div className="text-orange-500">{step.details}</div>
        )}
      </div>
    )
  }

  const collapseItems = result?.steps?.map((step, idx) => ({
    key: idx,
    label: (
      <div className="flex items-center gap-2">
        {step.success
          ? <CheckCircleOutlined className="text-green-500" />
          : <CloseCircleOutlined className="text-red-500" />}
        <span className="font-medium">{step.name}</span>
        <Tag size="small"><ClockCircleOutlined /> {step.duration_ms?.toFixed(0)}ms</Tag>
      </div>
    ),
    children: renderStepContent(step),
  })) || []

  return (
    <div className="space-y-4">
      {/* 输入区 */}
      <Card size="small" title={
        <Space><BugOutlined />{t('matchDebugger.title')}</Space>
      }>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1" style={{ minWidth: 200 }}>
            <label className="text-xs text-gray-500 mb-1 block">{t('matchDebugger.inputTitle')}</label>
            <Input value={title} onChange={e => setTitle(e.target.value)}
              placeholder={t('matchDebugger.titlePlaceholder')}
              onPressEnter={handleTrace} />
          </div>
          <div style={{ width: 80 }}>
            <label className="text-xs text-gray-500 mb-1 block">{t('matchDebugger.season')}</label>
            <InputNumber value={season} onChange={setSeason} min={1}
              placeholder="S" style={{ width: '100%' }} />
          </div>
          <div style={{ width: 80 }}>
            <label className="text-xs text-gray-500 mb-1 block">{t('matchDebugger.episode')}</label>
            <InputNumber value={episode} onChange={setEpisode} min={1}
              placeholder="E" style={{ width: '100%' }} />
          </div>
          <Button type="primary" icon={<SearchOutlined />} onClick={handleTrace}
            loading={loading}>
            {t('matchDebugger.run')}
          </Button>
        </div>
      </Card>

      {/* 结果区 */}
      {loading && <div className="text-center py-12"><Spin size="large" /></div>}

      {result && !result.error && (
        <>
          <Row gutter={[12, 12]}>
            <Col xs={8}><Card size="small">
              <Statistic title={t('matchDebugger.steps')} value={result.steps?.length || 0} />
            </Card></Col>
            <Col xs={8}><Card size="small">
              <Statistic title={t('matchDebugger.results')} value={result.result_count || 0} />
            </Card></Col>
            <Col xs={8}><Card size="small">
              <Statistic title={t('matchDebugger.totalTime')}
                value={result.total_duration_ms?.toFixed(0) || 0} suffix="ms" />
            </Card></Col>
          </Row>

          <Card size="small" bodyStyle={{ padding: 0 }}>
            <Collapse items={collapseItems} defaultActiveKey={collapseItems.map((_, i) => i)} size="small" />
          </Card>
        </>
      )}

      {result?.error && (
        <Card size="small"><Tag color="red">{result.error}</Tag></Card>
      )}

      {!loading && !result && (
        <Empty description={t('matchDebugger.hint')} />
      )}
    </div>
  )
}

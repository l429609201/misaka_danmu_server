import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Col, Empty, Progress, Row, Statistic, Tag, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { getDashboardOverview } from '@/apis'
import { MyIcon } from '@/components/MyIcon'
import { featureUrl } from '@/components/FeatureSearch'
import { FEATURES } from '@/configs/features'

// 快捷操作：直接复用全功能搜索的功能索引（标题/图标/跳转目标单一数据源）
const QUICK_ACTION_IDS = [
  'bullet-token', 'subscriptions', 'setting-webhook', 'task-schedule',
  'source-scrapers', 'setting-notification', 'setting-recognition', 'setting-proxy',
]
// 个别入口在快捷卡片里用更口语的名字
const QUICK_LABEL_KEYS = { 'bullet-token': 'dashboard.quickDanmuApi' }
const QUICK_ACTIONS = QUICK_ACTION_IDS.map(id => FEATURES.find(f => f.id === id)).filter(Boolean)

const QuickActionsCard = ({ t }) => {
  const navigate = useNavigate()
  return (
    <Card title={t('dashboard.quickActions')} type="inner">
      <Row gutter={[12, 12]}>
        {QUICK_ACTIONS.map(f => (
          <Col key={f.id} xs={12} sm={8} md={6} lg={3}>
            <Button block className="!h-auto !py-3 flex flex-col items-center gap-1" onClick={() => navigate(featureUrl(f))}>
              <MyIcon icon={f.icon} size={22} />
              <span className="text-xs whitespace-normal leading-tight">{t(QUICK_LABEL_KEYS[f.id] || f.titleKey)}</span>
            </Button>
          </Col>
        ))}
      </Row>
    </Card>
  )
}

// 自动刷新间隔（毫秒）
const REFRESH_INTERVAL = 30000

const TYPE_LABELS = {
  tv_series: 'libraryGroup.typeTv',
  movie: 'libraryGroup.typeMovie',
  ova: 'libraryGroup.typeOva',
  other: 'libraryGroup.typeOther',
  variety: 'libraryGroup.typeVariety',
  short_drama: 'libraryGroup.typeShortDrama',
}

const formatBytes = (bytes = 0) => {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value.toFixed(i ? 1 : 0)} ${units[i]}`
}

const percentColor = (p) => (p >= 90 ? '#ff4d4f' : p >= 75 ? '#faad14' : undefined)

const LibraryCard = ({ data, t }) => {
  const coverage = data.episode_total ? Math.round((data.episode_with_danmaku / data.episode_total) * 100) : 0
  const byType = Object.entries(data.anime_by_type || {})
  return (
    <Card title={t('dashboard.library')} type="inner" className="h-full">
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}><Statistic title={t('dashboard.animeTotal')} value={data.anime_total} suffix={t('dashboard.animeUnit')} /></Col>
        <Col xs={12} md={6}><Statistic title={t('dashboard.episodeTotal')} value={data.episode_total} suffix={t('dashboard.episodeUnit')} /></Col>
        <Col xs={12} md={6}><Statistic title={t('dashboard.withDanmaku')} value={data.episode_with_danmaku} suffix={t('dashboard.episodeUnit')} /></Col>
        <Col xs={12} md={6}>
          <div className="text-sm opacity-60 mb-1">{t('dashboard.coverage')}</div>
          <Progress percent={coverage} size="small" />
        </Col>
        <Col xs={12} md={6}><Statistic title={t('dashboard.today')} value={data.anime_today} suffix={`${t('dashboard.animeUnit')} / ${data.episode_today}${t('dashboard.episodeUnit')}`} /></Col>
        <Col xs={12} md={6}><Statistic title={t('dashboard.week')} value={data.anime_week} suffix={`${t('dashboard.animeUnit')} / ${data.episode_week}${t('dashboard.episodeUnit')}`} /></Col>
        <Col xs={24} md={12}>
          <div className="text-sm opacity-60 mb-2">{t('dashboard.byType')}</div>
          <div className="flex flex-wrap gap-2">
            {byType.length ? byType.map(([type, count]) => (
              <Tag key={type}>{TYPE_LABELS[type] ? t(TYPE_LABELS[type]) : type}: {count}</Tag>
            )) : '-'}
          </div>
        </Col>
      </Row>
    </Card>
  )
}

const SourcesCard = ({ data, t }) => (
  <Card title={t('dashboard.sources')} type="inner" className="h-full">
    <Row gutter={16}>
      <Col span={12}><Statistic title={t('dashboard.scrapers')} value={data.scraper_enabled} suffix={`/ ${data.scraper_total} ${t('dashboard.enabled')}`} /></Col>
      <Col span={12}><Statistic title={t('dashboard.metadata')} value={data.metadata_enabled} suffix={`/ ${data.metadata_total} ${t('dashboard.enabled')}`} /></Col>
    </Row>
  </Card>
)

const TasksCard = ({ data, t }) => (
  <Card title={t('dashboard.tasks')} type="inner" className="h-full">
    <Row gutter={16}>
      <Col span={8}><Statistic title={t('dashboard.running')} value={data.running?.length || 0} /></Col>
      <Col span={8}><Statistic title={t('dashboard.pending')} value={data.pendingCount || 0} /></Col>
      <Col span={8}><Statistic title={t('dashboard.paused')} value={data.pausedCount || 0} /></Col>
    </Row>
    <div className="mt-3 flex flex-col gap-1">
      {data.running?.length
        ? data.running.map(task => <div key={task.taskId} className="truncate text-sm">▶ {task.title}</div>)
        : <div className="text-sm opacity-60">{t('dashboard.noRunning')}</div>}
    </div>
    <div className="mt-3 text-sm opacity-60 mb-1">{t('dashboard.last24h')}</div>
    <div className="flex flex-wrap gap-2">
      {Object.entries(data.last24h || {}).map(([status, count]) => <Tag key={status}>{status}: {count}</Tag>)}
    </div>
  </Card>
)

const MediaServersCard = ({ data, t }) => (
  <Card title={t('dashboard.mediaServers')} type="inner" className="h-full">
    {data.length ? (
      <div className="flex flex-col gap-2">
        {data.map(server => (
          <div key={server.id} className="flex items-center justify-between">
            <span className="truncate">{server.name} <span className="opacity-60 text-sm">({server.providerName})</span></span>
            <Tag color={server.isEnabled ? 'green' : 'default'}>{server.isEnabled ? t('dashboard.enabled') : '-'}</Tag>
          </div>
        ))}
      </div>
    ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('dashboard.noMediaServer')} />}
  </Card>
)

const SystemCard = ({ data, t }) => {
  const rows = [
    { key: 'cpu', percent: data.cpuPercent },
    { key: 'memory', percent: data.memoryPercent, detail: `${formatBytes(data.memoryUsed)} / ${formatBytes(data.memoryTotal)}` },
    { key: 'disk', percent: data.diskPercent, detail: `${formatBytes(data.diskUsed)} / ${formatBytes(data.diskTotal)}` },
  ]
  return (
    <Card title={t('dashboard.system')} type="inner" className="h-full">
      {rows.map(row => (
        <div key={row.key} className="mb-2">
          <div className="flex justify-between text-sm">
            <span>{t(`dashboard.${row.key}`)}</span>
            <span className="opacity-60">{row.detail}</span>
          </div>
          <Progress percent={Math.round(row.percent || 0)} size="small" strokeColor={percentColor(row.percent || 0)} />
        </div>
      ))}
      <div className="text-sm opacity-60">{t('dashboard.process')}: {formatBytes(data.processMemory)}</div>
    </Card>
  )
}

export const Dashboard = () => {
  const { t } = useTranslation()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res = await getDashboardOverview()
      setData(res.data)
    } catch (e) {
      if (!silent) message.error(t('dashboard.loadFailed'))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [t])

  useEffect(() => {
    load()
    const timer = setInterval(() => load(true), REFRESH_INTERVAL)
    return () => clearInterval(timer)
  }, [load])

  return (
    // why：与其他页面统一，整个仪表盘由一张大卡片包裹，标题与刷新放在卡片头部
    <Card
      className="my-4"
      title={t('dashboard.title')}
      extra={
        <div className="flex items-center gap-3">
          {data?.generatedAt && <span className="text-sm opacity-60">{t('dashboard.updatedAt', { time: dayjs(data.generatedAt).format('HH:mm:ss') })}</span>}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>{t('dashboard.refresh')}</Button>
        </div>
      }
    >
      {data ? (
        <Row gutter={[16, 16]}>
          <Col xs={24}><QuickActionsCard t={t} /></Col>
          <Col xs={24}><LibraryCard data={data.library || {}} t={t} /></Col>
          <Col xs={24} lg={12}><TasksCard data={data.tasks || {}} t={t} /></Col>
          <Col xs={24} lg={12}><SystemCard data={data.system || {}} t={t} /></Col>
          <Col xs={24} lg={12}><SourcesCard data={data.sources || {}} t={t} /></Col>
          <Col xs={24} lg={12}><MediaServersCard data={data.mediaServers || []} t={t} /></Col>
        </Row>
      ) : <Card type="inner" loading />}
    </Card>
  )
}

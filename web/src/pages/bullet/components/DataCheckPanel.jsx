import { Card, Button, List, Tag, Space, Modal, Spin, Alert, message, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { scanDataIssues, fixOrphanEpisodes, clearBrokenMappings } from '@/apis'
import { MedicineBoxOutlined, ReloadOutlined, ExclamationCircleOutlined } from '@ant-design/icons'

const severityConfig = { info: { color: 'blue' }, warning: { color: 'orange' }, error: { color: 'red' } }

/** 根据 category 渲染具体条目列表 */
const renderItems = (category, items, t) => {
  if (!items?.length) return null
  const formatSeason = s => s != null ? ` S${s}` : ''
  const labels = {
    missing_metadata: it => `${it.title || it.id}${formatSeason(it.season)}`,
    zero_danmaku: it => `${it.title || it.id} (E${it.episodeIndex ?? '?'})`,
    duplicate_anime: it => `${it.title}${formatSeason(it.season)} ×${it.count}`,
    broken_mapping: it => `ID:${it.animeId} (${it.serverType})`,
  }
  const fn = labels[category] || (it => JSON.stringify(it))
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {items.slice(0, 20).map((it, i) => (
        <Tag key={i} className="text-xs">{fn(it)}</Tag>
      ))}
      {items.length > 20 && <Tag className="text-xs">…{t('dataCheck.andMore', { count: items.length - 20 })}</Tag>}
    </div>
  )
}

export const DataCheckPanel = () => {
  const { t } = useTranslation()
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)

  const doScan = () => {
    setLoading(true)
    scanDataIssues(50).then(res => { const d = res?.data; setResults(Array.isArray(d) ? d : []); setLoading(false) }).catch(() => setLoading(false))
  }

  const handleFix = async (category) => {
    Modal.confirm({
      title: t('dataCheck.confirmFix'),
      icon: <ExclamationCircleOutlined />,
      onOk: async () => {
        try {
          if (category === 'orphan_episodes') await fixOrphanEpisodes()
          else if (category === 'broken_mapping') await clearBrokenMappings()
          message.success(t('dataCheck.fixSuccess'))
          doScan()
        } catch { message.error(t('dataCheck.fixFailed')) }
      },
    })
  }

  return (
    <Card title={<><MedicineBoxOutlined className="mr-2" />{t('dataCheck.title')}</>} size="small"
      extra={<Button icon={<ReloadOutlined />} onClick={doScan} loading={loading} size="small">{t('dataCheck.scan')}</Button>}>
      {loading ? <Spin className="w-full flex justify-center py-4" /> : results.length === 0 ? (
        <Alert type="success" message={t('dataCheck.noIssues')} showIcon />
      ) : (
        <List size="small" dataSource={results} renderItem={item => (
          <List.Item actions={
            (item.category === 'orphan_episodes' || item.category === 'broken_mapping')
              ? [<Button size="small" type="link" danger onClick={() => handleFix(item.category)}>{t('dataCheck.fix')}</Button>]
              : undefined
          }>
            <List.Item.Meta
              title={
                <Space>
                  <Tag color={severityConfig[item.severity]?.color}>{t(`dataCheck.severity.${item.severity}`)}</Tag>
                  <span>{t(`dataCheck.category.${item.category}`, item.category)}</span>
                  <Tag>{item.count}</Tag>
                </Space>
              }
              description={
                <>
                  <Typography.Text type="secondary" className="text-xs">{t(`dataCheck.suggestion.${item.category}`, item.suggestion)}</Typography.Text>
                  {renderItems(item.category, item.items, t)}
                </>
              }
            />
          </List.Item>
        )} />
      )}
    </Card>
  )
}

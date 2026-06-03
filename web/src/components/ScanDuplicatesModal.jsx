import { useState, useCallback } from 'react'
import { Modal, Button, Radio, Switch, Empty, Spin, Progress, Tag, Space, Tooltip, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { ExclamationCircleOutlined } from '@ant-design/icons'
import { scanDuplicates, batchMergeAnimes } from '../apis'
import { useMessage } from '../MessageContext'
import { MyIcon } from './MyIcon'

const { Text } = Typography

// 阶段: idle → scanning → preview → confirming → merging → done
export const ScanDuplicatesModal = ({ open, onCancel, onSuccess }) => {
  const { t } = useTranslation()
  const messageApi = useMessage()
  const [stage, setStage] = useState('idle') // idle | scanning | preview | confirming | merging | done
  const [strict, setStrict] = useState(true)
  const [groups, setGroups] = useState([])
  const [selections, setSelections] = useState({}) // groupIndex → animeId (保留项)
  const [mergeResults, setMergeResults] = useState([])
  const [mergeProgress, setMergeProgress] = useState({ current: 0, total: 0 })

  const reset = useCallback(() => {
    setStage('idle')
    setGroups([])
    setSelections({})
    setMergeResults([])
    setMergeProgress({ current: 0, total: 0 })
  }, [])

  const handleClose = () => {
    if (stage === 'done') onSuccess?.()
    reset()
    onCancel()
  }

  // 扫描
  const handleScan = async () => {
    setStage('scanning')
    try {
      const res = await scanDuplicates(strict)
      const data = res.data
      if (!data.groups?.length) {
        setStage('idle')
        messageApi.success(t('scanDuplicates.noDuplicates'))
        return
      }
      setGroups(data.groups)
      // 默认选中每组中 sourceCount 最多的
      const defaultSelections = {}
      data.groups.forEach((g, i) => {
        const best = g.items.reduce((a, b) => (b.sourceCount > a.sourceCount ? b : a), g.items[0])
        defaultSelections[i] = best.animeId
      })
      setSelections(defaultSelections)
      setStage('preview')
    } catch (e) {
      messageApi.error(t('scanDuplicates.scanFailed') + ': ' + (e.message || t('scanDuplicates.unknownError')))
      setStage('idle')
    }
  }

  // 确认 → 执行合并
  const handleMerge = async () => {
    setStage('merging')
    const operations = groups.map((g, i) => ({
      targetAnimeId: selections[i],
      sourceAnimeIds: g.items.filter(item => item.animeId !== selections[i]).map(item => item.animeId),
    }))
    setMergeProgress({ current: 0, total: operations.length })

    try {
      const res = await batchMergeAnimes({ operations })
      setMergeResults(res.data.results || [])
      setMergeProgress({ current: operations.length, total: operations.length })
      setStage('done')
      if (res.data.failCount > 0) {
        messageApi.warning(t('scanDuplicates.mergeDoneWithFail', { success: res.data.successCount, fail: res.data.failCount }))
      } else {
        messageApi.success(t('scanDuplicates.mergeDoneAllSuccess', { success: res.data.successCount }))
      }
    } catch (e) {
      messageApi.error(t('scanDuplicates.mergeFailed') + ': ' + (e.message || t('scanDuplicates.unknownError')))
      setStage('preview')
    }
  }

  const getImageSrc = (item) => {
    let src = item.localImagePath || item.imageUrl
    if (src && src.startsWith('/images/')) src = src.replace('/images/', '/data/images/')
    return src
  }

  // 渲染扫描前的初始界面
  const renderIdle = () => (
    <div className="text-center py-8">
      <div className="mb-4 text-gray-500">
        {t('scanDuplicates.idleDesc')}
      </div>
      <div className="flex items-center justify-center gap-2 mb-6">
        <Text>{t('scanDuplicates.mode')}</Text>
        <Switch
          checked={strict}
          onChange={setStrict}
          checkedChildren={t('scanDuplicates.strict')}
          unCheckedChildren={t('scanDuplicates.loose')}
        />
        <Tooltip title={t('scanDuplicates.modeTip')}>
          <ExclamationCircleOutlined className="text-gray-400" />
        </Tooltip>
      </div>
      <Button type="primary" size="large" onClick={handleScan}>
        {t('scanDuplicates.startScan')}
      </Button>
    </div>
  )

  const renderScanning = () => (
    <div className="text-center py-12">
      <Spin size="large" />
      <div className="mt-4 text-gray-500">{t('scanDuplicates.scanning')}</div>
    </div>
  )

  // 预览重复组
  const renderPreview = () => (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <Text type="secondary">
          {t('scanDuplicates.foundGroups', { groups: groups.length, items: groups.reduce((s, g) => s + g.items.length, 0) })}
        </Text>
        <div className="flex items-center gap-2">
          <Text type="secondary">{t('scanDuplicates.mode')}</Text>
          <Switch checked={strict} onChange={(v) => { setStrict(v); handleScan() }}
            checkedChildren={t('scanDuplicates.strict')} unCheckedChildren={t('scanDuplicates.loose')} size="small" />
        </div>
      </div>
      <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
        {groups.map((group, gi) => (
          <div key={gi} className="border rounded-lg p-3 dark:border-gray-700">
            <div className="font-medium mb-2 flex items-center gap-2">
              <Tag color="blue">TMDB: {group.tmdbId}</Tag>
              {group.season != null && <Tag>Season {String(group.season).padStart(2, '0')}</Tag>}
              <Text type="secondary" className="text-xs">{t('scanDuplicates.itemCount', { count: group.items.length })}</Text>
            </div>
            <Radio.Group
              value={selections[gi]}
              onChange={(e) => setSelections(prev => ({ ...prev, [gi]: e.target.value }))}
              className="w-full"
            >
              <div className="space-y-2">
                {group.items.map((item) => (
                  <div key={item.animeId}
                    className={`flex items-center gap-3 p-2 rounded cursor-pointer transition-colors ${selections[gi] === item.animeId ? 'bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-300' : 'hover:bg-gray-50 dark:hover:bg-gray-800/30'}`}
                    onClick={() => setSelections(prev => ({ ...prev, [gi]: item.animeId }))}
                  >
                    <Radio value={item.animeId} />
                    {getImageSrc(item) ? (
                      <img src={getImageSrc(item)} className="w-10 h-14 object-cover rounded" alt="" />
                    ) : (
                      <div className="w-10 h-14 bg-gray-200 dark:bg-gray-700 rounded flex items-center justify-center">
                        <MyIcon icon="image" size={16} />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium truncate">{item.title}</div>
                      <div className="text-xs text-gray-500">
                        ID:{item.animeId} · S{String(item.season).padStart(2, '0')} · {t('scanDuplicates.sourceCount', { count: item.sourceCount })}
                        {item.year ? ` · ${t('scanDuplicates.yearSuffix', { year: item.year })}` : ''}
                      </div>
                    </div>
                    {selections[gi] === item.animeId && (
                      <Tag color="green" className="shrink-0">{t('scanDuplicates.keep')}</Tag>
                    )}
                  </div>
                ))}
              </div>
            </Radio.Group>
          </div>
        ))}
      </div>
    </div>
  )

  // 确认弹窗内容
  const renderConfirming = () => (
    <div>
      <div className="mb-3 flex items-center gap-2 text-orange-500">
        <ExclamationCircleOutlined />
        <Text strong>{t('scanDuplicates.confirmTip')}</Text>
      </div>
      <div className="space-y-2 max-h-[50vh] overflow-y-auto">
        {groups.map((group, gi) => {
          const target = group.items.find(i => i.animeId === selections[gi])
          const sources = group.items.filter(i => i.animeId !== selections[gi])
          return (
            <div key={gi} className="border rounded p-2 dark:border-gray-700 text-sm">
              <div className="font-medium">{gi + 1}. {target?.title || t('scanDuplicates.unknown')} (TMDB: {group.tmdbId})</div>
              <div className="text-gray-500 ml-4">
                {sources.map(s => `ID:${s.animeId} ${s.title}`).join('、')} → {t('scanDuplicates.mergeTo')} → ID:{target?.animeId}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 text-orange-500 text-sm">
        {t('scanDuplicates.irreversibleTip')}
      </div>
    </div>
  )

  // 合并中
  const renderMerging = () => (
    <div className="text-center py-8">
      <Progress percent={mergeProgress.total ? Math.round((mergeProgress.current / mergeProgress.total) * 100) : 0} />
      <div className="mt-2 text-gray-500">{t('scanDuplicates.merging')} {mergeProgress.current}/{mergeProgress.total}</div>
    </div>
  )

  // 完成
  const renderDone = () => (
    <div className="text-center py-8">
      <div className="text-4xl mb-4">🎉</div>
      <div className="text-lg font-medium mb-2">{t('scanDuplicates.mergeComplete')}</div>
      <div className="text-gray-500">
        {t('scanDuplicates.successCount', { count: mergeResults.filter(r => r.success).length })}
        {mergeResults.some(r => !r.success) && (
          <span className="text-red-500">{t('scanDuplicates.failCount', { count: mergeResults.filter(r => !r.success).length })}</span>
        )}
      </div>
    </div>
  )

  const getTitle = () => {
    if (stage === 'confirming') return t('scanDuplicates.titleConfirm')
    if (stage === 'merging') return t('scanDuplicates.titleMerging')
    if (stage === 'done') return t('scanDuplicates.titleDone')
    return t('scanDuplicates.titleScan')
  }

  const getFooter = () => {
    if (stage === 'idle' || stage === 'scanning') return null
    if (stage === 'preview') return (
      <Space>
        <Button onClick={handleClose}>{t('common.cancel')}</Button>
        <Button type="primary" danger onClick={() => setStage('confirming')}>
          {t('scanDuplicates.mergeSelected', { count: groups.length })}
        </Button>
      </Space>
    )
    if (stage === 'confirming') return (
      <Space>
        <Button onClick={() => setStage('preview')}>{t('common.back')}</Button>
        <Button type="primary" danger onClick={handleMerge}>{t('scanDuplicates.titleConfirm')}</Button>
      </Space>
    )
    if (stage === 'merging') return null
    if (stage === 'done') return <Button type="primary" onClick={handleClose}>{t('common.close')}</Button>
  }

  return (
    <Modal
      title={getTitle()}
      open={open}
      onCancel={handleClose}
      footer={getFooter()}
      width={640}
      destroyOnHidden
    >
      {stage === 'idle' && renderIdle()}
      {stage === 'scanning' && renderScanning()}
      {stage === 'preview' && renderPreview()}
      {stage === 'confirming' && renderConfirming()}
      {stage === 'merging' && renderMerging()}
      {stage === 'done' && renderDone()}
    </Modal>
  )
}


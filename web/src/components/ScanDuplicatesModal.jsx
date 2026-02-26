import { useState, useCallback } from 'react'
import { Modal, Button, Radio, Switch, Empty, Spin, Progress, Tag, Space, Tooltip, Typography } from 'antd'
import { ExclamationCircleOutlined } from '@ant-design/icons'
import { scanDuplicates, batchMergeAnimes } from '../apis'
import { useMessage } from '../MessageContext'
import { MyIcon } from './MyIcon'

const { Text } = Typography

// 阶段: idle → scanning → preview → confirming → merging → done
export const ScanDuplicatesModal = ({ open, onCancel, onSuccess }) => {
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
        messageApi.success('没有发现重复项，弹幕库中所有媒体都是唯一的')
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
      messageApi.error('扫描失败: ' + (e.message || '未知错误'))
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
        messageApi.warning(`合并完成: ${res.data.successCount} 成功, ${res.data.failCount} 失败`)
      } else {
        messageApi.success(`合并完成: ${res.data.successCount} 组全部成功`)
      }
    } catch (e) {
      messageApi.error('合并失败: ' + (e.message || '未知错误'))
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
        基于 TMDB ID 识别弹幕库中的重复条目，将多个相同作品合并为一个。
      </div>
      <div className="flex items-center justify-center gap-2 mb-6">
        <Text>模式：</Text>
        <Switch
          checked={strict}
          onChange={setStrict}
          checkedChildren="严格"
          unCheckedChildren="宽松"
        />
        <Tooltip title="严格模式按 TMDB ID + 季度 匹配；宽松模式仅按 TMDB ID 匹配（适用于剧集组导致季度不同的情况）">
          <ExclamationCircleOutlined className="text-gray-400" />
        </Tooltip>
      </div>
      <Button type="primary" size="large" onClick={handleScan}>
        开始扫描
      </Button>
    </div>
  )

  const renderScanning = () => (
    <div className="text-center py-12">
      <Spin size="large" />
      <div className="mt-4 text-gray-500">正在扫描弹幕库...</div>
    </div>
  )

  // 预览重复组
  const renderPreview = () => (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <Text type="secondary">
          发现 {groups.length} 组重复媒体，共涉及 {groups.reduce((s, g) => s + g.items.length, 0)} 个条目
        </Text>
        <div className="flex items-center gap-2">
          <Text type="secondary">模式：</Text>
          <Switch checked={strict} onChange={(v) => { setStrict(v); handleScan() }}
            checkedChildren="严格" unCheckedChildren="宽松" size="small" />
        </div>
      </div>
      <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
        {groups.map((group, gi) => (
          <div key={gi} className="border rounded-lg p-3 dark:border-gray-700">
            <div className="font-medium mb-2 flex items-center gap-2">
              <Tag color="blue">TMDB: {group.tmdbId}</Tag>
              {group.season != null && <Tag>Season {String(group.season).padStart(2, '0')}</Tag>}
              <Text type="secondary" className="text-xs">({group.items.length} 个条目)</Text>
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
                        ID:{item.animeId} · S{String(item.season).padStart(2, '0')} · {item.sourceCount}个源
                        {item.year ? ` · ${item.year}年` : ''}
                      </div>
                    </div>
                    {selections[gi] === item.animeId && (
                      <Tag color="green" className="shrink-0">保留</Tag>
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
        <Text strong>即将执行以下合并操作：</Text>
      </div>
      <div className="space-y-2 max-h-[50vh] overflow-y-auto">
        {groups.map((group, gi) => {
          const target = group.items.find(i => i.animeId === selections[gi])
          const sources = group.items.filter(i => i.animeId !== selections[gi])
          return (
            <div key={gi} className="border rounded p-2 dark:border-gray-700 text-sm">
              <div className="font-medium">{gi + 1}. {target?.title || '未知'} (TMDB: {group.tmdbId})</div>
              <div className="text-gray-500 ml-4">
                {sources.map(s => `ID:${s.animeId} ${s.title}`).join('、')} → 合并到 → ID:{target?.animeId}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 text-orange-500 text-sm">
        ⚠️ 此操作不可撤销，被合并条目将被删除，其数据源和弹幕文件将转移到保留条目下。
      </div>
    </div>
  )

  // 合并中
  const renderMerging = () => (
    <div className="text-center py-8">
      <Progress percent={mergeProgress.total ? Math.round((mergeProgress.current / mergeProgress.total) * 100) : 0} />
      <div className="mt-2 text-gray-500">正在合并... {mergeProgress.current}/{mergeProgress.total}</div>
    </div>
  )

  // 完成
  const renderDone = () => (
    <div className="text-center py-8">
      <div className="text-4xl mb-4">🎉</div>
      <div className="text-lg font-medium mb-2">合并完成</div>
      <div className="text-gray-500">
        成功 {mergeResults.filter(r => r.success).length} 项
        {mergeResults.some(r => !r.success) && (
          <span className="text-red-500">，失败 {mergeResults.filter(r => !r.success).length} 项</span>
        )}
      </div>
    </div>
  )

  const getTitle = () => {
    if (stage === 'confirming') return '确认合并'
    if (stage === 'merging') return '合并中...'
    if (stage === 'done') return '合并完成'
    return '扫描重复项'
  }

  const getFooter = () => {
    if (stage === 'idle' || stage === 'scanning') return null
    if (stage === 'preview') return (
      <Space>
        <Button onClick={handleClose}>取消</Button>
        <Button type="primary" danger onClick={() => setStage('confirming')}>
          合并选中 ({groups.length}组)
        </Button>
      </Space>
    )
    if (stage === 'confirming') return (
      <Space>
        <Button onClick={() => setStage('preview')}>返回</Button>
        <Button type="primary" danger onClick={handleMerge}>确认合并</Button>
      </Space>
    )
    if (stage === 'merging') return null
    if (stage === 'done') return <Button type="primary" onClick={handleClose}>关闭</Button>
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


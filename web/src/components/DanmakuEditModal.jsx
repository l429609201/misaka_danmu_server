import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Modal,
  Tabs,
  Select,
  InputNumber,
  Button,
  Table,
  Checkbox,
  Spin,
  Empty,
  Tag,
  Input,
  Divider,
  Progress,
} from 'antd'
import {
  InfoCircleOutlined,
  ClockCircleOutlined,
  ScissorOutlined,
  MergeCellsOutlined,
  PlusOutlined,
  DeleteOutlined,
  HolderOutlined,
} from '@ant-design/icons'
import { useMessage } from '../MessageContext'
import {
  getDanmakuEditDetail,
  applyDanmakuOffset,
  splitEpisodeDanmaku,
  mergeEpisodesDanmaku,
} from '../apis'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

// 格式化时间显示
const formatTime = (seconds) => {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

// 可拖拽的合并项组件
const SortableMergeItem = ({ item, onOffsetChange, onRemove }) => {
  const { t } = useTranslation()
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.episodeId })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={{ ...style, backgroundColor: 'var(--color-hover)' }}
      className="flex items-center gap-3 p-3 mb-2 rounded border border-gray-200 dark:border-gray-700"
    >
      <div {...attributes} {...listeners} className="cursor-grab">
        <HolderOutlined className="text-gray-400 dark:text-gray-500" />
      </div>
      <div className="flex-1">
        <div className="font-medium">{t('danmakuEdit.episodeItem', { index: item.episodeIndex })}</div>
        <div className="text-sm text-gray-500 dark:text-gray-400 truncate">{item.title}</div>
        <div className="text-xs text-gray-400 dark:text-gray-500">{t('danmakuEdit.danmakuCount', { count: item.commentCount })}</div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm">{t('danmakuEdit.offset')}</span>
        <InputNumber
          value={item.offsetSeconds}
          onChange={(val) => onOffsetChange(item.episodeId, val || 0)}
          addonAfter={t('danmakuEdit.second')}
          style={{ width: 120 }}
        />
      </div>
      <Button
        type="text"
        danger
        icon={<DeleteOutlined />}
        onClick={() => onRemove(item.episodeId)}
      />
    </div>
  )
}

export const DanmakuEditModal = ({ open, onCancel, onSuccess, episodes }) => {
  const { t } = useTranslation()
  const messageApi = useMessage()
  const [activeTab, setActiveTab] = useState('detail')
  
  // 弹幕详情状态
  const [selectedDetailEpisode, setSelectedDetailEpisode] = useState(null)
  const [detailData, setDetailData] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  
  // 时间偏移状态
  const [offsetEpisodes, setOffsetEpisodes] = useState([])
  const [offsetValue, setOffsetValue] = useState(0)
  const [offsetLoading, setOffsetLoading] = useState(false)
  
  // 分集拆分状态
  const [splitSourceEpisode, setSplitSourceEpisode] = useState(null)
  const [splitConfigs, setSplitConfigs] = useState([])
  const [splitDeleteSource, setSplitDeleteSource] = useState(true)
  const [splitResetTime, setSplitResetTime] = useState(true)
  const [splitLoading, setSplitLoading] = useState(false)
  
  // 分集合并状态
  const [mergeEpisodes, setMergeEpisodes] = useState([])
  const [mergeTargetIndex, setMergeTargetIndex] = useState(1)
  const [mergeTargetTitle, setMergeTargetTitle] = useState('')
  const [mergeDeleteSources, setMergeDeleteSources] = useState(true)
  const [mergeDeduplicate, setMergeDeduplicate] = useState(false)
  const [mergeLoading, setMergeLoading] = useState(false)

  // 拖拽传感器
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  // 初始化
  useEffect(() => {
    if (open && episodes?.length > 0) {
      setSelectedDetailEpisode(episodes[0].episodeId)
      setOffsetEpisodes([])
      setSplitSourceEpisode(null)
      setSplitConfigs([])
      setMergeEpisodes([])
    }
  }, [open, episodes])

  // 加载弹幕详情
  useEffect(() => {
    if (selectedDetailEpisode && activeTab === 'detail') {
      loadDetailData(selectedDetailEpisode)
    }
  }, [selectedDetailEpisode, activeTab])

  const loadDetailData = async (episodeId) => {
    setDetailLoading(true)
    try {
      const res = await getDanmakuEditDetail(episodeId)
      setDetailData(res.data)
    } catch (error) {
      messageApi.error(t('danmakuEdit.fetchDetailFailed'))
      setDetailData(null)
    } finally {
      setDetailLoading(false)
    }
  }

  // 时间偏移处理
  const handleApplyOffset = async () => {
    if (offsetEpisodes.length === 0) {
      messageApi.warning(t('danmakuEdit.selectEpisodes'))
      return
    }
    if (offsetValue === 0) {
      messageApi.warning(t('danmakuEdit.offsetNotZero'))
      return
    }
    setOffsetLoading(true)
    try {
      await applyDanmakuOffset({
        episodeIds: offsetEpisodes,
        offsetSeconds: offsetValue,
      })
      messageApi.success(t('danmakuEdit.offsetApplied', { count: offsetEpisodes.length, offset: offsetValue }))
      onSuccess?.()
    } catch (error) {
      messageApi.error(t('danmakuEdit.applyOffsetFailed') + ': ' + error.message)
    } finally {
      setOffsetLoading(false)
    }
  }

  // 添加拆分配置
  const addSplitConfig = () => {
    const lastConfig = splitConfigs[splitConfigs.length - 1]
    const newIndex = lastConfig ? lastConfig.episodeIndex + 1 : 1
    const newStartTime = lastConfig ? lastConfig.endTime : 0
    setSplitConfigs([
      ...splitConfigs,
      {
        id: Date.now(),
        episodeIndex: newIndex,
        startTime: newStartTime,
        endTime: newStartTime + 1500, // 默认25分钟
        title: `第${newIndex}集`,
      },
    ])
  }

  // 删除拆分配置
  const removeSplitConfig = (id) => {
    setSplitConfigs(splitConfigs.filter((c) => c.id !== id))
  }

  // 更新拆分配置
  const updateSplitConfig = (id, field, value) => {
    setSplitConfigs(
      splitConfigs.map((c) => (c.id === id ? { ...c, [field]: value } : c))
    )
  }

  // 执行拆分
  const handleSplit = async () => {
    if (!splitSourceEpisode) {
      messageApi.warning(t('danmakuEdit.selectSourceEpisode'))
      return
    }
    if (splitConfigs.length === 0) {
      messageApi.warning(t('danmakuEdit.addSplitConfig'))
      return
    }
    setSplitLoading(true)
    try {
      const res = await splitEpisodeDanmaku({
        sourceEpisodeId: splitSourceEpisode,
        splits: splitConfigs.map((c) => ({
          episodeIndex: c.episodeIndex,
          startTime: c.startTime,
          endTime: c.endTime,
          title: c.title,
        })),
        deleteSource: splitDeleteSource,
        resetTime: splitResetTime,
      })
      if (res.data.success) {
        messageApi.success(t('danmakuEdit.splitSuccess', { count: res.data.newEpisodes.length }))
        onSuccess?.()
      } else {
        messageApi.error(res.data.error || t('danmakuEdit.splitFailed'))
      }
    } catch (error) {
      messageApi.error(t('danmakuEdit.splitFailed') + ': ' + error.message)
    } finally {
      setSplitLoading(false)
    }
  }

  // 添加合并分集
  const addMergeEpisode = (episodeId) => {
    const episode = episodes.find((e) => e.episodeId === episodeId)
    if (episode && !mergeEpisodes.find((e) => e.episodeId === episodeId)) {
      setMergeEpisodes([
        ...mergeEpisodes,
        { ...episode, offsetSeconds: 0 },
      ])
    }
  }

  // 移除合并分集
  const removeMergeEpisode = (episodeId) => {
    setMergeEpisodes(mergeEpisodes.filter((e) => e.episodeId !== episodeId))
  }

  // 更新合并分集偏移
  const updateMergeOffset = (episodeId, offset) => {
    setMergeEpisodes(
      mergeEpisodes.map((e) =>
        e.episodeId === episodeId ? { ...e, offsetSeconds: offset } : e
      )
    )
  }

  // 拖拽排序处理
  const handleDragEnd = (event) => {
    const { active, over } = event
    if (active.id !== over?.id) {
      setMergeEpisodes((items) => {
        const oldIndex = items.findIndex((i) => i.episodeId === active.id)
        const newIndex = items.findIndex((i) => i.episodeId === over.id)
        return arrayMove(items, oldIndex, newIndex)
      })
    }
  }

  // 执行合并
  const handleMerge = async () => {
    if (mergeEpisodes.length < 2) {
      messageApi.warning(t('danmakuEdit.selectAtLeast2'))
      return
    }
    if (!mergeTargetTitle.trim()) {
      messageApi.warning(t('danmakuEdit.inputTargetTitle'))
      return
    }
    setMergeLoading(true)
    try {
      const res = await mergeEpisodesDanmaku({
        sourceEpisodes: mergeEpisodes.map((e) => ({
          episodeId: e.episodeId,
          offsetSeconds: e.offsetSeconds,
        })),
        targetEpisodeIndex: mergeTargetIndex,
        targetTitle: mergeTargetTitle,
        deleteSources: mergeDeleteSources,
        deduplicate: mergeDeduplicate,
      })
      if (res.data.success) {
        messageApi.success(t('danmakuEdit.mergeSuccess', { count: res.data.commentCount }))
        onSuccess?.()
      } else {
        messageApi.error(res.data.error || t('danmakuEdit.mergeFailed'))
      }
    } catch (error) {
      messageApi.error(t('danmakuEdit.mergeFailed') + ': ' + error.message)
    } finally {
      setMergeLoading(false)
    }
  }

  // 计算时间分布的最大值（用于进度条）
  const maxDistribution = useMemo(() => {
    if (!detailData?.distribution) return 1
    return Math.max(...detailData.distribution.map((d) => d.count), 1)
  }, [detailData])

  // 可选的分集列表（排除已选择的）
  const availableMergeEpisodes = useMemo(() => {
    const selectedIds = mergeEpisodes.map((e) => e.episodeId)
    return episodes?.filter((e) => !selectedIds.includes(e.episodeId)) || []
  }, [episodes, mergeEpisodes])

  // 渲染弹幕详情标签页
  const renderDetailTab = () => (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 sm:gap-4">
        <span className="shrink-0">{t('danmakuEdit.selectEpisode')}</span>
        <Select
          value={selectedDetailEpisode}
          onChange={setSelectedDetailEpisode}
          className="flex-1 min-w-[200px]"
          style={{ maxWidth: 300 }}
          options={episodes?.map((e) => ({
            value: e.episodeId,
            label: t('danmakuEdit.episodeOption', { index: e.episodeIndex, title: e.title, count: e.commentCount }),
          }))}
        />
      </div>

      {detailLoading ? (
        <div className="flex justify-center py-8">
          <Spin />
        </div>
      ) : detailData ? (
        <div className="space-y-4">
          {/* 统计信息 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="p-3 bg-blue-50 dark:bg-blue-900/30 rounded">
              <div className="text-sm text-gray-500 dark:text-gray-400">{t('danmakuEdit.totalDanmaku')}</div>
              <div className="text-xl font-bold text-gray-900 dark:text-gray-100">{detailData.totalCount}</div>
            </div>
            <div className="p-3 bg-green-50 dark:bg-green-900/30 rounded">
              <div className="text-sm text-gray-500 dark:text-gray-400">{t('danmakuEdit.timeRange')}</div>
              <div className="text-xl font-bold text-gray-900 dark:text-gray-100">
                {formatTime(detailData.timeRange.start)} - {formatTime(detailData.timeRange.end)}
              </div>
            </div>
            <div className="p-3 bg-purple-50 dark:bg-purple-900/30 rounded">
              <div className="text-sm text-gray-500 dark:text-gray-400">{t('danmakuEdit.sourceCount')}</div>
              <div className="text-xl font-bold text-gray-900 dark:text-gray-100">{detailData.sources.length}</div>
            </div>
            <div className="p-3 bg-orange-50 dark:bg-orange-900/30 rounded">
              <div className="text-sm text-gray-500 dark:text-gray-400">{t('danmakuEdit.duration')}</div>
              <div className="text-xl font-bold text-gray-900 dark:text-gray-100">
                {t('danmakuEdit.minutes', { count: Math.ceil((detailData.timeRange.end - detailData.timeRange.start) / 60) })}
              </div>
            </div>
          </div>

          {/* 来源分布 */}
          <div>
            <div className="text-sm font-medium mb-2">{t('danmakuEdit.sourceDistribution')}</div>
            <div className="flex flex-wrap gap-2">
              {detailData.sources.map((s) => (
                <Tag key={s.name} color="blue">
                  {t('danmakuEdit.sourceCountSuffix', { name: s.name, count: s.count })}
                </Tag>
              ))}
            </div>
          </div>

          {/* 时间分布图 */}
          <div>
            <div className="text-sm font-medium mb-2">{t('danmakuEdit.timeDistribution')}</div>
            <div className="max-h-40 overflow-y-auto space-y-1">
              {detailData.distribution.map((d) => (
                <div key={d.minute} className="flex items-center gap-2 text-xs">
                  <span className="w-16 text-right">{t('danmakuEdit.minuteLabel', { minute: d.minute })}</span>
                  <Progress
                    percent={(d.count / maxDistribution) * 100}
                    showInfo={false}
                    size="small"
                    className="flex-1"
                  />
                  <span className="w-12">{t('danmakuEdit.countSuffix', { count: d.count })}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 弹幕预览 */}
          <div>
            <div className="text-sm font-medium mb-2">{t('danmakuEdit.danmakuPreview')}</div>
            <div className="max-h-60 overflow-y-auto border rounded border-gray-200 dark:border-gray-700">
              <Table
                dataSource={detailData.comments}
                columns={[
                  {
                    title: t('danmakuEdit.colTime'),
                    dataIndex: 'time',
                    width: 80,
                    render: (t) => formatTime(t),
                  },
                  { title: t('danmakuEdit.colContent'), dataIndex: 'content', ellipsis: true },
                  {
                    title: t('danmakuEdit.colSource'),
                    dataIndex: 'source',
                    width: 100,
                    render: (s) => <Tag>{s}</Tag>,
                  },
                ]}
                rowKey={(_, i) => i}
                size="small"
                pagination={false}
              />
            </div>
          </div>
        </div>
      ) : (
        <Empty description={t('danmakuEdit.noDanmakuData')} />
      )}
    </div>
  )

  // 渲染时间偏移标签页
  const renderOffsetTab = () => (
    <div className="space-y-4">
      <div className="p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded text-sm text-yellow-800 dark:text-yellow-200">
        <InfoCircleOutlined className="mr-2" />
        {t('danmakuEdit.offsetTip')}
      </div>

      <div>
        <div className="text-sm font-medium mb-2">{t('danmakuEdit.selectEpisodeLabel')}</div>
        <div className="max-h-60 overflow-y-auto border rounded p-2 border-gray-200 dark:border-gray-700" style={{ backgroundColor: 'var(--color-hover)' }}>
          <Checkbox
            checked={offsetEpisodes.length === episodes?.length}
            indeterminate={offsetEpisodes.length > 0 && offsetEpisodes.length < episodes?.length}
            onChange={(e) => {
              if (e.target.checked) {
                setOffsetEpisodes(episodes?.map((ep) => ep.episodeId) || [])
              } else {
                setOffsetEpisodes([])
              }
            }}
          >
            {t('danmakuEdit.selectAll')}
          </Checkbox>
          <Divider className="my-2" />
          <div className="space-y-1">
            {episodes?.map((ep) => (
              <div key={ep.episodeId}>
                <Checkbox
                  checked={offsetEpisodes.includes(ep.episodeId)}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setOffsetEpisodes([...offsetEpisodes, ep.episodeId])
                    } else {
                      setOffsetEpisodes(offsetEpisodes.filter((id) => id !== ep.episodeId))
                    }
                  }}
                >
                  {t('danmakuEdit.episodeOption', { index: ep.episodeIndex, title: ep.title, count: ep.commentCount })}
                </Checkbox>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 sm:gap-4">
        <span className="shrink-0">{t('danmakuEdit.offsetSeconds')}</span>
        <InputNumber
          value={offsetValue}
          onChange={setOffsetValue}
          addonAfter={t('danmakuEdit.second')}
          style={{ width: 150 }}
        />
        <span className="text-gray-500 dark:text-gray-400 text-sm">
          {offsetValue > 0 ? t('danmakuEdit.danmakuDelay') : offsetValue < 0 ? t('danmakuEdit.danmakuAdvance') : ''}
        </span>
      </div>

      <div className="flex justify-end">
        <Button
          type="primary"
          onClick={handleApplyOffset}
          loading={offsetLoading}
          disabled={offsetEpisodes.length === 0 || offsetValue === 0}
        >
          {t('danmakuEdit.applyOffset', { count: offsetEpisodes.length })}
        </Button>
      </div>
    </div>
  )

  // 渲染分集拆分标签页
  const renderSplitTab = () => (
    <div className="space-y-4">
      <div className="p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded text-sm text-yellow-800 dark:text-yellow-200">
        <InfoCircleOutlined className="mr-2" />
        {t('danmakuEdit.splitTip')}
      </div>

      <div className="flex flex-wrap items-center gap-2 sm:gap-4">
        <span className="shrink-0">{t('danmakuEdit.sourceEpisode')}</span>
        <Select
          value={splitSourceEpisode}
          onChange={(val) => {
            setSplitSourceEpisode(val)
            setSplitConfigs([])
          }}
          className="flex-1 min-w-[200px]"
          style={{ maxWidth: 300 }}
          placeholder={t('danmakuEdit.selectSplitEpisode')}
          options={episodes?.map((e) => ({
            value: e.episodeId,
            label: t('danmakuEdit.episodeOption', { index: e.episodeIndex, title: e.title, count: e.commentCount }),
          }))}
        />
        {splitSourceEpisode && (
          <Button size="small" onClick={() => loadDetailData(splitSourceEpisode)}>
            {t('danmakuEdit.viewDetail')}
          </Button>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium">{t('danmakuEdit.splitConfig')}</span>
          <Button size="small" icon={<PlusOutlined />} onClick={addSplitConfig}>
            {t('danmakuEdit.add')}
          </Button>
        </div>
        <div className="space-y-2">
          {splitConfigs.map((config, index) => (
            <div
              key={config.id}
              className="p-3 rounded"
              style={{ backgroundColor: 'var(--color-hover)' }}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-sm">{t('danmakuEdit.newEpisode', { index: index + 1 })}</span>
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => removeSplitConfig(config.id)}
                />
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                <InputNumber
                  value={config.episodeIndex}
                  onChange={(val) => updateSplitConfig(config.id, 'episodeIndex', val)}
                  addonBefore={t('danmakuEdit.episodeNum')}
                  className="w-full"
                  min={1}
                />
                <InputNumber
                  value={config.startTime}
                  onChange={(val) => updateSplitConfig(config.id, 'startTime', val)}
                  addonBefore={t('danmakuEdit.start')}
                  addonAfter={t('danmakuEdit.second')}
                  className="w-full"
                  min={0}
                />
                <InputNumber
                  value={config.endTime}
                  onChange={(val) => updateSplitConfig(config.id, 'endTime', val)}
                  addonBefore={t('danmakuEdit.end')}
                  addonAfter={t('danmakuEdit.second')}
                  className="w-full"
                  min={0}
                />
                <Input
                  value={config.title}
                  onChange={(e) => updateSplitConfig(config.id, 'title', e.target.value)}
                  placeholder={t('danmakuEdit.titlePlaceholder')}
                  className="w-full"
                />
              </div>
            </div>
          ))}
          {splitConfigs.length === 0 && (
            <Empty description={t('danmakuEdit.addSplitConfigEmpty')} />
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <Checkbox checked={splitDeleteSource} onChange={(e) => setSplitDeleteSource(e.target.checked)}>
          {t('danmakuEdit.deleteSourceEpisode')}
        </Checkbox>
        <Checkbox checked={splitResetTime} onChange={(e) => setSplitResetTime(e.target.checked)}>
          {t('danmakuEdit.resetTimeFromZero')}
        </Checkbox>
      </div>

      <div className="flex justify-end">
        <Button
          type="primary"
          onClick={handleSplit}
          loading={splitLoading}
          disabled={!splitSourceEpisode || splitConfigs.length === 0}
        >
          {t('danmakuEdit.doSplit')}
        </Button>
      </div>
    </div>
  )

  // 渲染分集合并标签页
  const renderMergeTab = () => (
    <div className="space-y-4">
      <div className="p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded text-sm text-yellow-800 dark:text-yellow-200">
        <InfoCircleOutlined className="mr-2" />
        {t('danmakuEdit.mergeTip')}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 左侧：可选分集 */}
        <div>
          <div className="text-sm font-medium mb-2">{t('danmakuEdit.availableEpisodes')}</div>
          <div className="max-h-60 overflow-y-auto border rounded p-2 border-gray-200 dark:border-gray-700" style={{ backgroundColor: 'var(--color-hover)' }}>
            {availableMergeEpisodes.length > 0 ? (
              availableMergeEpisodes.map((ep) => (
                <div
                  key={ep.episodeId}
                  className="flex items-center justify-between p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer"
                  onClick={() => addMergeEpisode(ep.episodeId)}
                >
                  <div>
                    <div className="font-medium">{t('danmakuEdit.episodeItem', { index: ep.episodeIndex })}</div>
                    <div className="text-sm text-gray-500 dark:text-gray-400 truncate">{ep.title}</div>
                  </div>
                  <Button size="small" icon={<PlusOutlined />} />
                </div>
              ))
            ) : (
              <Empty description={t('danmakuEdit.allEpisodesAdded')} />
            )}
          </div>
        </div>

        {/* 右侧：已选分集（可拖拽排序） */}
        <div>
          <div className="text-sm font-medium mb-2">{t('danmakuEdit.mergeOrder')}</div>
          <div className="max-h-60 overflow-y-auto border rounded p-2 border-gray-200 dark:border-gray-700" style={{ backgroundColor: 'var(--color-hover)' }}>
            {mergeEpisodes.length > 0 ? (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={mergeEpisodes.map((e) => e.episodeId)}
                  strategy={verticalListSortingStrategy}
                >
                  {mergeEpisodes.map(item => (
                    <SortableMergeItem
                      key={item.episodeId}
                      item={item}
                      onOffsetChange={updateMergeOffset}
                      onRemove={removeMergeEpisode}
                    />
                  ))}
                </SortableContext>
              </DndContext>
            ) : (
              <Empty description={t('danmakuEdit.clickToAdd')} />
            )}
          </div>
        </div>
      </div>

      {/* 目标配置 */}
      <div className="p-3 rounded space-y-3" style={{ backgroundColor: 'var(--color-hover)' }}>
        <div className="text-sm font-medium">{t('danmakuEdit.targetConfig')}</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex items-center gap-2">
            <span className="shrink-0">{t('danmakuEdit.episodeNumLabel')}</span>
            <InputNumber
              value={mergeTargetIndex}
              onChange={setMergeTargetIndex}
              min={1}
              className="w-full"
              style={{ maxWidth: 100 }}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="shrink-0">{t('danmakuEdit.titleLabel')}</span>
            <Input
              value={mergeTargetTitle}
              onChange={(e) => setMergeTargetTitle(e.target.value)}
              placeholder={t('danmakuEdit.mergeTitlePlaceholder')}
              className="flex-1"
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <Checkbox checked={mergeDeleteSources} onChange={(e) => setMergeDeleteSources(e.target.checked)}>
            {t('danmakuEdit.deleteSourceEpisode')}
          </Checkbox>
          <Checkbox checked={mergeDeduplicate} onChange={(e) => setMergeDeduplicate(e.target.checked)}>
            {t('danmakuEdit.deduplicate')}
          </Checkbox>
        </div>
      </div>

      <div className="flex justify-end">
        <Button
          type="primary"
          onClick={handleMerge}
          loading={mergeLoading}
          disabled={mergeEpisodes.length < 2 || !mergeTargetTitle.trim()}
        >
          {t('danmakuEdit.doMerge', { count: mergeEpisodes.length })}
        </Button>
      </div>
    </div>
  )

  const tabItems = [
    {
      key: 'detail',
      label: (
        <span>
          <InfoCircleOutlined />
          {t('danmakuEdit.tabDetail')}
        </span>
      ),
      children: renderDetailTab(),
    },
    {
      key: 'offset',
      label: (
        <span>
          <ClockCircleOutlined />
          {t('danmakuEdit.tabOffset')}
        </span>
      ),
      children: renderOffsetTab(),
    },
    {
      key: 'split',
      label: (
        <span>
          <ScissorOutlined />
          {t('danmakuEdit.tabSplit')}
        </span>
      ),
      children: renderSplitTab(),
    },
    {
      key: 'merge',
      label: (
        <span>
          <MergeCellsOutlined />
          {t('danmakuEdit.tabMerge')}
        </span>
      ),
      children: renderMergeTab(),
    },
  ]

  return (
    <Modal
      title={t('danmakuEdit.title')}
      open={open}
      onCancel={onCancel}
      footer={null}
      width={900}
      destroyOnClose
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
      />
    </Modal>
  )
}
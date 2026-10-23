import { useEffect, useMemo, useState } from 'react'
import { Button, Empty, Modal, Spin, Tag, Typography } from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, HolderOutlined } from '@ant-design/icons'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useTranslation } from 'react-i18next'
import { getEpisodes, sortEpisodes } from '../apis'
import { useMessage } from '../MessageContext'

/**
 * 单个可拖动分集行。
 * 拖动只绑定在把手上，手机端滚动列表时不会误触发拖动。
 */
const SortableEpisodeItem = ({ item, position, newIndex, total, onMove, isMobile }) => {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.episodeId,
  })
  const changed = newIndex !== item.episodeIndex
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : undefined,
    position: 'relative',
    backgroundColor: 'var(--color-card)',
    borderColor: changed ? 'var(--ant-color-primary, #1677ff)' : 'var(--color-border)',
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="sortable-priority-item flex items-center gap-2 p-2 mb-2 rounded-lg border border-solid"
    >
      <span
        {...attributes}
        {...listeners}
        aria-label={t('episodeSort.dragHandle')}
        // why: touch-action:none 让触屏拖动不被浏览器当作滚动吞掉
        style={{ touchAction: 'none', color: 'var(--color-text-secondary, #999)' }}
        className={`cursor-grab flex items-center justify-center ${isMobile ? 'w-9 h-9 text-lg' : 'w-6 h-6'}`}
      >
        <HolderOutlined />
      </span>
      <div className="flex flex-col items-center shrink-0" style={{ minWidth: 56 }}>
        <span className="font-bold" style={{ color: changed ? 'var(--ant-color-primary, #1677ff)' : 'var(--color-text)' }}>
          {t('episodeSort.episodeNo', { index: newIndex })}
        </span>
        {changed && (
          <span className="text-xs line-through" style={{ color: 'var(--color-text-secondary, #999)' }}>
            {t('episodeSort.episodeNo', { index: item.episodeIndex })}
          </span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="truncate" style={{ color: 'var(--color-text)' }} title={item.title}>{item.title}</div>
        {!isMobile && (
          <div className="text-xs" style={{ color: 'var(--color-text-secondary, #999)' }}>
            ID {item.episodeId} · {t('episodeSort.commentCount', { count: item.commentCount ?? 0 })}
          </div>
        )}
      </div>
      {/* 上下移动按钮：键盘/读屏用户和不便拖动时的简易替代 */}
      <div className="flex gap-1 shrink-0">
        <Button
          size={isMobile ? 'middle' : 'small'}
          type="text"
          icon={<ArrowUpOutlined />}
          disabled={position === 0}
          aria-label={t('episodeSort.moveUp')}
          onClick={() => onMove(position, position - 1)}
        />
        <Button
          size={isMobile ? 'middle' : 'small'}
          type="text"
          icon={<ArrowDownOutlined />}
          disabled={position === total - 1}
          aria-label={t('episodeSort.moveDown')}
          onClick={() => onMove(position, position + 1)}
        />
      </div>
    </div>
  )
}

/**
 * 分集拖动排序弹窗。
 * 拖动只改变顺序，提交时把该源原有的集数集合按新顺序重新分配，
 * 后端在同一事务内两阶段交换编号，不再需要“先改成别的再改回来”。
 */
export const EpisodeSortModal = ({ open, sourceId, isMobile, onClose, onSubmitted }) => {
  const { t } = useTranslation()
  const messageApi = useMessage()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    // 长按 150ms 再拖，避免手机端滑动列表时误拖
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  useEffect(() => {
    if (!open || !sourceId) return
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        // 排序需要全量分集，先取总数再一次取全
        const first = await getEpisodes({ sourceId, page: 1, pageSize: 1 })
        const total = first.data?.total || 0
        const res = total
          ? await getEpisodes({ sourceId, page: 1, pageSize: total })
          : { data: { list: [] } }
        const list = [...(res.data?.list || [])].sort(
          (a, b) => a.episodeIndex - b.episodeIndex || a.episodeId - b.episodeId
        )
        if (!cancelled) setItems(list)
      } catch (error) {
        if (!cancelled) messageApi.error(t('episodeSort.loadFailed', { error: error.message }))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [open, sourceId])

  // 原有集数升序，按位置重新分配，保留缺集空位
  const slots = useMemo(() => items.map(i => i.episodeIndex).sort((a, b) => a - b), [items])
  const changedCount = items.filter((item, i) => item.episodeIndex !== slots[i]).length

  const handleMove = (from, to) => setItems(prev => arrayMove(prev, from, to))

  const handleDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return
    setItems(prev => {
      const from = prev.findIndex(i => i.episodeId === active.id)
      const to = prev.findIndex(i => i.episodeId === over.id)
      return arrayMove(prev, from, to)
    })
  }

  const handleSubmit = async () => {
    if (!changedCount) {
      onClose()
      return
    }
    setSubmitting(true)
    try {
      await sortEpisodes({ sourceId, episodeIds: items.map(i => i.episodeId) })
      messageApi.success(t('episodeSort.submitted'))
      onSubmitted?.()
      onClose()
    } catch (error) {
      messageApi.error(t('episodeSort.submitFailed', { error: error?.detail || error.message }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title={t('episodeSort.title')}
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      okText={t('episodeSort.save', { count: changedCount })}
      okButtonProps={{ disabled: loading || !changedCount }}
      cancelText={t('common.cancel')}
      confirmLoading={submitting}
      width={isMobile ? '100%' : 640}
      style={isMobile ? { top: 0, maxWidth: '100vw', padding: 0 } : undefined}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" className="!mb-2 text-sm">
        {isMobile ? t('episodeSort.hintMobile') : t('episodeSort.hintDesktop')}
      </Typography.Paragraph>
      {changedCount > 0 && (
        <div className="mb-2 flex items-center justify-between">
          <Tag color="blue">{t('episodeSort.changed', { count: changedCount })}</Tag>
          <Button
            size="small"
            type="link"
            onClick={() => setItems(prev => [...prev].sort((a, b) => a.episodeIndex - b.episodeIndex || a.episodeId - b.episodeId))}
          >
            {t('episodeSort.reset')}
          </Button>
        </div>
      )}
      <Spin spinning={loading}>
        <div style={{ maxHeight: isMobile ? '70vh' : '60vh', overflowY: 'auto', overscrollBehavior: 'contain' }}>
          {!loading && !items.length ? (
            <Empty />
          ) : (
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
              <SortableContext items={items.map(i => i.episodeId)} strategy={verticalListSortingStrategy}>
                {items.map((item, index) => (
                  <SortableEpisodeItem
                    key={item.episodeId}
                    item={item}
                    position={index}
                    newIndex={slots[index]}
                    total={items.length}
                    onMove={handleMove}
                    isMobile={isMobile}
                  />
                ))}
              </SortableContext>
            </DndContext>
          )}
        </div>
      </Spin>
    </Modal>
  )
}

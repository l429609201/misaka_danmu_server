import { useMemo } from 'react'
import { Button, Card, Empty, Table } from 'antd'
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

/**
 * 原有集数升序排列，按新位置依次分配，保留缺集空位。
 * @param {Array} items 当前排序后的分集
 * @returns {number[]} 每个位置对应的新集数
 */
export const getSortSlots = items => items.map(i => i.episodeIndex).sort((a, b) => a - b)

/** 按原集数恢复初始顺序 */
export const sortByOriginalIndex = items =>
  [...items].sort((a, b) => a.episodeIndex - b.episodeIndex || a.episodeId - b.episodeId)

/** 桌面端可拖动表格行：整行即拖动区，拖动中提升层级 */
const SortableTableRow = props => {
  const id = props['data-row-key']
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  const style = {
    ...props.style,
    transform: CSS.Translate.toString(transform),
    transition,
    cursor: 'grab',
    ...(isDragging ? { position: 'relative', zIndex: 20, opacity: 0.85 } : {}),
  }
  return <tr {...props} ref={setNodeRef} style={style} {...attributes} {...listeners} />
}

/** 移动端可拖动卡片：长按后拖动，不影响列表滚动 */
const SortableCard = ({ item, position, newIndex, total, onMove, renderInfo }) => {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.episodeId,
  })
  const changed = newIndex !== item.episodeIndex
  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Translate.toString(transform),
        transition,
        position: 'relative',
        zIndex: isDragging ? 20 : undefined,
        opacity: isDragging ? 0.85 : 1,
      }}
      className="mb-3"
    >
      <Card
        size="small"
        bodyStyle={{ padding: 12 }}
        style={changed ? { borderColor: 'var(--ant-color-primary, #1677ff)' } : undefined}
        {...attributes}
        {...listeners}
      >
        <div className="flex items-center gap-3">
          <HolderOutlined style={{ color: 'var(--color-text-secondary, #999)', fontSize: 18 }} />
          <div className="flex-1 min-w-0">{renderInfo(item, newIndex, changed)}</div>
          <div className="flex flex-col gap-1 shrink-0">
            <Button
              size="small"
              type="text"
              icon={<ArrowUpOutlined />}
              disabled={position === 0}
              aria-label={t('episodeSort.moveUp')}
              onClick={() => onMove(position, position - 1)}
            />
            <Button
              size="small"
              type="text"
              icon={<ArrowDownOutlined />}
              disabled={position === total - 1}
              aria-label={t('episodeSort.moveDown')}
              onClick={() => onMove(position, position + 1)}
            />
          </div>
        </div>
      </Card>
    </div>
  )
}

/**
 * 页面内分集拖拽排序视图（替代原排序弹窗）。
 * 进入排序模式后直接在页面上拖动分集，只改变本地顺序，由父组件统一保存。
 * @param {Object} props
 * @param {Array} props.items 全量分集（当前顺序）
 * @param {Function} props.onChange 新顺序回调 (items) => void
 * @param {boolean} props.isMobile 是否移动端
 * @param {boolean} props.loading 是否加载中
 */
export const EpisodeSortBoard = ({ items, onChange, isMobile, loading }) => {
  const { t } = useTranslation()
  const slots = useMemo(() => getSortSlots(items), [items])

  const sensors = useSensors(
    // 移动 5px 才算拖动，单击上下按钮不会误触
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    // 长按 150ms 再拖，避免手机端滑动列表时误拖
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  const handleMove = (from, to) => onChange(arrayMove(items, from, to))

  const handleDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return
    const from = items.findIndex(i => i.episodeId === active.id)
    const to = items.findIndex(i => i.episodeId === over.id)
    onChange(arrayMove(items, from, to))
  }

  const renderIndex = (newIndex, oldIndex, changed) => (
    <span className="whitespace-nowrap">
      <span className="font-bold" style={changed ? { color: 'var(--ant-color-primary, #1677ff)' } : undefined}>
        {newIndex}
      </span>
      {changed && (
        <span className="ml-1 text-xs line-through" style={{ color: 'var(--color-text-secondary, #999)' }}>
          {oldIndex}
        </span>
      )}
    </span>
  )

  const columns = [
    {
      key: 'drag',
      width: 44,
      align: 'center',
      render: () => <HolderOutlined style={{ color: 'var(--color-text-secondary, #999)' }} />,
    },
    { title: 'ID', dataIndex: 'episodeId', key: 'episodeId', width: 150 },
    { title: t('episodePage.colEpisodeName'), dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: t('episodePage.colEpisodeIndex'),
      key: 'episodeIndex',
      width: 110,
      render: (_, record, index) => renderIndex(slots[index], record.episodeIndex, slots[index] !== record.episodeIndex),
    },
    { title: t('episodePage.colCommentCount'), dataIndex: 'commentCount', key: 'commentCount', width: 90 },
    {
      title: t('episodePage.colAction'),
      key: 'move',
      width: 90,
      align: 'center',
      render: (_, __, index) => (
        <div className="flex justify-center gap-1">
          <Button
            size="small"
            type="text"
            icon={<ArrowUpOutlined />}
            disabled={index === 0}
            aria-label={t('episodeSort.moveUp')}
            onClick={() => handleMove(index, index - 1)}
          />
          <Button
            size="small"
            type="text"
            icon={<ArrowDownOutlined />}
            disabled={index === items.length - 1}
            aria-label={t('episodeSort.moveDown')}
            onClick={() => handleMove(index, index + 1)}
          />
        </div>
      ),
    },
  ]

  if (!loading && !items.length) return <Empty />

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={items.map(i => i.episodeId)} strategy={verticalListSortingStrategy}>
        {isMobile ? (
          items.map((item, index) => (
            <SortableCard
              key={item.episodeId}
              item={item}
              position={index}
              newIndex={slots[index]}
              total={items.length}
              onMove={handleMove}
              renderInfo={(it, newIndex, changed) => (
                <>
                  <div className="flex items-center gap-2">
                    {renderIndex(newIndex, it.episodeIndex, changed)}
                    <span className="truncate" title={it.title}>{it.title}</span>
                  </div>
                  <div className="text-xs" style={{ color: 'var(--color-text-secondary, #999)' }}>
                    ID {it.episodeId} · {t('episodeSort.commentCount', { count: it.commentCount ?? 0 })}
                  </div>
                </>
              )}
            />
          ))
        ) : (
          <Table
            size="small"
            rowKey="episodeId"
            loading={loading}
            dataSource={items}
            columns={columns}
            pagination={false}
            components={{ body: { row: SortableTableRow } }}
            rowClassName={(record, index) =>
              slots[index] !== record.episodeIndex ? 'episode-sort-changed' : ''
            }
            className="library-table episode-sort-table"
          />
        )}
      </SortableContext>
    </DndContext>
  )
}

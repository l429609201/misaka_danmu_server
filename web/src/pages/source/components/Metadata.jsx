import { Button, Card, Form, Input, List, message, Tag, Tooltip } from 'antd'
import { useEffect, useState, useRef } from 'react'
import { getMetaData, setMetaData } from '../../../apis'
import { MyIcon } from '@/components/MyIcon'
import {
  closestCorners,
  DndContext,
  DragOverlay,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ContainerOutlined } from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'

const SortableItem = ({ item, index, handleChangeStatus }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.id || `item-${index}`, // 使用item.id或索引作为唯一标识
    data: {
      item,
      index,
    },
  })

  // 拖拽样式
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    cursor: 'grab',
    touchAction: 'none', // 关键：阻止浏览器默认触摸行为
    userSelect: 'none', // 防止拖拽时选中文本
    ...(isDragging && { cursor: 'grabbing' }),
  }

  return (
    <List.Item ref={setNodeRef} style={style} {...attributes}>
      {/* 保留你原有的列表项渲染逻辑 */}
      <div className="w-full flex items-center justify-between">
        {/* 左侧添加拖拽手柄 */}
        <div className="flex items-center gap-2">
          <div {...listeners} style={{ cursor: 'grab' }}>
            <MyIcon icon="drag" size={24} />
          </div>
          <div>{item.providerName}</div>
        </div>
        <div className="flex items-center justify-around gap-4">
          {item.status !== '未配置' && (
            <Tooltip title={item.status} trigger={['click', 'hover']}>
              <ContainerOutlined
                style={{
                  color: item.status?.includes('失败')
                    ? 'var(--color-red-400)'
                    : 'var(--color-green-400)',
                }}
              />
            </Tooltip>
          )}
          {item.isAuxSearchEnabled ? (
            <Tag color="green">已启用</Tag>
          ) : (
            <Tag color="red">未启用</Tag>
          )}
          {item.providerName !== 'tmdb' ? (
            <Tooltip title="切换启用状态">
              <div onClick={handleChangeStatus}>
                <MyIcon icon="exchange" size={24} />
              </div>
            </Tooltip>
          ) : (
            <div className="w-6"></div>
          )}
        </div>
      </div>
    </List.Item>
  )
}

export const Metadata = () => {
  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [activeItem, setActiveItem] = useState(null)
  const dragOverlayRef = useRef(null)

  const messageApi = useMessage()

  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 5,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        distance: 8,
        delay: 100,
      },
    })
  )

  useEffect(() => {
    getMetaData()
      .then(res => {
        setList(res.data ?? [])
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleDragEnd = event => {
    const { active, over } = event

    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    // 找到原位置和新位置
    const activeIndex = list.findIndex(
      item => item.providerName === active.data.current.item.providerName
    )
    const overIndex = list.findIndex(
      item => item.providerName === over.data.current.item.providerName
    )

    if (activeIndex !== -1 && overIndex !== -1) {
      // 1. 重新排列数组
      const newList = [...list]
      const [movedItem] = newList.splice(activeIndex, 1)
      newList.splice(overIndex, 0, movedItem)

      // 2. 重新计算所有项的display_order（从1开始连续编号）
      const updatedList = newList.map((item, index) => ({
        ...item,
        displayOrder: index + 1, // 排序值从1开始
      }))

      // 3. 更新状态
      console.log(updatedList, 'updatedList')
      setList(updatedList)
      setMetaData(updatedList)
      messageApi.success(
        `已更新排序，${movedItem.providerName} 移动到位置 ${overIndex + 1}`
      )
    }

    setActiveItem(null)
  }

  // 处理拖拽开始
  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = list.find(
      item => (item.id || `item-${list.indexOf(item)}`) === active.id
    )
    setActiveItem(item)
  }

  const handleChangeStatus = item => {
    const newList = list.map(it => {
      if (it.providerName === item.providerName) {
        return {
          ...it,
          isAuxSearchEnabled: Number(!it.isAuxSearchEnabled),
        }
      } else {
        return it
      }
    })
    setList(newList)
    setMetaData(newList)
  }

  const renderDragOverlay = () => {
    if (!activeItem) return null

    return (
      <div ref={dragOverlayRef} style={{ width: '100%', maxWidth: '100%' }}>
        <List.Item
          style={{
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
            opacity: 0.9,
          }}
        >
          <div className="w-full flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MyIcon icon="drag" size={24} />
              <div>{activeItem.providerName}</div>
            </div>
            <div className="flex items-center justify-around gap-4">
              {activeItem.status !== '未配置' && (
                <Tooltip title={activeItem.status}>
                  <ContainerOutlined
                    style={{
                      color: activeItem.status?.includes('失败')
                        ? 'var(--color-red-400)'
                        : 'var(--color-green-400)',
                    }}
                  />
                </Tooltip>
              )}
              {activeItem.isAuxSearchEnabled ? (
                <Tag color="green">已启用</Tag>
              ) : (
                <Tag color="red">未启用</Tag>
              )}
              {activeItem.providerName !== 'tmdb' ? (
                <div>
                  <MyIcon icon="exchange" size={24} />
                </div>
              ) : (
                <div className="w-6"></div>
              )}
            </div>
          </div>
        </List.Item>
      </div>
    )
  }

  return (
    <div className="my-6">
      <Card loading={loading} title="元信息搜索源">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            strategy={verticalListSortingStrategy}
            items={list.map((item, index) => item.id || `item-${index}`)}
          >
            <List
              itemLayout="vertical"
              size="large"
              dataSource={list}
              renderItem={(item, index) => (
                <SortableItem
                  key={item.id || index}
                  item={item}
                  index={index}
                  handleChangeStatus={() => handleChangeStatus(item)}
                />
              )}
            />
          </SortableContext>

          {/* 拖拽覆盖层 */}
          <DragOverlay>{renderDragOverlay()}</DragOverlay>
        </DndContext>
      </Card>
    </div>
  )
}

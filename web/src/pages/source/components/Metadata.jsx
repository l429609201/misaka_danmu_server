import { Button, Card, Form, Input, List, message, Tag } from 'antd'
import { useEffect, useState, useRef } from 'react'
import { getMetaData, setMetaData } from '../../../apis'
import { MyIcon } from '@/components/MyIcon'
import { DndContext, DragOverlay } from '@dnd-kit/core'
import { SortableContext, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

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
          <div>{item.provider_name}</div>
        </div>
        <div className="flex items-center justify-around gap-4">
          {item.status !== '未配置' ? (
            <Tag color="green">{item.status}</Tag>
          ) : (
            <Tag color="red">{item.status}</Tag>
          )}
          {item.is_aux_search_enabled ? (
            <Tag color="green">已启用</Tag>
          ) : (
            <Tag color="red">未启用</Tag>
          )}
          {item.provider_name !== 'tmdb' ? (
            <div onClick={handleChangeStatus}>
              <MyIcon icon="exchange" size={24} />
            </div>
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
      item => item.provider_name === active.data.current.item.provider_name
    )
    const overIndex = list.findIndex(
      item => item.provider_name === over.data.current.item.provider_name
    )

    if (activeIndex !== -1 && overIndex !== -1) {
      // 1. 重新排列数组
      const newList = [...list]
      const [movedItem] = newList.splice(activeIndex, 1)
      newList.splice(overIndex, 0, movedItem)

      // 2. 重新计算所有项的display_order（从1开始连续编号）
      const updatedList = newList.map((item, index) => ({
        ...item,
        display_order: index + 1, // 排序值从1开始
      }))

      // 3. 更新状态
      console.log(updatedList, 'updatedList')
      setList(updatedList)
      setMetaData(updatedList)
      message.success(
        `已更新排序，${movedItem.provider_name} 移动到位置 ${overIndex + 1}`
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
      if (it.provider_name === item.provider_name) {
        return {
          ...it,
          is_aux_search_enabled: Number(!it.is_aux_search_enabled),
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
              <div>{activeItem.provider_name}</div>
            </div>
            <div className="flex items-center justify-around gap-4">
              {activeItem.status !== '未配置' ? (
                <Tag color="green">{activeItem.status}</Tag>
              ) : (
                <Tag color="red">{activeItem.status}</Tag>
              )}
              {activeItem.is_aux_search_enabled ? (
                <Tag color="green">已启用</Tag>
              ) : (
                <Tag color="red">未启用</Tag>
              )}
              {activeItem.provider_name !== 'tmdb' ? (
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
        <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
          <SortableContext
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

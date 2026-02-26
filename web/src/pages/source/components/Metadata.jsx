import {
  Card,
  Form,
  List,
  Modal,
  Switch,
  Tag,
  Tooltip,
  Tabs,
} from 'antd'
import { useEffect, useState, useRef } from 'react'
import {
  getMetaData,
  getProviderConfig,
  setMetaData,
  setProviderConfig,
  setBangumiConfig,
  setTmdbConfig,
  setTvdbConfig,
  setDoubanConfig,
} from '../../../apis'
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
import {
  CheckCircleFilled,
  CloseCircleFilled,
  QuestionCircleFilled,
  ExclamationCircleFilled,
  MinusCircleFilled,
} from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'
import {
  BangumiConfig,
  TMDBConfig,
  TVDBConfig,
  DoubanConfig,
  ImdbConfig
} from './MetadataSourceConfig'

const getStatusIcon = (statusCode) => {
  switch (statusCode) {
    case 'ok':
      return <CheckCircleFilled style={{ color: 'var(--color-green-400)', fontSize: 16 }} />
    case 'warning':
      return <ExclamationCircleFilled style={{ color: 'var(--color-orange-400)', fontSize: 16 }} />
    case 'error':
      return <CloseCircleFilled style={{ color: 'var(--color-red-400)', fontSize: 16 }} />
    case 'disabled':
      return <MinusCircleFilled style={{ color: 'var(--color-gray-400)', fontSize: 16 }} />
    case 'unconfigured':
    default:
      return <QuestionCircleFilled style={{ color: 'var(--color-gray-400)', fontSize: 16 }} />
  }
}

const SortableItem = ({ item, index, handleChangeStatus, onConfig }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    // 修正：始终使用 providerName 作为唯一 ID
    id: item.providerName,
    data: {
      item,
      index,
    },
  })

  // 拖拽样式
  // 只保留必要的样式，移除会阻止滚动的touchAction
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    ...(isDragging && { cursor: 'grabbing' }),
  }

  return (
    <List.Item ref={setNodeRef} style={style}>
      {/* 保留你原有的列表项渲染逻辑 */}
      <div className="w-full flex items-center justify-between">
        {/* 左侧添加拖拽手柄 */}
        <div className="flex items-center gap-2">
          {/* 将attributes移到拖拽图标容器上，确保只有拖拽图标可触发拖拽 */}
          <div {...attributes} {...listeners} style={{ cursor: 'grab' }}>
            <MyIcon icon="drag" size={24} />
          </div>
          <div>{item.providerName}</div>
        </div>
        <div className="flex items-center justify-around gap-3">
          {/* 状态图标：移到齿轮左边，hover 显示详细状态 */}
          <Tooltip title={item.status || '未配置'} trigger={['click', 'hover']}>
            <span className="cursor-default">{getStatusIcon(item.statusCode)}</span>
          </Tooltip>
          {/* 配置按钮 */}
          <div onClick={onConfig} className="cursor-pointer">
            <MyIcon icon="setting" size={24} />
          </div>
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
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedSource, setSelectedSource] = useState(null)
  const [form] = Form.useForm()
  const [confirmLoading, setConfirmLoading] = useState(false)

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

  const fetchInfo = () => {
    setLoading(true)
    getMetaData()
      .then(res => {
        setList(res.data ?? [])
      })
      .finally(() => {
        setLoading(false)
      })
  }

  useEffect(fetchInfo, [])

  useEffect(() => {
    if (isModalOpen && selectedSource?.providerName) {
      // 重置表单以防显示旧数据
      form.resetFields()
      getProviderConfig({ providerName: selectedSource.providerName })
        .then(res => {
          const formValues = {
            ...res.data,
            useProxy: res.data.useProxy ?? true,
            logRawResponses: res.data.logRawResponses ?? false,
          }

          // IMDB特定配置
          if (selectedSource.providerName === 'imdb') {
            formValues.imdbUseApi = res.data.imdbUseApi ?? true
            formValues.imdbEnableFallback = res.data.imdbEnableFallback ?? true
          }

          form.setFieldsValue(formValues)
        })
        .catch(() => {
          messageApi.error('获取配置失败')
        })
    }
  }, [isModalOpen, selectedSource, form, messageApi])

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
      setList(updatedList)
      // 修正：只发送必要的字段，避免发送status等只读字段
      const payload = updatedList.map(item => ({
        providerName: item.providerName,
        isAuxSearchEnabled: item.isAuxSearchEnabled,
        displayOrder: item.displayOrder,
      }))
      setMetaData(payload)
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
    const item = list.find(item => item.providerName === active.id)
    setActiveItem(item)
  }

  const handleChangeStatus = item => {
    const newList = list.map(it => {
      if (it.providerName === item.providerName) {
        return {
          ...it,
          isAuxSearchEnabled: !it.isAuxSearchEnabled,
        }
      } else {
        return it
      }
    })
    setList(newList)
    const payload = newList.map(item => ({
      providerName: item.providerName,
      isAuxSearchEnabled: item.isAuxSearchEnabled,
      displayOrder: item.displayOrder,
    }))
    setMetaData(payload)
  }

  const handleSaveSettings = async () => {
    try {
      setConfirmLoading(true)
      const values = await form.validateFields()

      // 保存通用配置
      await setProviderConfig(selectedSource.providerName, {
        useProxy: values.useProxy,
        logRawResponses: values.logRawResponses,
        forceAuxSearchEnabled: values.forceAuxSearchEnabled,
        episodeUrlsEnabled: values.episodeUrlsEnabled,
      })

      // 保存源特定配置
      const providerName = selectedSource.providerName
      if (providerName === 'bangumi') {
        await setBangumiConfig({
          bangumiToken: values.bangumiToken,
          bangumiClientId: values.bangumiClientId,
          bangumiClientSecret: values.bangumiClientSecret,
          authMode: values.authMode || 'token', // 保存认证模式
        })
      } else if (providerName === 'tmdb') {
        await setTmdbConfig({
          tmdbApiKey: values.tmdbApiKey,
          tmdbApiBaseUrl: values.tmdbApiBaseUrl,
          tmdbImageBaseUrl: values.tmdbImageBaseUrl,
        })
      } else if (providerName === 'tvdb') {
        await setTvdbConfig({
          tvdbApiKey: values.tvdbApiKey,
        })
      } else if (providerName === 'douban') {
        await setDoubanConfig({
          doubanCookie: values.doubanCookie,
        })
      } else if (providerName === 'imdb') {
        await setProviderConfig(providerName, {
          imdbUseApi: values.imdbUseApi ?? true,
          imdbEnableFallback: values.imdbEnableFallback ?? true,
        })
      }

      messageApi.success('保存成功')
      setIsModalOpen(false)
      // 成功后刷新列表以更新状态
      fetchInfo()
    } catch (error) {
      messageApi.error(`保存失败: ${error.message || '未知错误'}`)
    } finally {
      setConfirmLoading(false)
    }
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
              {/* 状态图标：移到齿轮左边，hover 显示详细状态 */}
              <Tooltip title={activeItem.status || '未配置'}>
                <span className="cursor-default">{getStatusIcon(activeItem.statusCode)}</span>
              </Tooltip>
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
            items={list.map(item => item.providerName)}
          >
            <List
              itemLayout="vertical"
              size="large"
              dataSource={list}
              renderItem={(item, index) => (
                <SortableItem
                  key={item.providerName}
                  item={item}
                  index={index}
                  handleChangeStatus={() => handleChangeStatus(item)}
                  onConfig={() => {
                    setSelectedSource(item)
                    setIsModalOpen(true)
                  }}
                />
              )}
            />
          </SortableContext>

          {/* 拖拽覆盖层 */}
          <DragOverlay>{renderDragOverlay()}</DragOverlay>
        </DndContext>
      </Card>
      <Modal
        title={`配置: ${selectedSource?.providerName}`}
        open={isModalOpen}
        onOk={handleSaveSettings}
        onCancel={() => setIsModalOpen(false)}
        confirmLoading={confirmLoading}
        destroyOnClose
        forceRender
        width={700}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ useProxy: true, logRawResponses: false }}
        >
          <Tabs
            defaultActiveKey="general"
            items={[
              {
                key: 'general',
                label: '通用配置',
                children: (
                  <div className="space-y-4">
                    <div className="my-4">
                      请为 {selectedSource?.providerName} 源填写以下配置信息。
                    </div>
                    <div className="flex items-center justify-start flex-wrap gap-2 mb-4">
                      <Form.Item
                        name="useProxy"
                        label="启用代理"
                        valuePropName="checked"
                        className="min-w-[100px] shrink-0 !mb-0"
                      >
                        <Switch />
                      </Form.Item>
                      <div className="w-full text-gray-500">
                        启用后，此源的所有API请求将通过全局代理服务器进行。需要先在设置中配置全局代理。
                      </div>
                    </div>
                    <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
                      <Form.Item
                        name="logRawResponses"
                        label="记录原始响应"
                        valuePropName="checked"
                        className="min-w-[100px] shrink-0 !mb-0"
                      >
                        <Switch />
                      </Form.Item>
                      <div className="w-full text-gray-500">
                        启用后，此源的所有API请求的原始响应将被记录到{' '}
                        <code>config/logs/metadata_responses.log</code> 文件中，用于调试。
                      </div>
                    </div>
                    {/* 修正：根据后端返回的 isFailoverSource 标志来决定是否显示此开关 */}
                    {form.getFieldValue('isFailoverSource') && (
                      <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
                        <Form.Item
                          name="forceAuxSearchEnabled"
                          label="强制辅助搜索"
                          valuePropName="checked"
                          className="min-w-[100px] shrink-0 !mb-0"
                        >
                          <Switch />
                        </Form.Item>
                        <div
                          className="w-full text-gray-500"
                          title="启用后，在搜索时，此源将作为一个补充搜索源。如果其他弹幕源没有找到结果，或结果不佳，此源的结果将作为备选项显示在搜索结果中。"
                        >
                          启用后，此源将作为补充搜索源。当其他弹幕源结果不佳时，其结果将作为备选项显示。
                        </div>
                      </div>
                    )}
                    {/* 新增：根据后端返回的 supportsEpisodeUrls 标志来决定是否显示补充源开关 */}
                    {form.getFieldValue('supportsEpisodeUrls') && (
                      <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
                        <Form.Item
                          name="episodeUrlsEnabled"
                          label="启用补充源"
                          valuePropName="checked"
                          className="min-w-[100px] shrink-0 !mb-0"
                        >
                          <Switch />
                        </Form.Item>
                        <div
                          className="w-full text-gray-500"
                          title="启用后，当弹幕源没有提供分集列表时，此元数据源可以提供分集URL作为补充。"
                        >
                          启用后，当弹幕源缺少分集列表时，此源可提供分集URL作为补充。
                        </div>
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'source',
                label: '源配置',
                children: (
                  <div className="py-4">
                    {selectedSource?.providerName === 'bangumi' && <BangumiConfig form={form} />}
                    {selectedSource?.providerName === 'tmdb' && <TMDBConfig form={form} />}
                    {selectedSource?.providerName === 'tvdb' && <TVDBConfig form={form} />}
                    {selectedSource?.providerName === 'douban' && <DoubanConfig form={form} />}
                    {selectedSource?.providerName === 'imdb' && <ImdbConfig form={form} />}
                    {!['bangumi', 'tmdb', 'tvdb', 'douban', 'imdb'].includes(selectedSource?.providerName) && (
                      <div className="text-gray-500 text-center py-8">
                        此源暂无特定配置项
                      </div>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </Form>
      </Modal>
    </div>
  )
}

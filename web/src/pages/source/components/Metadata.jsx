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
import { useEffect, useState } from 'react'
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
import { useTranslation } from 'react-i18next'
import {
  BangumiConfig,
  TMDBConfig,
  TVDBConfig,
  DoubanConfig,
  ImdbConfig,
  TraktConfig,
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
  const { t } = useTranslation()
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
    <List.Item ref={setNodeRef} style={style} className="!border-0 !p-0 mb-3">
      <div
        {...attributes}
        {...listeners}
        className="w-full rounded-xl border px-4 py-3 flex items-center justify-between transition-all hover:shadow-md"
        style={{ background: 'var(--color-card)', borderColor: 'var(--color-border)', cursor: isDragging ? 'grabbing' : 'grab' }}
      >
        <div className="flex items-center gap-2">
          <div style={{ cursor: 'grab' }}>
            <MyIcon icon="drag" size={24} />
          </div>
          <div>{item.providerName}</div>
        </div>
        <div
          className="flex items-center justify-around gap-3"
          onPointerDown={e => e.stopPropagation()}
          onMouseDown={e => e.stopPropagation()}
          onTouchStart={e => e.stopPropagation()}
        >
          {/* 状态图标：移到齿轮左边，hover 显示详细状态 */}
          <Tooltip title={item.status || t('metadata.notConfigured')} trigger={['click', 'hover']}>
            <span className="cursor-default">{getStatusIcon(item.statusCode)}</span>
          </Tooltip>
          {/* 配置按钮 */}
          <div onClick={onConfig} className="cursor-pointer">
            <MyIcon icon="setting" size={24} />
          </div>
          {item.providerName !== 'tmdb' ? (
            <Switch
              checked={item.isAuxSearchEnabled}
              checkedChildren={t('metadata.enabled')}
              unCheckedChildren={t('metadata.notEnabled')}
              onChange={handleChangeStatus}
            />
          ) : (
            <Switch
              checked
              checkedChildren={t('metadata.enabled')}
              disabled
            />
          )}
        </div>
      </div>
    </List.Item>
  )
}

export const Metadata = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [activeItem, setActiveItem] = useState(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedSource, setSelectedSource] = useState(null)
  const [form] = Form.useForm()
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [configData, setConfigData] = useState(null)

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
      setConfigData(null)
      getProviderConfig({ providerName: selectedSource.providerName })
        .then(res => {
          setConfigData(res.data)
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
          messageApi.error(t('metadata.getConfigFailed'))
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
        t('metadata.sortUpdated', { name: movedItem.providerName, position: overIndex + 1 })
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

      // 收集动态字段（来自 configurableFields）
      const dynamicPayload = {}
      if (configData?.configurableFields) {
        for (const key of Object.keys(configData.configurableFields)) {
          if (values[key] !== undefined) {
            dynamicPayload[key] = values[key]
          }
        }
      }

      // 保存通用配置 + 动态字段（一次请求）
      await setProviderConfig(selectedSource.providerName, {
        useProxy: values.useProxy,
        logRawResponses: values.logRawResponses,
        ...dynamicPayload,
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
      } else if (providerName === 'trakt') {
        // Trakt OAuth 走内置 CF Worker，无需保存配置
      }

      messageApi.success(t('metadata.saveSuccess'))
      setIsModalOpen(false)
      // 成功后刷新列表以更新状态
      fetchInfo()
    } catch (error) {
      messageApi.error(`${t('metadata.saveFailed')}: ${error.message || t('metadata.unknownError')}`)
    } finally {
      setConfirmLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title={t('metadata.metadataSearchSource')}>
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

        </DndContext>
      </Card>
      <Modal
        title={t('metadata.configTitle', { name: selectedSource?.providerName })}
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
                label: t('metadata.generalConfig'),
                children: (
                  <div className="space-y-4">
                    <div className="my-4">
                      {t('metadata.fillConfigTip', { name: selectedSource?.providerName })}
                    </div>
                    <div className="flex items-center justify-start flex-wrap gap-2 mb-4">
                      <Form.Item
                        name="useProxy"
                        label={t('metadata.useProxy')}
                        valuePropName="checked"
                        className="min-w-[100px] shrink-0 !mb-0"
                      >
                        <Switch />
                      </Form.Item>
                      <div className="w-full text-gray-500">
                        {t('metadata.useProxyTip')}
                      </div>
                    </div>
                    <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
                      <Form.Item
                        name="logRawResponses"
                        label={t('metadata.recordRawResponse')}
                        valuePropName="checked"
                        className="min-w-[100px] shrink-0 !mb-0"
                      >
                        <Switch />
                      </Form.Item>
                      <div className="w-full text-gray-500">
                        {t('metadata.rawResponseTipPrefix')}
                        <code>config/logs/metadata_responses.log</code>{t('metadata.rawResponseTipSuffix')}
                      </div>
                    </div>
                  </div>
                ),
              },
              {
                key: 'source',
                label: t('metadata.sourceConfig'),
                children: (
                  <div className="py-4">
                    {selectedSource?.providerName === 'bangumi' && <BangumiConfig form={form} />}
                    {selectedSource?.providerName === 'tmdb' && <TMDBConfig form={form} />}
                    {selectedSource?.providerName === 'tvdb' && <TVDBConfig form={form} />}
                    {selectedSource?.providerName === 'douban' && <DoubanConfig form={form} />}
                    {selectedSource?.providerName === 'imdb' && <ImdbConfig form={form} />}
                    {selectedSource?.providerName === 'trakt' && <TraktConfig form={form} />}
                    {/* 动态渲染 configurableFields 声明的字段 */}
                    {configData?.configurableFields && Object.entries(configData.configurableFields).map(([key, fieldInfo]) => {
                      // 解析字段配置（兼容元组和对象格式）
                      const config = Array.isArray(fieldInfo)
                        ? { label: fieldInfo[0], type: fieldInfo[1] || 'string', tooltip: fieldInfo[2] || '' }
                        : { type: 'string', tooltip: '', ...fieldInfo }

                      if (config.type === 'boolean') {
                        return (
                          <div key={key} className="flex items-center justify-start flex-wrap md:flex-nowrap gap-2 mb-4">
                            <Form.Item
                              name={key}
                              label={config.label}
                              valuePropName="checked"
                              className="min-w-[100px] shrink-0 !mb-0"
                            >
                              <Switch />
                            </Form.Item>
                            {config.tooltip && (
                              <div className="w-full text-gray-500">{config.tooltip}</div>
                            )}
                          </div>
                        )
                      }
                      // 其他类型暂不渲染（未来可扩展）
                      return null
                    })}
                    {!['bangumi', 'tmdb', 'tvdb', 'douban', 'imdb', 'trakt'].includes(selectedSource?.providerName)
                      && !configData?.configurableFields && (
                      <div className="text-gray-500 text-center py-8">
                        {t('metadata.noSpecificConfig')}
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

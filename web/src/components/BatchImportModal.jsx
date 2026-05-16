import {
  Button,
  Checkbox,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Segmented,
  Tooltip,
  Upload,
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import { batchManualImport, validateImportUrl } from '../apis'
import {
  CloseCircleOutlined,
  CloudUploadOutlined,
  LinkOutlined,
  UploadOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import { useMessage } from '../MessageContext'

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

export const BatchImportModal = ({ open, sourceInfo, onCancel, onSuccess }) => {
  const messageApi = useMessage()
  const [loading, setLoading] = useState(false)
  // 导入模式: 'xml' | 'url'
  const [importMode, setImportMode] = useState('xml')
  // 存储解析后的XML数据列表
  const [xmlDataList, setXmlDataList] = useState([])
  const [fileList, setFileList] = useState([])
  // 存储上传状态
  const [uploading, setUploading] = useState(false)
  // 用于跟踪文件拖入顺序的计数器
  const orderCounter = useRef(0)
  // 存储文件ID与拖入顺序的映射
  const fileOrderMap = useRef({})
  const dragOverlayRef = useRef(null)
  const uploadRef = useRef(null)

  const [pageSize, setPageSize] = useState(10)
  const [activeItem, setActiveItem] = useState(null)

  // 批量URL导入相关状态
  const [urlListInput, setUrlListInput] = useState('')  // 多行URL输入
  const [urlValidating, setUrlValidating] = useState(false)
  const [urlParseResults, setUrlParseResults] = useState([])  // 解析结果列表
  const [startEpisodeIndex, setStartEpisodeIndex] = useState(1)  // 起始集数

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

  // 判断是否为自定义源
  const isCustomSource = sourceInfo?.providerName === 'custom'

  // 批量解析URL
  const handleBatchValidateUrls = async () => {
    const urls = urlListInput
      .split('\n')
      .map(line => line.trim())
      .filter(line => line.length > 0)

    if (urls.length === 0) {
      messageApi.warning('请输入至少一个URL')
      return
    }

    setUrlValidating(true)
    // 初始化解析结果，全部设为 loading 状态
    const initialResults = urls.map((url, index) => ({
      id: `url-${Date.now()}-${index}`,
      url,
      status: 'loading',  // loading | success | error
      selected: true,
      episodeIndex: startEpisodeIndex + index,
      title: '',
      provider: '',
      mediaId: '',
      errorMessage: '',
    }))
    setUrlParseResults(initialResults)

    // 并行解析所有URL
    const results = await Promise.all(
      urls.map(async (url, index) => {
        try {
          const res = await validateImportUrl({ url })
          if (res.data?.isValid) {
            // 对于非自定义源，检查 provider 是否匹配
            if (!isCustomSource) {
              const currentProvider = sourceInfo?.providerName?.toLowerCase()
              const urlProvider = res.data.provider?.toLowerCase()
              if (currentProvider !== urlProvider) {
                return {
                  ...initialResults[index],
                  status: 'error',
                  errorMessage: `来源不匹配 (${res.data.provider})`,
                  provider: res.data.provider,
                }
              }
            }
            return {
              ...initialResults[index],
              status: 'success',
              title: res.data.title || '',
              provider: res.data.provider,
              mediaId: res.data.mediaId,
            }
          } else {
            return {
              ...initialResults[index],
              status: 'error',
              errorMessage: res.data?.errorMessage || '解析失败',
            }
          }
        } catch (error) {
          return {
            ...initialResults[index],
            status: 'error',
            errorMessage: error.detail || error.message || '解析失败',
          }
        }
      })
    )

    setUrlParseResults(results)
    setUrlValidating(false)

    const successCount = results.filter(r => r.status === 'success').length
    const errorCount = results.filter(r => r.status === 'error').length
    if (successCount > 0) {
      messageApi.success(`解析完成：${successCount} 个成功，${errorCount} 个失败`)
    } else {
      messageApi.error('所有URL解析失败')
    }
  }

  // 批量URL导入提交
  const handleBatchUrlImport = async () => {
    const selectedItems = urlParseResults.filter(item => item.selected && item.status === 'success')
    if (selectedItems.length === 0) {
      messageApi.warning('请选择至少一个有效的URL')
      return
    }

    setLoading(true)
    try {
      const items = selectedItems.map(item => ({
        episodeIndex: item.episodeIndex,
        content: item.url,
        title: item.title || undefined,
      }))

      const res = await batchManualImport({
        sourceId: sourceInfo.sourceId,
        items,
      })

      if (res.data) {
        messageApi.success(res.data.message || `成功导入 ${selectedItems.length} 个分集`)
        onSuccess(res.data)
        clearUrlState()
      }
    } catch (error) {
      console.error('批量URL导入失败:', error)
      messageApi.error(error.detail || error.message || '批量URL导入失败')
    } finally {
      setLoading(false)
    }
  }

  // 清空URL相关状态
  const clearUrlState = () => {
    setUrlListInput('')
    setUrlParseResults([])
    setStartEpisodeIndex(1)
  }

  const handleOk = async () => {
    // URL导入模式
    if (importMode === 'url') {
      await handleBatchUrlImport()
      return
    }

    // XML导入模式
    if (!sourceInfo?.sourceId) return
    try {
      if (xmlDataList.length === 0) {
        messageApi.warn('未解析到任何有效条目！')
        return
      }
      setLoading(true)
      const res = await batchManualImport({
        sourceId: sourceInfo.sourceId,
        items: xmlDataList.map(it => {
          return {
            episodeIndex: it.episodeIndex,
            content: it.content,
            title: it.title ?? undefined,
          }
        }),
      })
      if (res.data) {
        onSuccess(res.data)
        clearAll()
      }
    } catch (error) {
      console.error('批量导入失败:', error)
      messageApi.error(
        error.detail || error.message || '批量导入失败，请检查内容格式和日志'
      )
    } finally {
      setLoading(false)
    }
  }

  const clearAll = () => {
    // 清空文件列表
    setXmlDataList([])
    // 重置顺序计数器
    orderCounter.current = 0
    // 重置上传状态
    setUploading(false)
    // 清空上传组件的内部文件列表
    setFileList([])
    // 清空URL状态
    clearUrlState()
  }

  const handleDelete = item => {
    setXmlDataList(list => {
      const activeIndex = list.findIndex(o => o.id === item.id)
      const newList = [...list]
      newList.splice(activeIndex, 1)
      return newList
    })
  }

  const handleEditIndex = (item, value) => {
    setXmlDataList(list => {
      return list.map(it => {
        if (it.id === item.id) {
          return {
            ...it,
            episodeIndex: value,
          }
        } else {
          return it
        }
      })
    })
  }

  const handleEditTitle = (item, value) => {
    setXmlDataList(list => {
      return list.map(it => {
        if (it.id === item.id) {
          return {
            ...it,
            title: value,
          }
        } else {
          return it
        }
      })
    })
  }

  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = xmlDataList.find(item => item.id === active.id)
    setActiveItem(item)
  }

  const handleDragEnd = event => {
    const { active, over } = event
    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    setXmlDataList(list => {
      const activeIndex = list.findIndex(
        item => item.id === active.data.current.item.id
      )
      const overIndex = list.findIndex(
        item => item.id === over.data.current.item.id
      )

      if (activeIndex !== -1 && overIndex !== -1) {
        // 1. 重新排列数组
        const newList = [...xmlDataList]
        const [movedItem] = newList.splice(activeIndex, 1)
        newList.splice(overIndex, 0, movedItem)

        return newList
      }
      return list
    })

    setActiveItem(null)
  }

  /**
   * 处理文件上传
   * @param {Array} files - 上传的文件列表
   */
  const handleUpload = async ({ file }) => {
    const fileOrder = orderCounter.current++
    fileOrderMap.current[file.uid] = fileOrder
    setUploading(true)

    try {
      // 创建文件读取器
      const reader = new FileReader()

      reader.onload = async e => {
        try {
          const xmlContent = e.target.result
          // 将解析结果添加到列表
          setXmlDataList(prev => {
            const newItems = [
              ...prev,
              {
                id: file.uid,
                episodeIndex: fileOrder + 1,
                title: file.name?.split('.')?.[0],
                content: xmlContent,
                size: file.size,
                order: fileOrder,
              },
            ]

            newItems.sort((a, b) => a.order - b.order)
            const firstIndex = newItems?.[0]?.episodeIndex ?? 1
            return newItems.map((it, i) => {
              return {
                ...it,
                episodeIndex: firstIndex + i,
              }
            })
          })
        } catch (error) {
          messageApi.error(`文件 ${file.name} 解析失败: ${error.message}`)
        }
      }

      reader.readAsText(file)
    } catch (error) {
      messageApi.error(`文件处理失败: ${error.message}`)
    } finally {
      setUploading(false)
    }
  }

  const handleChange = ({ file, fileList }) => {
    // 更新文件列表状态
    setFileList(fileList)

    if (file.status === 'uploading') {
      setUploading(true)
    }
    if (file.status === 'done' || file.status === 'error') {
      setUploading(false)
    }
  }

  const uploadProps = {
    accept: '.xml',
    multiple: true,
    showUploadList: false,
    beforeUpload: () => true,
    customRequest: handleUpload,
    fileList: fileList,
    onChange: handleChange,
    maxCount: 20,
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
          {/* 保留你原有的列表项渲染逻辑 */}
          <div className="w-full flex items-center justify-between">
            <div>
              <MyIcon icon="drag" size={24} />
            </div>
            <div className="w-full flex items-center justify-start gap-3">
              <InputNumber value={activeItem.episodeIndex} />
              <Input
                style={{
                  width: '100%',
                }}
                value={activeItem.title}
              />
              <div>
                <CloseCircleOutlined />
              </div>
            </div>
          </div>
        </List.Item>
      </div>
    )
  }

  // 更新解析结果中的某一项
  const updateParseResult = (id, updates) => {
    setUrlParseResults(results =>
      results.map(item =>
        item.id === id ? { ...item, ...updates } : item
      )
    )
  }

  // 全选/取消全选
  const handleSelectAll = (checked) => {
    setUrlParseResults(results =>
      results.map(item => ({
        ...item,
        selected: item.status === 'success' ? checked : false
      }))
    )
  }

  // 一键应用集数（从起始集数开始递增）
  const handleApplyEpisodeIndex = () => {
    setUrlParseResults(results =>
      results.map((item, index) => ({
        ...item,
        episodeIndex: startEpisodeIndex + index
      }))
    )
  }

  // 渲染批量URL导入界面
  const renderUrlImport = () => {
    const successCount = urlParseResults.filter(r => r.status === 'success').length
    const selectedCount = urlParseResults.filter(r => r.selected && r.status === 'success').length
    const allSuccessSelected = successCount > 0 && selectedCount === successCount

    return (
      <div className="p-4">
        <div className="mb-4">
          <div className="text-gray-500 dark:text-gray-400 mb-2">
            输入多个视频URL（每行一个），系统将批量解析并导入
            {!isCustomSource && <span className="text-orange-500 ml-1">（仅支持 {sourceInfo?.providerName} 平台的链接）</span>}
          </div>
          <Input.TextArea
            placeholder={`请输入视频URL，每行一个\n例如：\nhttps://www.bilibili.com/video/BV1xxx\nhttps://www.bilibili.com/video/BV2xxx`}
            value={urlListInput}
            onChange={e => {
              setUrlListInput(e.target.value)
              setUrlParseResults([])
            }}
            rows={5}
          />
          <div className="flex items-center justify-between mt-4">
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 text-sm">起始集数：</span>
              <InputNumber
                value={startEpisodeIndex}
                onChange={setStartEpisodeIndex}
                min={1}
                size="small"
                style={{ width: 80 }}
              />
            </div>
            <Button
              type="primary"
              onClick={handleBatchValidateUrls}
              loading={urlValidating}
              disabled={!urlListInput.trim()}
            >
              批量解析URL
            </Button>
          </div>
        </div>

        {/* 解析结果列表 */}
        {urlParseResults.length > 0 && (
          <div className="border dark:border-gray-600 rounded-lg">
            <div className="p-3 border-b dark:border-gray-600 flex items-center justify-between bg-gray-50 dark:bg-gray-800">
              <div className="flex items-center gap-3">
                <Checkbox
                  checked={allSuccessSelected}
                  indeterminate={selectedCount > 0 && selectedCount < successCount}
                  onChange={e => handleSelectAll(e.target.checked)}
                  disabled={successCount === 0}
                >
                  全选
                </Checkbox>
                <span className="text-gray-500 dark:text-gray-400 text-sm">
                  已选择 {selectedCount}/{successCount} 个有效项
                </span>
              </div>
              <Button size="small" onClick={handleApplyEpisodeIndex}>
                一键应用集数
              </Button>
            </div>
            <div className="max-h-64 overflow-y-auto">
              {urlParseResults.map((item, index) => (
                <div
                  key={item.id}
                  className={`p-3 border-b dark:border-gray-700 last:border-b-0 flex items-center gap-3 ${
                    item.status === 'error' ? 'bg-red-50 dark:bg-red-900/20' : ''
                  }`}
                >
                  <Checkbox
                    checked={item.selected}
                    onChange={e => updateParseResult(item.id, { selected: e.target.checked })}
                    disabled={item.status !== 'success'}
                  />
                  <InputNumber
                    value={item.episodeIndex}
                    onChange={value => updateParseResult(item.id, { episodeIndex: value })}
                    min={1}
                    size="small"
                    style={{ width: 60 }}
                    disabled={item.status !== 'success'}
                  />
                  <div className="flex-1 min-w-0">
                    {item.status === 'loading' ? (
                      <div className="flex items-center gap-2 text-gray-500">
                        <LoadingOutlined />
                        <span className="truncate">{item.url}</span>
                      </div>
                    ) : item.status === 'success' ? (
                      <div>
                        <Input
                          value={item.title}
                          onChange={e => updateParseResult(item.id, { title: e.target.value })}
                          placeholder="分集标题"
                          size="small"
                          className="mb-1"
                        />
                        <div className="text-xs text-gray-400 truncate" title={item.url}>
                          <CheckCircleOutlined className="text-green-500 mr-1" />
                          {item.provider} | {item.url}
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div className="text-red-500 dark:text-red-400 text-sm flex items-center gap-1">
                          <ExclamationCircleOutlined />
                          {item.errorMessage}
                        </div>
                        <div className="text-xs text-gray-400 truncate" title={item.url}>
                          {item.url}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  // 计算选中的有效项数量
  const selectedValidCount = urlParseResults.filter(r => r.selected && r.status === 'success').length

  return (
    <Modal
      title={`批量导入 - ${sourceInfo?.animeName} (${sourceInfo?.providerName})`}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      destroyOnHidden
      okText={importMode === 'url' ? `导入选中 (${selectedValidCount})` : '确定'}
      okButtonProps={{
        disabled: importMode === 'url' && selectedValidCount === 0
      }}
      width={importMode === 'url' ? 700 : 520}
    >
      {/* 导入模式切换 - 所有源都显示 */}
      <div className="mb-4">
        <Segmented
          value={importMode}
          onChange={value => {
            setImportMode(value)
            // 切换模式时清空状态
            if (value === 'xml') {
              clearUrlState()
            } else {
              clearAll()
            }
          }}
          options={[
            { label: <span><UploadOutlined className="mr-1" />XML文件导入</span>, value: 'xml' },
            { label: <span><LinkOutlined className="mr-1" />URL批量导入</span>, value: 'url' },
          ]}
          block
        />
      </div>

      {/* URL导入模式 */}
      {importMode === 'url' ? (
        renderUrlImport()
      ) : (
        <>
          {/* XML文件导入模式 */}
          <div className="p-4 text-center">
            <Upload {...uploadProps} ref={uploadRef}>
              <div>
                <CloudUploadOutlined style={{ fontSize: 48, marginBottom: 16 }} className="dark:text-gray-400" />
                <div className="flex items-center justify-center">
                  <Button
                    type="primary"
                    icon={<UploadOutlined />}
                    loading={uploading}
                    disabled={uploading}
                  >
                    选择文件
                  </Button>
                  <div className="ml-2 dark:text-gray-300">或直接拖拽文件到此处</div>
                </div>
                <div className="mt-2 text-gray-500 dark:text-gray-400">
                  {isCustomSource
                    ? '支持批量上传，仅接受.xml格式文件'
                    : `支持批量上传，接受.xml格式文件或 ${sourceInfo?.providerName} 的视频URL`
                  }
                </div>
              </div>
            </Upload>
          </div>
          {xmlDataList.length === 0 ? (
            <div className="text-center py-10 text-gray-500 dark:text-gray-400">
              <Empty description="暂无解析数据，请上传XML文件" />
            </div>
          ) : (
            <>
              <Tooltip title="以第一个文件集为准依次自增1">
                <Button
                  onClick={() => {
                    setXmlDataList(list => {
                      const index = list[0]?.episodeIndex
                      return list.map((it, i) => {
                        return {
                          ...it,
                          episodeIndex: index + i,
                        }
                      })
                    })
                  }}
                >
                  一键应用集数
                </Button>
              </Tooltip>
              <DndContext
                sensors={sensors}
                collisionDetection={closestCorners}
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={xmlDataList.map(item => item.id)}
                  strategy={verticalListSortingStrategy}
                >
                  <List
                    itemLayout="vertical"
                    size="large"
                    pagination={{
                      pageSize: pageSize,
                      onShowSizeChange: (_, size) => {
                        setPageSize(size)
                      },
                      hideOnSinglePage: true,
                    }}
                    dataSource={xmlDataList}
                    renderItem={(item, index) => (
                      <SortableItem
                        key={item.id}
                        item={item}
                        index={index}
                        handleDelete={() => handleDelete(item)}
                        handleEditTitle={value => handleEditTitle(item, value)}
                        handleEditIndex={value => handleEditIndex(item, value)}
                      />
                    )}
                  />
                </SortableContext>

                {/* 拖拽覆盖层 */}
                <DragOverlay>{renderDragOverlay()}</DragOverlay>
              </DndContext>
            </>
          )}
        </>
      )}
    </Modal>
  )
}

const SortableItem = ({
  item,
  index,
  handleDelete,
  handleEditTitle,
  handleEditIndex,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.id,
    data: {
      item,
      index,
    },
  })

  const inputRef = useRef(null)
  const [isFocused, setIsFocused] = useState(false)

  const inputNumberRef = useRef(null)
  const [isNumberFocused, setIsNumberFocused] = useState(false)

  useEffect(() => {
    if (isFocused && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isFocused, item.title])

  useEffect(() => {
    if (isNumberFocused && inputNumberRef.current) {
      inputNumberRef.current.focus()
    }
  }, [isNumberFocused, item.episodeIndex])

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
        {/* 将attributes移到拖拽图标容器上，确保只有拖拽图标可触发拖拽 */}
        <div {...attributes} {...listeners} style={{ cursor: 'grab' }}>
          <MyIcon icon="drag" size={24} />
        </div>
        <div className="w-full flex items-center justify-start gap-3">
          <InputNumber
            ref={inputNumberRef}
            value={item.episodeIndex}
            onChange={value => {
              handleEditIndex(value)
            }}
            onFocus={() => setIsNumberFocused(true)}
            onBlur={() => setIsNumberFocused(false)}
          />
          <Input
            ref={inputRef}
            style={{
              width: '100%',
            }}
            key={item.title}
            value={item.title}
            onChange={e => {
              handleEditTitle(e.target.value)
            }}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
          />
          <div onClick={() => handleDelete(item)}>
            <CloseCircleOutlined />
          </div>
        </div>
      </div>
    </List.Item>
  )
}

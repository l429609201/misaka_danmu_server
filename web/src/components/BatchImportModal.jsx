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
import { useTranslation } from 'react-i18next'
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

/**
 * 验证 XML 字符串是否格式良好
 * @param {string} content - XML 字符串
 * @returns {{ valid: boolean, error: string | null }}
 */
function validateXmlContent(content) {
  try {
    const parser = new DOMParser()
    const doc = parser.parseFromString(content, 'text/xml')
    const errorNode = doc.querySelector('parsererror')
    return { valid: !errorNode, error: errorNode ? errorNode.textContent.slice(0, 200) : null }
  } catch (e) {
    return { valid: false, error: e.message }
  }
}

/**
 * 尝试修复 XML 格式问题（非法控制字符、未转义 & 等）
 * @param {string} content - 原始 XML 字符串
 * @returns {{ repaired: string | null, issues: string[] }}
 */
function repairXmlContent(content) {
  let repaired = content
  const issues = []

  // 策略1：清除 XML 1.0 非法控制字符 (\x00-\x08, \x0B, \x0C, \x0E-\x1F)
  const cleaned = content.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, '')
  if (cleaned !== content) {
    issues.push('非法控制字符')
    repaired = cleaned
  }

  // 策略2：修复未转义的 & 符号
  const ampFixed = repaired.replace(/&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)/g, '&amp;')
  if (ampFixed !== repaired) {
    issues.push('未转义 & 符号')
    repaired = ampFixed
  }

  // 验证修复后的内容
  const validation = validateXmlContent(repaired)
  if (validation.valid) {
    return { repaired, issues }
  }
  return { repaired: null, issues }
}

export const BatchImportModal = ({ open, sourceInfo, onCancel, onSuccess }) => {
  const { t } = useTranslation()
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

  // XML 修复相关状态
  const [xmlRepairModalOpen, setXmlRepairModalOpen] = useState(false)

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
      messageApi.warning(t('batchImport.inputAtLeastOneUrl'))
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
                  errorMessage: t('batchImport.sourceMismatch', { provider: res.data.provider }),
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
              errorMessage: res.data?.errorMessage || t('batchImport.parseFailed'),
            }
          }
        } catch (error) {
          return {
            ...initialResults[index],
            status: 'error',
            errorMessage: error.detail || error.message || t('batchImport.parseFailed'),
          }
        }
      })
    )

    setUrlParseResults(results)
    setUrlValidating(false)

    const successCount = results.filter(r => r.status === 'success').length
    const errorCount = results.filter(r => r.status === 'error').length
    if (successCount > 0) {
      messageApi.success(t('batchImport.parseDone', { success: successCount, error: errorCount }))
    } else {
      messageApi.error(t('batchImport.allUrlFailed'))
    }
  }

  // 批量URL导入提交
  const handleBatchUrlImport = async () => {
    const selectedItems = urlParseResults.filter(item => item.selected && item.status === 'success')
    if (selectedItems.length === 0) {
      messageApi.warning(t('batchImport.selectAtLeastOneUrl'))
      return
    }

    setLoading(true)
    try {
      const items = selectedItems.map(item => ({
        episodeIndex: item.episodeIndex,
        content: item.url,
        // 自定义源仍需真实平台抓取，不能让视频 URL 落入 XML 导入分支。
        urlProvider: item.provider,
        title: item.title || undefined,
      }))

      const res = await batchManualImport({
        sourceId: sourceInfo.sourceId,
        items,
      })

      if (res.data) {
        messageApi.success(res.data.message || t('batchImport.importSuccess', { count: selectedItems.length }))
        onSuccess(res.data)
        clearUrlState()
      }
    } catch (error) {
      console.error('批量URL导入失败:', error)
      messageApi.error(error.detail || error.message || t('batchImport.urlImportFailed'))
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
    if (xmlDataList.length === 0) {
      messageApi.warn(t('batchImport.noValidItems'))
      return
    }

    // 检查是否有格式问题的文件，如有则先弹出修复确认框
    const hasIssueItems = xmlDataList.filter(it => it.hasIssue)
    if (hasIssueItems.length > 0) {
      setXmlRepairModalOpen(true)
      return
    }

    await doXmlImport(xmlDataList)
  }

  /**
   * 执行 XML 批量导入（内部复用）
   * @param {Array} list - 待导入的 xmlDataList（已修复或原始）
   */
  const doXmlImport = async (list) => {
    try {
      setLoading(true)
      const res = await batchManualImport({
        sourceId: sourceInfo.sourceId,
        items: list.map(it => ({
          episodeIndex: it.episodeIndex,
          content: it.content,
          title: it.title ?? undefined,
        })),
      })
      if (res.data) {
        onSuccess(res.data)
        clearAll()
      }
    } catch (error) {
      console.error('批量导入失败:', error)
      messageApi.error(
        error.detail || error.message || t('batchImport.batchImportFailed')
      )
    } finally {
      setLoading(false)
    }
  }

  /**
   * 修复所有有格式问题的 XML 条目，然后提交导入
   */
  const handleRepairAndImport = async () => {
    setXmlRepairModalOpen(false)
    const repairedList = xmlDataList.map(it => {
      if (!it.hasIssue || it.brokenXml) return it
      const { repaired } = repairXmlContent(it.content)
      if (repaired) {
        return { ...it, content: repaired, hasIssue: false, issueInfo: null, brokenXml: false }
      }
      return it
    })
    setXmlDataList(repairedList)
    await doXmlImport(repairedList)
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

          // 检测 XML 格式是否正常
          let hasIssue = false
          let issueInfo = null
          let brokenXml = false

          const validation = validateXmlContent(xmlContent)
          if (!validation.valid) {
            const { repaired, issues } = repairXmlContent(xmlContent)
            if (repaired) {
              hasIssue = true
              issueInfo = issues.join('、')
            } else {
              hasIssue = true
              brokenXml = true
              issueInfo = '结构性损坏，无法自动修复'
            }
          }

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
                hasIssue,
                issueInfo,
                brokenXml,
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
            {t('batchImport.urlImportDesc')}
            {!isCustomSource && <span className="text-orange-500 ml-1">{t('batchImport.onlySupportProvider', { provider: sourceInfo?.providerName })}</span>}
          </div>
          <Input.TextArea
            placeholder={t('batchImport.urlPlaceholder')}
            value={urlListInput}
            onChange={e => {
              setUrlListInput(e.target.value)
              setUrlParseResults([])
            }}
            rows={5}
          />
          <div className="flex items-center justify-between mt-4">
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 text-sm">{t('batchImport.startEpisode')}</span>
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
              {t('batchImport.batchParseUrl')}
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
                  {t('batchImport.selectAll')}
                </Checkbox>
                <span className="text-gray-500 dark:text-gray-400 text-sm">
                  {t('batchImport.selectedValid', { selected: selectedCount, success: successCount })}
                </span>
              </div>
              <Button size="small" onClick={handleApplyEpisodeIndex}>
                {t('batchImport.applyEpisodeIndex')}
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
                          placeholder={t('batchImport.episodeTitlePlaceholder')}
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
    <>
    <Modal
      title={t('batchImport.title', { name: sourceInfo?.animeName, provider: sourceInfo?.providerName })}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      destroyOnHidden
      okText={importMode === 'url' ? t('batchImport.importSelected', { count: selectedValidCount }) : t('common.confirm')}
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
            { label: <span><UploadOutlined className="mr-1" />{t('batchImport.xmlFileImport')}</span>, value: 'xml' },
            { label: <span><LinkOutlined className="mr-1" />{t('batchImport.urlBatchImport')}</span>, value: 'url' },
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
                    {t('batchImport.selectFile')}
                  </Button>
                  <div className="ml-2 dark:text-gray-300">{t('batchImport.dragFileHere')}</div>
                </div>
                <div className="mt-2 text-gray-500 dark:text-gray-400">
                  {isCustomSource
                    ? t('batchImport.xmlOnlyTip')
                    : t('batchImport.xmlOrUrlTip', { provider: sourceInfo?.providerName })
                  }
                </div>
              </div>
            </Upload>
          </div>
          {xmlDataList.length === 0 ? (
            <div className="text-center py-10 text-gray-500 dark:text-gray-400">
              <Empty description={t('batchImport.noParseData')} />
            </div>
          ) : (
            <>
              <Tooltip title={t('batchImport.autoIncrementTip')}>
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
                  {t('batchImport.applyEpisodeIndex')}
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

    {/* XML 格式修复确认弹窗 */}
    <Modal
      title={<span><ExclamationCircleOutlined className="text-orange-500 mr-2" />{t('batchImport.xmlRepairTitle')}</span>}
      open={xmlRepairModalOpen}
      onCancel={() => setXmlRepairModalOpen(false)}
      footer={[
        <Button key="cancel" onClick={() => setXmlRepairModalOpen(false)}>
          {t('common.cancel')}
        </Button>,
        <Button
          key="import-as-is"
          onClick={() => {
            setXmlRepairModalOpen(false)
            doXmlImport(xmlDataList)
          }}
        >
          {t('batchImport.xmlRepairImportAsIs')}
        </Button>,
        <Button
          key="repair-and-import"
          type="primary"
          onClick={handleRepairAndImport}
        >
          {t('batchImport.xmlRepairAndImport')}
        </Button>,
      ]}
    >
      <p className="mb-3">{t('batchImport.xmlRepairDesc')}</p>
      <ul className="list-disc list-inside text-sm space-y-1">
        {xmlDataList.filter(it => it.hasIssue).map(it => (
          <li key={it.id} className={it.brokenXml ? 'text-red-500' : 'text-orange-500'}>
            <span className="font-medium">{t('batchImport.episodeLabel', { index: it.episodeIndex })}</span>
            {it.title ? ` · ${it.title}` : ''}
            {' — '}
            {it.brokenXml
              ? t('batchImport.xmlBrokenCannotRepair')
              : it.issueInfo}
          </li>
        ))}
      </ul>
      {xmlDataList.some(it => it.hasIssue && it.brokenXml) && (
        <p className="mt-3 text-xs text-gray-500">
          {t('batchImport.xmlBrokenWillSkip')}
        </p>
      )}
    </Modal>
    </>
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
          {/* XML 格式问题提示图标 */}
          {item.hasIssue && (
            <Tooltip title={item.issueInfo}>
              <ExclamationCircleOutlined
                className={item.brokenXml ? 'text-red-500' : 'text-orange-500'}
                style={{ fontSize: 16, flexShrink: 0 }}
              />
            </Tooltip>
          )}
          <div onClick={() => handleDelete(item)}>
            <CloseCircleOutlined />
          </div>
        </div>
      </div>
    </List.Item>
  )
}

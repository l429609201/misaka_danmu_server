import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  deleteAnimeEpisode,
  deleteAnimeEpisodeSingle,
  editEpisode,
  getAnimeDetail,
  getAnimeSource,
  getEpisodes,
  offsetEpisodes,
  manualImportEpisode,
  refreshEpisodeDanmaku,
  refreshEpisodesBulk,
  resetEpisode,
  validateImportUrl,
  importFromUrl,
} from '../../apis'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Switch,
  Table,
  Tooltip,
  Upload,
  Tag,
  Typography,
} from 'antd'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon'
import {
  EditOutlined,
  HomeOutlined,
  HolderOutlined,
  UploadOutlined,
  VerticalAlignMiddleOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
} from '@ant-design/icons'
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Select, Segmented } from 'antd'
import { RoutePaths } from '../../general/RoutePaths'
import { useModal } from '../../ModalContext'
import { useMessage } from '../../MessageContext'
import { BatchImportModal } from '../../components/BatchImportModal'
import { DanmakuEditModal } from '../../components/DanmakuEditModal'
import { isUrl } from '../../utils/data'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store'
import { ResponsiveTable } from '@/components/ResponsiveTable'
import { useDefaultPageSize } from '../../hooks/useDefaultPageSize'

export const EpisodeDetail = () => {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const animeId = searchParams.get('animeId')
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const messageApi = useMessage()
  const modalApi = useModal()

  // 从后端配置获取默认分页大小
  const defaultPageSize = useDefaultPageSize('episode')

  const [loading, setLoading] = useState(true)
  const [animeDetail, setAnimeDetail] = useState({})
  const [episodeList, setEpisodeList] = useState([])
  const [selectedRows, setSelectedRows] = useState([])
  const [sourceInfo, setSourceInfo] = useState({})
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: defaultPageSize,
    total: 0,
  })

  const [form] = Form.useForm()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetLoading, setResetLoading] = useState(false)
  const [resetInfo, setResetInfo] = useState({})
  const [isBatchModalOpen, setIsBatchModalOpen] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const uploadRef = useRef(null)
  const deleteFilesRef = useRef(true) // 删除时是否同时删除弹幕文件，默认为 true
  const [uploading, setUploading] = useState(false)
  const [fileList, setFileList] = useState([])
  const [lastClickedIndex, setLastClickedIndex] = useState(null)

  // URL解析相关状态（手动导入分集时使用）
  const [urlValidating, setUrlValidating] = useState(false)
  const [urlValidationResult, setUrlValidationResult] = useState(null)
  // 手动导入模式: 'xml' | 'url' (仅自定义源使用)
  const [manualImportMode, setManualImportMode] = useState('xml')

  // 批量编辑相关状态
  const [isBatchEditModalOpen, setIsBatchEditModalOpen] = useState(false)
  const [batchEditData, setBatchEditData] = useState([])
  const [batchEditLoading, setBatchEditLoading] = useState(false)
  const [batchIndexMode, setBatchIndexMode] = useState('none') // none, offset, reorder
  const [batchOffsetValue, setBatchOffsetValue] = useState(0)
  const [batchReorderStart, setBatchReorderStart] = useState(1) // 按顺序重排的起始集数
  // ReNamer风格多规则批量重命名系统
  const [renameRules, setRenameRules] = useState([])
  const [selectedRuleType, setSelectedRuleType] = useState('replace')
  const [ruleParams, setRuleParams] = useState({})
  const [isPreviewMode, setIsPreviewMode] = useState(false)
  const [previewData, setPreviewData] = useState({})

  // 弹幕编辑弹窗状态
  const [isDanmakuEditModalOpen, setIsDanmakuEditModalOpen] = useState(false)

  // 当默认分页大小加载完成后，更新 pagination
  useEffect(() => {
    if (defaultPageSize) {
      setPagination(prev => ({
        ...prev,
        pageSize: defaultPageSize
      }))
    }
  }, [defaultPageSize])

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        setSelectedRows([])
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  const isXmlImport = useMemo(() => {
    return sourceInfo.providerName === 'custom'
  }, [sourceInfo])

  const getDetail = async () => {
    setLoading(true)
    try {
      // 如果 animeId 为 0 或无效，直接返回到库页面
      if (!animeId || Number(animeId) === 0) {
        messageApi.error('无效的作品ID')
        navigate('/library')
        return
      }

      const [detailRes, episodeRes, sourceRes] = await Promise.all([
        getAnimeDetail({
          animeId: Number(animeId),
        }),
        getEpisodes({
          sourceId: Number(id),
          page: pagination.current,
          pageSize: pagination.pageSize,
        }),
        getAnimeSource({
          animeId: Number(animeId),
        }),
      ])
      setAnimeDetail(detailRes.data)
      setEpisodeList(episodeRes.data?.list || [])
      setPagination(prev => ({
        ...prev,
        total: episodeRes.data?.total || 0,
      }))
      setSourceInfo({
        ...sourceRes?.data?.filter(it => it.sourceId === Number(id))?.[0],
        animeName: detailRes.data?.title,
      })
      setLoading(false)
    } catch (error) {
      messageApi.error('获取剧集详情失败')
      navigate(`/anime/${animeId}`)
    }
  }

  useEffect(() => {
    getDetail()
  }, [id, animeId, pagination.current, pagination.pageSize])

  // 处理 URL 参数 batchEdit=all，自动打开批量编辑弹窗
  const batchEditParam = searchParams.get('batchEdit')
  useEffect(() => {
    if (batchEditParam === 'all' && episodeList.length > 0 && !isBatchEditModalOpen) {
      openBatchEditModal(episodeList)
    }
  }, [batchEditParam, episodeList])

  const handleBatchImportSuccess = task => {
    setIsBatchModalOpen(false)
    // messageApi.success(
    //   `批量导入任务已提交 (ID: ${task.taskId})，请在任务中心查看进度。`
    // )
    goTask(task)
  }

  const columns = [
    {
      title: (
        <div className="flex items-center justify-center cursor-pointer" onClick={() => {
          if (selectedRows.length === episodeList.length && episodeList.length > 0) {
            setSelectedRows([])
          } else {
            setSelectedRows(episodeList)
          }
        }}>
          {selectedRows.length === episodeList.length && episodeList.length > 0 ? (
            <div className="w-4 h-4 bg-pink-400 rounded flex items-center justify-center">
              <span className="text-white text-xs">✓</span>
            </div>
          ) : (
            <div className="w-4 h-4 border border-gray-300 dark:border-gray-600 rounded"></div>
          )}
        </div>
      ),
      key: 'selection',
      width: 50,
      render: (_, record, index) => {
        const isSelected = selectedRows.some(row => row.episodeId === record.episodeId)
        return (
          <div
            className="cursor-pointer flex items-center justify-center"
            onClick={(e) => {
              const newSelected = [...selectedRows]
              if (e.shiftKey && lastClickedIndex !== null) {
                const start = Math.min(lastClickedIndex, index)
                const end = Math.max(lastClickedIndex, index)
                const range = episodeList.slice(start, end + 1)
                if (isSelected) {
                  // 如果当前已选，移除范围
                  setSelectedRows(selectedRows.filter(row => !range.some(r => r.episodeId === row.episodeId)))
                } else {
                  // 添加范围
                  const toAdd = range.filter(r => !selectedRows.some(s => s.episodeId === r.episodeId))
                  setSelectedRows([...selectedRows, ...toAdd])
                }
              } else {
                if (isSelected) {
                  setSelectedRows(selectedRows.filter(row => row.episodeId !== record.episodeId))
                } else {
                  setSelectedRows([...selectedRows, record])
                }
              }
              setLastClickedIndex(index)
            }}
          >
            {isSelected ? (
              <div className="w-4 h-4 bg-primary rounded flex items-center justify-center">
                <span className="text-white text-xs">✓</span>
              </div>
            ) : (
              <div className="w-4 h-4 border border-gray-300 dark:border-gray-600 rounded"></div>
            )}
          </div>
        )
      },
    },
    {
      title: 'ID',
      dataIndex: 'episodeId',
      key: 'episodeId',
      width: 150,
    },
    {
      title: '剧集名',
      dataIndex: 'title',
      key: 'title',
      width: 200,
    },
    {
      title: '集数',
      dataIndex: 'episodeIndex',
      key: 'episodeIndex',
      width: 80,
      sorter: {
        compare: (a, b) => a.episodeIndex - b.episodeIndex,
        multiple: 1,
      },
    },
    {
      title: '弹幕数',
      dataIndex: 'commentCount',
      key: 'commentCount',
      width: 80,
    },

    {
      title: '采集时间',
      dataIndex: 'fetchedAt',
      key: 'fetchedAt',
      width: 160,
      render: (_, record) => {
        return (
          <Typography.Text>{dayjs(record.fetchedAt).format('YYYY-MM-DD HH:mm:ss')}</Typography.Text>
        )
      },
    },
    {
      title: '官方链接',
      dataIndex: 'sourceUrl',
      key: 'sourceUrl',
      width: 100,
      render: (_, record) => {
        return (
          <div>
            {isUrl(record.sourceUrl) ? (
              <a
                href={record.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                跳转
              </a>
            ) : (
              '--'
            )}
          </div>
        )
      },
    },
    {
      title: '操作',
      width: isXmlImport ? 90 : 120,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title="编辑分集信息">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  form.setFieldsValue({
                    ...record,
                    episodeId: record.episodeId,
                    originalEpisodeIndex: record.episodeIndex,
                  })
                  setIsEditing(true)
                  setEditOpen(true)
                }}
              >
                <MyIcon icon="edit" size={20} />
              </span>
            </Tooltip>
            {!isXmlImport && (
              <Tooltip title="刷新分集弹幕">
                <span
                  className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                  onClick={() => handleRefresh(record)}
                >
                  <MyIcon icon="refresh" size={20} />
                </span>
              </Tooltip>
            )}

            <Tooltip title="弹幕详情">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  navigate(`/comment/${record.episodeId}?episodeId=${id}`)
                }}
              >
                <MyIcon icon="comment" size={20} />
              </span>
            </Tooltip>
            <Tooltip title="删除">
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => deleteEpisodeSingle(record)}
              >
                <MyIcon icon="delete" size={20} />
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  // 可拖拽行组件
  const SortableRow = ({ id, data, index }) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
    const style = {
      transform: CSS.Transform.toString(transform),
      transition,
      opacity: isDragging ? 0.5 : 1,
    }
    const previewTitle = previewData[data.episodeId]
    const hasPreviewChange = isPreviewMode && previewTitle && previewTitle !== data.title
    return (
      <tr ref={setNodeRef} style={style} className="bg-white dark:bg-gray-800">
        <td className="p-2 border border-gray-200 dark:border-gray-600 cursor-move" {...attributes} {...listeners}>
          <HolderOutlined />
        </td>
        <td className="p-2 border border-gray-200 dark:border-gray-600 text-xs">{data.episodeId}</td>
        <td className="p-2 border border-gray-200 dark:border-gray-600">
          {hasPreviewChange ? (
            <div className="text-sm">
              <span className="text-gray-400 line-through">{data.title}</span>
              <span className="mx-1 text-blue-500">→</span>
              <span className="text-green-600 dark:text-green-400 font-medium">{previewTitle}</span>
            </div>
          ) : (
            <Input
              size="small"
              value={data.title}
              onChange={(e) => {
                setBatchEditData(prev => prev.map((item, i) => i === index ? { ...item, title: e.target.value } : item))
              }}
            />
          )}
        </td>
        <td className="p-2 border border-gray-200 dark:border-gray-600">
          <InputNumber
            size="small"
            min={1}
            value={data.episodeIndex}
            onChange={(val) => {
              setBatchEditData(prev => prev.map((item, i) => i === index ? { ...item, episodeIndex: val } : item))
            }}
          />
        </td>
      </tr>
    )
  }

  // 拖拽传感器
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  // 拖拽结束处理
  const handleDragEnd = (event) => {
    const { active, over } = event
    if (active.id !== over?.id) {
      setBatchEditData((items) => {
        const oldIndex = items.findIndex(item => item.episodeId === active.id)
        const newIndex = items.findIndex(item => item.episodeId === over.id)
        return arrayMove(items, oldIndex, newIndex)
      })
    }
  }

  // 打开批量编辑弹窗
  const openBatchEditModal = (episodes) => {
    setBatchEditData(episodes.map(ep => ({ ...ep })))
    setBatchIndexMode('none')
    setBatchOffsetValue(0)
    setBatchReorderStart(1)
    // 重置多规则系统
    setRenameRules([])
    setSelectedRuleType('replace')
    setRuleParams({})
    setIsPreviewMode(false)
    setPreviewData({})
    setIsBatchEditModalOpen(true)
  }

  // 应用批量偏移（预览）
  const handleApplyBatchOffset = () => {
    if (!batchOffsetValue) return
    setBatchEditData(prev => prev.map(item => ({
      ...item,
      episodeIndex: item.episodeIndex + batchOffsetValue
    })))
    setBatchOffsetValue(0)
  }

  // 应用按顺序重排集数（预览）
  const handleApplyBatchReorder = () => {
    setBatchEditData(prev => prev.map((item, index) => ({
      ...item,
      episodeIndex: batchReorderStart + index
    })))
  }

  // 规则类型配置
  const ruleTypeOptions = [
    { value: 'replace', label: '替换' },
    { value: 'regex', label: '正则' },
    { value: 'insert', label: '插入' },
    { value: 'delete', label: '删除' },
    { value: 'serialize', label: '序列化' },
    { value: 'case', label: '大小写' },
    { value: 'strip', label: '清理' },
  ]

  // 应用单条规则到标题
  const applyRule = (title, rule, index) => {
    if (!rule.enabled) return title
    try {
      switch (rule.type) {
        case 'replace':
          return rule.params.caseSensitive
            ? title.split(rule.params.search).join(rule.params.replace || '')
            : title.replace(new RegExp(rule.params.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), rule.params.replace || '')
        case 'regex':
          return title.replace(new RegExp(rule.params.pattern, 'g'), rule.params.replace || '')
        case 'insert':
          if (rule.params.position === 'start') return (rule.params.text || '') + title
          if (rule.params.position === 'end') return title + (rule.params.text || '')
          const pos = parseInt(rule.params.index) || 0
          return title.slice(0, pos) + (rule.params.text || '') + title.slice(pos)
        case 'delete':
          const deleteMode = rule.params.mode || 'text'

          switch (deleteMode) {
            case 'text':
              // 删除指定文本
              return rule.params.caseSensitive
                ? title.split(rule.params.text).join('')
                : title.replace(new RegExp(rule.params.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), '')

            case 'first':
              // 删除前N个字符
              const firstCount = parseInt(rule.params.count) || 0
              return title.slice(firstCount)

            case 'last':
              // 删除后N个字符
              const lastCount = parseInt(rule.params.count) || 0
              return title.slice(0, -lastCount || undefined)

            case 'toText':
              // 从开头删除到指定文本（包含该文本）
              const toText = rule.params.text || ''
              if (!toText) return title
              const toIndex = rule.params.caseSensitive
                ? title.indexOf(toText)
                : title.toLowerCase().indexOf(toText.toLowerCase())
              return toIndex >= 0 ? title.slice(toIndex + toText.length) : title

            case 'fromText':
              // 从指定文本删除到结尾（包含该文本）
              const fromText = rule.params.text || ''
              if (!fromText) return title
              const fromIndex = rule.params.caseSensitive
                ? title.indexOf(fromText)
                : title.toLowerCase().indexOf(fromText.toLowerCase())
              return fromIndex >= 0 ? title.slice(0, fromIndex) : title

            case 'range':
              // 删除指定范围（从位置X删除Y个字符）
              const from = parseInt(rule.params.from) || 0
              const count = parseInt(rule.params.count) || 0
              return title.slice(0, from) + title.slice(from + count)

            default:
              return title
          }
        case 'serialize':
          const start = parseInt(rule.params.start) || 1
          const step = parseInt(rule.params.step) || 1
          const digits = parseInt(rule.params.digits) || 2
          const num = String(start + index * step).padStart(digits, '0')
          const serialized = (rule.params.prefix || '') + num + (rule.params.suffix || '')
          if (rule.params.position === 'start') return serialized + title
          if (rule.params.position === 'end') return title + serialized
          return serialized // 替换原标题
        case 'case':
          if (rule.params.mode === 'upper') return title.toUpperCase()
          if (rule.params.mode === 'lower') return title.toLowerCase()
          if (rule.params.mode === 'title') return title.charAt(0).toUpperCase() + title.slice(1).toLowerCase()
          return title
        case 'strip':
          let result = title
          if (rule.params.trimSpaces) result = result.trim()
          if (rule.params.trimDuplicateSpaces) result = result.replace(/\s+/g, ' ')
          if (rule.params.chars) result = result.split(rule.params.chars).join('')
          return result
        default:
          return title
      }
    } catch (e) {
      messageApi.error(`规则 "${ruleTypeOptions.find(r => r.value === rule.type)?.label}" 执行错误: ${e.message}`)
      return title
    }
  }

  // 应用所有规则到标题
  const applyAllRules = (title, index) => {
    return renameRules.reduce((t, rule) => applyRule(t, rule, index), title)
  }

  // 添加规则
  const handleAddRule = () => {
    // 验证必填参数
    if (selectedRuleType === 'replace' && !ruleParams.search) {
      messageApi.warning('请输入要查找的文本')
      return
    }
    if (selectedRuleType === 'regex' && !ruleParams.pattern) {
      messageApi.warning('请输入正则表达式')
      return
    }
    if (selectedRuleType === 'insert') {
      if (!ruleParams.text) {
        messageApi.warning('请输入要插入的文本')
        return
      }
      if (ruleParams.position === 'index' && ruleParams.index === undefined) {
        messageApi.warning('请输入插入位置')
        return
      }
    }
    if (selectedRuleType === 'delete') {
      const mode = ruleParams.mode || 'text'
      if ((mode === 'text' || mode === 'toText' || mode === 'fromText') && !ruleParams.text) {
        messageApi.warning('请输入文本')
        return
      }
      if ((mode === 'first' || mode === 'last' || mode === 'range') && !ruleParams.count) {
        messageApi.warning('请输入字符数')
        return
      }
      if (mode === 'range' && ruleParams.from === undefined) {
        messageApi.warning('请输入起始位置')
        return
      }
    }

    const newRule = {
      id: Date.now().toString(),
      type: selectedRuleType,
      enabled: true,
      params: { ...ruleParams }
    }
    setRenameRules(prev => [...prev, newRule])
    setRuleParams({})
    messageApi.success('规则已添加')
  }

  // 删除规则
  const handleDeleteRule = (ruleId) => {
    setRenameRules(prev => prev.filter(r => r.id !== ruleId))
  }

  // 切换规则启用状态
  const handleToggleRule = (ruleId) => {
    setRenameRules(prev => prev.map(r => r.id === ruleId ? { ...r, enabled: !r.enabled } : r))
  }

  // 监听规则变化，自动更新预览
  useEffect(() => {
    if (isPreviewMode) {
      if (renameRules.length > 0) {
        const preview = {}
        batchEditData.forEach((item, index) => {
          preview[item.episodeId] = applyAllRules(item.title, index)
        })
        setPreviewData(preview)
      } else {
        // 规则列表为空时清空预览
        setPreviewData({})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renameRules, isPreviewMode, batchEditData])

  // 预览效果
  const handlePreviewRules = () => {
    if (renameRules.length === 0) {
      messageApi.warning('请先添加规则')
      return
    }
    const preview = {}
    batchEditData.forEach((item, index) => {
      preview[item.episodeId] = applyAllRules(item.title, index)
    })
    setPreviewData(preview)
    setIsPreviewMode(true)
  }

  // 应用批量命名规则
  const handleApplyBatchRename = () => {
    if (renameRules.length === 0) {
      messageApi.warning('请先添加规则')
      return
    }
    setBatchEditData(prev => prev.map((item, index) => ({
      ...item,
      title: applyAllRules(item.title, index)
    })))
    setIsPreviewMode(false)
    setPreviewData({})
    messageApi.success('规则已应用')
  }

  // 提交批量编辑
  const handleBatchEditSubmit = async () => {
    setBatchEditLoading(true)
    try {
      for (const item of batchEditData) {
        await editEpisode({
          episodeId: item.episodeId,
          title: item.title,
          episodeIndex: item.episodeIndex,
          sourceUrl: item.sourceUrl,
        })
      }
      messageApi.success('批量编辑成功')
      setIsBatchEditModalOpen(false)
      getDetail()
    } catch (error) {
      messageApi.error('批量编辑失败: ' + error.message)
    } finally {
      setBatchEditLoading(false)
    }
  }

  const keepColumns = [
    {
      title: '集数',
      dataIndex: 'episodeIndex',
      key: 'episodeIndex',
      width: 60,
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      width: 200,
    },
    {
      title: '弹幕数',
      dataIndex: 'commentCount',
      key: 'commentCount',
      width: 60,
    },
  ]

  const handleBatchDelete = () => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: '删除分集',
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>您确定要删除选中的 {selectedRows.length} 个分集吗？</Typography.Text>
          <br />
          <Typography.Text>此操作将在后台提交一个批量删除任务。</Typography.Text>
          <div className="flex items-center gap-2 mt-3">
            <span>同时删除弹幕文件：</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisode({
            episodeIds: selectedRows?.map(it => it.episodeId),
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`提交批量删除任务失败:${error.message}`)
        }
      },
    })
  }

  const deleteEpisodeSingle = record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: '删除分集',
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>您确定要删除分集 '{record.title}' 吗？</Typography.Text>
          <br />
          <Typography.Text>此操作将在后台提交一个批量删除任务。</Typography.Text>
          <div className="flex items-center gap-2 mt-3">
            <span>同时删除弹幕文件：</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisodeSingle({
            id: record.episodeId,
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`提交删除任务失败:${error.message}`)
        }
      },
    })
  }

  const handleRefresh = record => {
    modalApi.confirm({
      title: '刷新分集',
      zIndex: 1002,
      content: <Typography.Text>您确定要刷新分集 '{record.title}' 的弹幕吗？</Typography.Text>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await refreshEpisodeDanmaku({
            id: record.episodeId,
          })
          messageApi.success(res.message || '刷新任务已开始。')
        } catch (error) {
          messageApi.error(`启动刷新任务失败:${error.message}`)
        }
      },
    })
  }

  const handleBatchRefresh = () => {
    if (!selectedRows.length) {
      messageApi.warning('请先选择要刷新的分集')
      return
    }

    modalApi.confirm({
      title: '批量刷新分集',
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>您确定要刷新选中的 {selectedRows.length} 个分集的弹幕吗？</Typography.Text>
          <br />
          <Typography.Text>此操作将在后台提交 {selectedRows.length} 个刷新任务。</Typography.Text>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const episodeIds = selectedRows.map(row => row.episodeId)
          const res = await refreshEpisodesBulk({ episodeIds })
          messageApi.success(res.message || '批量刷新任务已提交。')
        } catch (error) {
          messageApi.error(`提交批量刷新任务失败:${error.message}`)
        }
      },
    })
  }

  const goTask = res => {
    modalApi.confirm({
      title: '提示',
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>{res.data?.message || '任务已提交'}</Typography.Text>
          <br />
          <Typography.Text>是否立即跳转到任务管理器查看进度？</Typography.Text>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        navigate(`${RoutePaths.TASK}?status=all`)
      },
      onCancel: () => {
        getDetail()
        setSelectedRows([])
      },
    })
  }

  // URL解析函数（手动导入分集时使用）
  const handleValidateUrl = async (url) => {
    if (!url?.trim()) {
      messageApi.warning('请输入URL')
      return
    }

    setUrlValidating(true)
    setUrlValidationResult(null)

    try {
      const res = await validateImportUrl({ url: url.trim() })
      if (res.data) {
        // 对于非自定义源，检查 URL 的 provider 是否匹配当前源
        if (!isXmlImport && res.data.isValid) {
          const currentProvider = sourceInfo?.providerName?.toLowerCase()
          const urlProvider = res.data.provider?.toLowerCase()
          if (currentProvider !== urlProvider) {
            setUrlValidationResult({
              isValid: false,
              provider: res.data.provider,
              errorMessage: `URL来源 (${res.data.provider}) 与当前源 (${sourceInfo?.providerName}) 不匹配`
            })
            return
          }
        }
        setUrlValidationResult(res.data)
        // 自动填充表单字段
        if (res.data.isValid) {
          const currentValues = form.getFieldsValue()
          const updates = {}
          // 如果标题为空，自动填充
          if (!currentValues.title && res.data.title) {
            updates.title = res.data.title
          }
          // 如果URL解析出了集数，优先使用解析出的集数
          if (res.data.episodeIndex) {
            updates.episodeIndex = res.data.episodeIndex
          } else if (!currentValues.episodeIndex) {
            // 否则如果集数为空，填充下一集
            const nextEpisode = episodeList.length > 0
              ? Math.max(...episodeList.map(e => e.episodeIndex)) + 1
              : 1
            updates.episodeIndex = nextEpisode
          }
          if (Object.keys(updates).length > 0) {
            form.setFieldsValue(updates)
          }
        }
      }
    } catch (error) {
      console.error('URL校验失败:', error)
      setUrlValidationResult({
        isValid: false,
        errorMessage: error.detail || error.message || 'URL校验失败'
      })
    } finally {
      setUrlValidating(false)
    }
  }

  // 清空URL解析状态
  const clearUrlValidation = () => {
    setUrlValidationResult(null)
    setUrlValidating(false)
  }

  const handleSave = async () => {
    try {
      if (confirmLoading) return
      setConfirmLoading(true)
      const values = await form.validateFields()

      if (values.episodeId) {
        // 编辑模式
        await editEpisode({
          ...values,
          sourceId: Number(id),
        })
      } else if (isXmlImport && manualImportMode === 'url') {
        // 自定义源 URL 导入模式：在当前自定义源下创建分集，而非新建条目
        if (!urlValidationResult?.isValid) {
          messageApi.warning('请先解析URL')
          setConfirmLoading(false)
          return
        }
        await manualImportEpisode({
          sourceId: Number(id),
          episodeIndex: values.episodeIndex,
          title: values.title,
          sourceUrl: values.sourceUrl,
          urlProvider: urlValidationResult.provider,  // 传入解析出的真实平台名，后端用于 scraper 调用
        })
      } else {
        // 普通手动导入（XML或非自定义源URL）
        await manualImportEpisode({
          ...values,
          sourceId: Number(id),
        })
      }
      getDetail()
      form.resetFields()
      setUploading(false)
      // 清空上传组件的内部文件列表
      setFileList([])
      // 清空URL解析状态
      clearUrlValidation()
      setManualImportMode('xml')
      messageApi.success('分集信息更新成功！')
    } catch (error) {
      // 改进错误提示，处理对象类型的错误
      let errorMsg = '更新失败'
      if (error?.errorFields) {
        // 表单验证错误
        errorMsg = error.errorFields.map(f => f.errors.join(', ')).join('; ')
      } else if (error?.detail) {
        errorMsg = error.detail
      } else if (error?.message) {
        errorMsg = error.message
      } else if (typeof error === 'string') {
        errorMsg = error
      }
      messageApi.error(errorMsg)
    } finally {
      setConfirmLoading(false)
      setEditOpen(false)
    }
  }

  const handleOffset = () => {
    let offsetValue = 0
    modalApi.confirm({
      title: '集数偏移',
      icon: <VerticalAlignMiddleOutlined />,
      zIndex: 1002,
      content: (
        <div className="mt-4">
          <Typography.Text>请输入一个整数作为偏移量（可为负数）。</Typography.Text>
          <br />
          <Typography.Text className="text-gray-500 dark:text-gray-400 text-xs">
            例如：输入 12 会将第 1 集变为第 13 集。
          </Typography.Text>
          <InputNumber
            placeholder="输入偏移量, e.g., 12 or -5"
            onChange={value => (offsetValue = value)}
            style={{ width: '100%' }}
            autoFocus
          />
        </div>
      ),
      onOk: async () => {
        if (!offsetValue || !Number.isInteger(offsetValue)) {
          messageApi.warning('请输入一个有效的整数偏移量。')
          return
        }
        try {
          const res = await offsetEpisodes({
            episodeIds: selectedRows.map(it => it.episodeId),
            offset: offsetValue,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(error?.detail || '提交任务失败')
        }
      },
      okText: '确认',
      cancelText: '取消',
    })
  }

  const handleResetEpisode = () => {
    modalApi.confirm({
      title: '重整集数',
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>
            您确定要为 '{animeDetail.title}'的这个数据源重整集数吗？
          </Typography.Text>
          <br />
          <Typography.Text>此操作会按当前顺序将集数重新编号为 1, 2, 3...</Typography.Text>
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await resetEpisode({
            sourceId: Number(id),
          })
          goTask(res)
        } catch (error) {
          messageApi.error(`提交重整任务失败:${error.message}`)
        }
      },
    })
  }

  const handleResetMainEpisode = async () => {
    try {
      if (resetLoading) return
      setResetLoading(true)
      const episodeIds = resetInfo?.toDelete?.map(ep => Number(ep.episodeId))
      await deleteAnimeEpisode({
        episodeIds: episodeIds,
      })
      await resetEpisode({
        sourceId: Number(id),
      })
      messageApi.success('已提交：批量删除 + 重整集数 两个任务。')
    } catch (error) {
      messageApi.error(`提交任务失败: ${error.message}`)
    } finally {
      setResetInfo({})
      setResetOpen(false)
      setResetLoading(false)
    }
  }

  const handleUpload = async ({ file }) => {
    setUploading(true)

    try {
      // 创建文件读取器
      const reader = new FileReader()

      reader.onload = async e => {
        try {
          const xmlContent = e.target.result
          form.setFieldsValue({
            content: xmlContent,
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
    multiple: false,
    showUploadList: false,
    beforeUpload: () => true,
    customRequest: handleUpload,
    onChange: handleChange,
    fileList: fileList,
  }

  return (
    <div className="my-6">
      <Breadcrumb
        className="!mb-4"
        items={[
          {
            title: (
              <Link to="/">
                <HomeOutlined />
              </Link>
            ),
          },
          {
            title: <Link to="/library">弹幕库</Link>,
          },
          {
            title: (
              <Link to={`/anime/${animeId}`}>
                {animeDetail.title?.length > 10
                  ? animeDetail.title.slice(0, 10) + '...'
                  : animeDetail.title}
              </Link>
            ),
          },
          {
            title: '分集列表',
          },
        ]}
      />
      <Card loading={loading} title={`分集列表: ${animeDetail?.title ?? ''}`}>
        <div className="mb-3 text-sm text-gray-600 dark:text-gray-400">
          💡 {isMobile ? '点击卡片可选中/取消选中分集，支持Shift多选' : '点击复选框或卡片可选中/取消选中分集，支持Shift多选'}，用于批量操作
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <Button
            onClick={() => {
              handleBatchDelete()
            }}
            type="primary"
            disabled={!selectedRows.length}
          >
            删除选中
          </Button>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <Button
              onClick={() => openBatchEditModal(selectedRows)}
              disabled={!selectedRows.length}
            >
              <Tooltip title="批量编辑选中分集的标题和集数">
                <EditOutlined />
                <span className="ml-1">批量编辑</span>
              </Tooltip>
            </Button>
            <Button
              onClick={handleOffset}
              disabled={!selectedRows.length}
            >
              <Tooltip title="对所有选中的分集应用一个集数偏移量">
                <VerticalAlignMiddleOutlined />
                <span className="ml-1">集数偏移</span>
              </Tooltip>
            </Button>
            <Button
              onClick={() => {
                const validCounts = episodeList
                  .map(ep => Number(ep.commentCount))
                  .filter(n => Number.isFinite(n) && n >= 0)
                if (validCounts.length === 0) {
                  messageApi.error('所有分集的弹幕数不可用。')
                  return
                }
                const average =
                  validCounts.reduce((a, b) => a + b, 0) / validCounts.length
                const toDelete = episodeList.filter(
                  ep => Number(ep.commentCount) < average
                )
                const toKeep = episodeList.filter(
                  ep => Number(ep.commentCount) >= average
                )

                if (toDelete.length === 0) {
                  messageApi.error(
                    `未找到低于平均值 (${average.toFixed(2)}) 的分集。`
                  )
                  return
                }
                setResetInfo({
                  average,
                  toDelete,
                  toKeep,
                })
                setResetOpen(true)
              }}
              disabled={!episodeList.length}
            >
              正片重整
            </Button>
            <Button
              onClick={() => {
                handleResetEpisode()
              }}
              disabled={!episodeList.length}
            >
              重整集数
            </Button>
            <Button
              onClick={handleBatchRefresh}
              disabled={!selectedRows.length || isXmlImport}
            >
              <Tooltip title="批量刷新选中分集的弹幕">
                <MyIcon icon="refresh" size={16} />
                <span className="ml-1">批量刷新</span>
              </Tooltip>
            </Button>
            <Button
              onClick={() => setIsDanmakuEditModalOpen(true)}
              disabled={!episodeList.length}
            >
              <Tooltip title="弹幕时间偏移、分集拆分、合并等操作">
                <EditOutlined />
                <span className="ml-1">弹幕编辑</span>
              </Tooltip>
            </Button>
            {isXmlImport && (
              <Button
                onClick={() => {
                  setIsBatchModalOpen(true)
                }}
              >
                批量导入
              </Button>
            )}
            <Button
              onClick={() => {
                form.resetFields()
                setIsEditing(false)
                // 默认填充下一集的集数
                const nextEpisode = episodeList.length > 0
                  ? Math.max(...episodeList.map(e => e.episodeIndex)) + 1
                  : 1
                form.setFieldsValue({ episodeIndex: nextEpisode })
                clearUrlValidation()
                setEditOpen(true)
              }}
              type="primary"
            >
              手动导入
            </Button>
          </div>
        </div>
        <div className="mb-4"></div>
        {!!episodeList?.length ? (
          <ResponsiveTable
            pagination={{
              ...pagination,
              showTotal: total => `共 ${total} 条数据`,
              onChange: (page, pageSize) => {
                setPagination(n => {
                  return {
                    ...n,
                    current: page,
                    pageSize,
                  }
                })
              },
              onShowSizeChange: (_, size) => {
                setPagination(n => {
                  return {
                    ...n,
                    pageSize: size,
                  }
                })
              },
              hideOnSinglePage: true,
            }}
            size="small"
            dataSource={episodeList}
            columns={columns}
            rowKey={'episodeId'}
            tableProps={{ rowClassName: () => '' }}
            scroll={{ x: '100%' }}
            renderCard={(record) => {
              const isSelected = selectedRows.some(row => row.episodeId === record.episodeId);
              const index = episodeList.findIndex(ep => ep.episodeId === record.episodeId);
              return (
                <Card
                  size="small"
                  className={`hover:shadow-lg transition-all duration-300 mb-3 cursor-pointer relative ${isSelected ? 'shadow-lg ring-2 ring-pink-400/50 bg-pink-50/30 dark:bg-pink-900/10' : ''}`}
                  bodyStyle={{ padding: '12px' }}
                  onClick={(e) => {
                    // 如果点击的是按钮或链接，不触发选择
                    if (
                      e.target.closest('.ant-btn') ||
                      e.target.closest('a')
                    ) {
                      return
                    }

                    const currentIndex = episodeList.findIndex(ep => ep.episodeId === record.episodeId)
                    if (e.shiftKey && lastSelectedIndex !== null) {
                      const start = Math.min(lastSelectedIndex, currentIndex)
                      const end = Math.max(lastSelectedIndex, currentIndex)
                      const range = episodeList.slice(start, end + 1)
                      const newSelected = [...selectedRows]
                      range.forEach(ep => {
                        const isInSelected = newSelected.some(s => s.episodeId === ep.episodeId)
                        if (!isInSelected) {
                          newSelected.push(ep)
                        }
                      })
                      setSelectedRows(newSelected)
                    } else {
                      // 切换选中状态
                      if (isSelected) {
                        setSelectedRows(selectedRows.filter(row => row.episodeId !== record.episodeId))
                      } else {
                        setSelectedRows([...selectedRows, record])
                      }
                    }
                    setLastClickedIndex(index)
                  }}
                >
                  <div className="space-y-3 relative">
                    {isSelected && (
                      <div className="absolute -top-1 -right-1 w-3 h-3 bg-pink-400 rounded-full border-2 border-white dark:border-gray-800 z-10"></div>
                    )}
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <Tag color="blue" className="text-xs">
                              第{record.episodeIndex}集
                            </Tag>
                            <span className="text-sm font-medium text-gray-600 dark:text-gray-400">
                              ID: {record.episodeId}
                            </span>
                          </div>
                          <Button
                            size="small"
                            type="text"
                            danger
                            className="flex-shrink-0"
                            icon={<MyIcon icon="delete" size={16} />}
                            title="删除分集"
                            onClick={(e) => {
                              e.stopPropagation()
                              deleteEpisodeSingle(record)
                            }}
                          />
                        </div>
                        <Typography.Text className="font-semibold text-base mb-2 break-words">
                          {record.title}
                        </Typography.Text>
                        <div className="space-y-1">
                          <div className="flex items-center gap-4 text-sm">
                            <span className="flex items-center gap-1">
                              <MyIcon icon="comment" size={14} className="text-blue-500" />
                              <span className="text-gray-600 dark:text-gray-400">
                                {record.commentCount || 0} 条弹幕
                              </span>
                            </span>
                          </div>
                          {record.sourceUrl && isUrl(record.sourceUrl) && (
                            <div className="flex items-center gap-1">
                              <span className="text-xs text-gray-500 dark:text-gray-400">来源:</span>
                              <a
                                href={record.sourceUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs text-primary hover:text-primary-dark break-all"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {record.sourceUrl.length > 30 ? record.sourceUrl.substring(0, 30) + '...' : record.sourceUrl}
                              </a>
                            </div>
                          )}
                          <div className="text-xs text-gray-500 dark:text-gray-400">
                            采集时间: {dayjs(record.fetchedAt).format('YYYY-MM-DD HH:mm')}
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="pt-1 border-t border-gray-200 dark:border-gray-700">
                      <div className="flex justify-end gap-1">
                        <Button
                          size="small"
                          type="text"
                          icon={<MyIcon icon="edit" size={14} />}
                          title="编辑分集信息"
                          onClick={(e) => {
                            e.stopPropagation()
                            form.setFieldsValue({
                              ...record,
                              episodeId: record.episodeId,
                              originalEpisodeIndex: record.episodeIndex,
                              episodeIndex: Math.max(1, record.episodeIndex || 1),
                            })
                            setIsEditing(true)
                            setEditOpen(true)
                          }}
                        >
                          编辑
                        </Button>
                        {!isXmlImport && (
                          <Button
                            size="small"
                            type="text"
                            icon={<MyIcon icon="refresh" size={14} />}
                            title="刷新分集弹幕"
                            onClick={(e) => {
                              e.stopPropagation()
                              handleRefresh(record)
                            }}
                          >
                            刷新
                          </Button>
                        )}
                        <Button
                          size="small"
                          type="text"
                          icon={<MyIcon icon="comment" size={14} />}
                          title="查看弹幕详情"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/comment/${record.episodeId}?episodeId=${id}`)
                          }}
                        >
                          弹幕
                        </Button>
                      </div>
                    </div>
                  </div>
                </Card>
              );
            }}
          />
        ) : (
          <Empty />
        )}
      </Card>
      <Modal
        title={isEditing ? '编辑分集信息' : '手动导入分集'}
        open={editOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => {
          setEditOpen(false)
          setIsEditing(false)
          form.resetFields()
          clearUrlValidation()
          setManualImportMode('xml')
        }}
        zIndex={100}
        width={600}
      >
        {/* 自定义源且非编辑模式时，显示导入模式切换 */}
        {isXmlImport && !isEditing && (
          <div className="mb-4">
            <Segmented
              value={manualImportMode}
              onChange={value => {
                setManualImportMode(value)
                form.resetFields()
                clearUrlValidation()
                // 重新设置默认集数
                const nextEpisode = episodeList.length > 0
                  ? Math.max(...episodeList.map(e => e.episodeIndex)) + 1
                  : 1
                form.setFieldsValue({ episodeIndex: nextEpisode })
              }}
              options={[
                { label: <span><UploadOutlined className="mr-1" />XML导入</span>, value: 'xml' },
                { label: <span><LinkOutlined className="mr-1" />URL导入</span>, value: 'url' },
              ]}
              block
            />
          </div>
        )}

        <Form form={form} layout="horizontal">
          {/* 自定义源 URL 导入模式 */}
          {isXmlImport && !isEditing && manualImportMode === 'url' && (
            <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: 'var(--color-hover)' }}>
              <div className="text-gray-500 dark:text-gray-400 text-sm mb-2">
                <LinkOutlined className="mr-1" />
                输入其他平台的视频URL，系统将自动获取弹幕并导入到当前自定义源
              </div>
              <Form.Item
                name="sourceUrl"
                label="视频URL"
                rules={[
                  {
                    required: true,
                    message: `请输入视频URL`,
                  },
                ]}
                className="mb-2"
              >
                <Input.Search
                  placeholder="请输入视频URL，如 https://www.bilibili.com/video/BV..."
                  onSearch={handleValidateUrl}
                  onChange={() => setUrlValidationResult(null)}
                  enterButton={
                    <Button loading={urlValidating}>
                      解析URL
                    </Button>
                  }
                />
              </Form.Item>

              {/* URL解析结果显示 */}
              {urlValidationResult && (
                <div className={`p-3 rounded-lg ${urlValidationResult.isValid ? 'bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700' : 'bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700'}`}>
                  {urlValidationResult.isValid ? (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <CheckCircleOutlined className="text-green-500" />
                        <span className="font-medium text-green-700 dark:text-green-400 text-sm">URL解析成功</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-xs">
                        <div><span className="text-gray-500 dark:text-gray-400">平台：</span><span className="dark:text-gray-200">{urlValidationResult.provider}</span></div>
                        <div><span className="text-gray-500 dark:text-gray-400">媒体ID：</span><span className="dark:text-gray-200">{urlValidationResult.mediaId}</span></div>
                        {urlValidationResult.title && (
                          <div className="col-span-2"><span className="text-gray-500 dark:text-gray-400">标题：</span><span className="dark:text-gray-200">{urlValidationResult.title}</span></div>
                        )}
                        {urlValidationResult.mediaType && (
                          <div><span className="text-gray-500 dark:text-gray-400">类型：</span><span className="dark:text-gray-200">{urlValidationResult.mediaType === 'movie' ? '电影' : '剧集'}</span></div>
                        )}
                        {urlValidationResult.episodeIndex && (
                          <div><span className="text-gray-500 dark:text-gray-400">集数：</span><span className="dark:text-gray-200">第 {urlValidationResult.episodeIndex} 集</span></div>
                        )}
                      </div>
                      {urlValidationResult.imageUrl && (
                        <div className="mt-2">
                          <img src={urlValidationResult.imageUrl} alt="封面" className="h-20 rounded" />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ExclamationCircleOutlined className="text-red-500" />
                      <span className="text-red-700 dark:text-red-400 text-sm">{urlValidationResult.errorMessage || 'URL解析失败'}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 非自定义源且非编辑模式时，显示URL解析功能 */}
          {!isXmlImport && !isEditing && (
            <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: 'var(--color-hover)' }}>
              <div className="text-gray-500 dark:text-gray-400 text-sm mb-2">
                <LinkOutlined className="mr-1" />
                输入 {sourceInfo?.providerName} 平台的视频URL，可自动解析标题
              </div>
              <Form.Item
                name="sourceUrl"
                label="官方链接"
                rules={[
                  {
                    required: true,
                    message: `请输入官方链接`,
                  },
                ]}
                className="mb-2"
              >
                <Input.Search
                  placeholder={`请输入 ${sourceInfo?.providerName} 的视频URL`}
                  onSearch={handleValidateUrl}
                  onChange={() => setUrlValidationResult(null)}
                  enterButton={
                    <Button loading={urlValidating}>
                      解析URL
                    </Button>
                  }
                />
              </Form.Item>

              {/* URL解析结果显示 */}
              {urlValidationResult && (
                <div className={`p-3 rounded-lg ${urlValidationResult.isValid ? 'bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700' : 'bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700'}`}>
                  {urlValidationResult.isValid ? (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <CheckCircleOutlined className="text-green-500" />
                        <span className="font-medium text-green-700 dark:text-green-400 text-sm">URL解析成功</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-xs">
                        <div><span className="text-gray-500 dark:text-gray-400">平台：</span><span className="dark:text-gray-200">{urlValidationResult.provider}</span></div>
                        <div><span className="text-gray-500 dark:text-gray-400">媒体ID：</span><span className="dark:text-gray-200">{urlValidationResult.mediaId}</span></div>
                        {urlValidationResult.title && (
                          <div className="col-span-2"><span className="text-gray-500 dark:text-gray-400">标题：</span><span className="dark:text-gray-200">{urlValidationResult.title}</span></div>
                        )}
                        {urlValidationResult.episodeIndex && (
                          <div><span className="text-gray-500 dark:text-gray-400">集数：</span><span className="dark:text-gray-200">第 {urlValidationResult.episodeIndex} 集</span></div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ExclamationCircleOutlined className="text-red-500" />
                      <span className="text-red-700 dark:text-red-400 text-sm">{urlValidationResult.errorMessage || 'URL解析失败'}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <Form.Item
            name="title"
            label="分集标题"
            rules={[{ required: true, message: '请输入分集标题' }]}
          >
            <Input placeholder="请输入分集标题" />
          </Form.Item>
          <Form.Item
            name="episodeIndex"
            label="集数"
            rules={[{ required: true, message: '请输入集数' }]}
          >
            <InputNumber
              style={{ width: '100%' }}
              placeholder="请输入分集集数"
              min={1}
            />
          </Form.Item>

          {/* 自定义源 XML 导入模式 */}
          {isXmlImport && !isEditing && manualImportMode === 'xml' && (
            <>
              <Form.Item
                name="content"
                label="弹幕XML内容"
                rules={[
                  {
                    required: true,
                    message: `请输入弹幕XML内容`,
                  },
                ]}
              >
                <Input.TextArea
                  rows={6}
                  placeholder="请在此处粘贴弹幕XML文件的内容"
                />
              </Form.Item>
              <div className="text-right my-4">
                <Upload
                  {...uploadProps}
                  ref={uploadRef}
                  loading={uploading}
                  disabled={uploading}
                >
                  <Button type="primary" icon={<UploadOutlined />}>
                    选择文件导入XML
                  </Button>
                </Upload>
              </div>
            </>
          )}

          {/* 非自定义源编辑模式下显示普通的官方链接输入框 */}
          {!isXmlImport && isEditing && (
            <Form.Item
              name="sourceUrl"
              label="官方链接"
              rules={[
                {
                  required: true,
                  message: `请输入官方链接`,
                },
              ]}
            >
              <Input placeholder="请输入官方链接" />
            </Form.Item>
          )}

          {isEditing && (
            <Form.Item
              name="danmakuFilePath"
              label="弹幕文件路径"
              tooltip="弹幕XML文件的存储路径，修改后会更新数据库记录（不会移动实际文件）"
            >
              <Input placeholder="例如: /app/config/danmaku/123/456.xml" />
            </Form.Item>
          )}
          <Form.Item name="episodeId" hidden>
            <Input />
          </Form.Item>
          <Form.Item name="originalEpisodeIndex" hidden>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`正片重整预览 - ${animeDetail.title}`}
        open={resetOpen}
        onOk={handleResetMainEpisode}
        confirmLoading={resetLoading}
        cancelText="取消"
        okText="确认执行"
        onCancel={() => setResetOpen(false)}
        zIndex={100}
      >
        <div>
          <Typography.Text className="mb-2">将基于平均弹幕数进行正片重整：</Typography.Text>
          <ul>
            <li>
              <Typography.Text>
                平均弹幕数：<strong>{resetInfo?.average?.toFixed(2)}</strong>
              </Typography.Text>
            </li>
            <li>
              <Typography.Text>
                预计删除分集：
                <span className="text-red-400 font-bold">
                  {resetInfo?.toDelete?.length}
                </span>{' '}
                / {episodeList.length}
              </Typography.Text>
            </li>
            <li>
              <Typography.Text>
                预计保留分集：
                <span className="text-green-500 font-bold">
                  {resetInfo?.toKeep?.length}
                </span>{' '}
                / {episodeList.length}
              </Typography.Text>
            </li>
          </ul>
        </div>
        <div className="my-4 text-sm font-semibold">
          <Typography.Text>预览将保留的分集（最多显示 80 条）</Typography.Text>
        </div>
        <Table
          pagination={false}
          size="small"
          dataSource={resetInfo?.toKeep?.slice(0, 80) ?? []}
          columns={keepColumns}
          rowKey={'episodeId'}
          scroll={{ x: '100%' }}
        />
      </Modal>
      {/* 批量编辑弹窗 */}
      <Modal
        title="批量编辑分集"
        open={isBatchEditModalOpen}
        onCancel={() => setIsBatchEditModalOpen(false)}
        onOk={handleBatchEditSubmit}
        confirmLoading={batchEditLoading}
        width={800}
        okText="确认提交"
        cancelText="取消"
      >
        {/* 批量调整集数 */}
        <div className="mb-4 p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
          <div className="font-medium mb-2">🔢 批量调整集数</div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={batchIndexMode}
              onChange={setBatchIndexMode}
              style={{ width: 120 }}
              options={[
                { value: 'none', label: '不修改' },
                { value: 'offset', label: '偏移' },
                { value: 'reorder', label: '按顺序重排' },
              ]}
            />
            {batchIndexMode === 'offset' && (
              <>
                <InputNumber
                  value={batchOffsetValue}
                  onChange={setBatchOffsetValue}
                  placeholder="偏移量"
                  className="w-28"
                />
                <span className="text-gray-500 dark:text-gray-400 text-sm">正数增加，负数减少</span>
              </>
            )}
            {batchIndexMode === 'reorder' && (
              <>
                <span className="text-gray-500 dark:text-gray-400 text-sm">从第</span>
                <InputNumber
                  value={batchReorderStart}
                  onChange={setBatchReorderStart}
                  min={1}
                  className="w-20"
                />
                <span className="text-gray-500 dark:text-gray-400 text-sm">集开始</span>
              </>
            )}
            <Button
              onClick={batchIndexMode === 'offset' ? handleApplyBatchOffset : handleApplyBatchReorder}
              disabled={batchIndexMode === 'none' || (batchIndexMode === 'offset' && !batchOffsetValue)}
            >
              应用
            </Button>
          </div>
        </div>

        {/* 批量命名规则 - ReNamer风格 */}
        <div className="mb-4 p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
          <div className="font-medium mb-2">📝 批量命名规则</div>
          {/* 添加规则区域 */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <span className="text-gray-500 dark:text-gray-400 text-sm">添加规则:</span>
            <Select
              value={selectedRuleType}
              onChange={(v) => { setSelectedRuleType(v); setRuleParams({}) }}
              style={{ width: 100 }}
              options={ruleTypeOptions}
            />
            {/* 替换规则参数 */}
            {selectedRuleType === 'replace' && (
              <>
                <Input value={ruleParams.search || ''} onChange={(e) => setRuleParams(p => ({ ...p, search: e.target.value }))} placeholder="查找" style={{ width: 120 }} />
                <span>→</span>
                <Input value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder="替换为" style={{ width: 120 }} />
              </>
            )}
            {/* 正则规则参数 */}
            {selectedRuleType === 'regex' && (
              <>
                <Input value={ruleParams.pattern || ''} onChange={(e) => setRuleParams(p => ({ ...p, pattern: e.target.value }))} placeholder="正则表达式" style={{ width: 150 }} />
                <span>→</span>
                <Input value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder="替换为" style={{ width: 120 }} />
              </>
            )}
            {/* 插入规则参数 */}
            {selectedRuleType === 'insert' && (
              <>
                <Input value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder="插入文本" style={{ width: 120 }} />
                <Select
                  value={ruleParams.position || 'start'}
                  onChange={(v) => setRuleParams(p => ({ ...p, position: v }))}
                  style={{ width: 100 }}
                  options={[
                    { value: 'start', label: '开头' },
                    { value: 'end', label: '结尾' },
                    { value: 'index', label: '指定位置' }
                  ]}
                />
                {ruleParams.position === 'index' && (
                  <InputNumber
                    value={ruleParams.index || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, index: v }))}
                    min={0}
                    placeholder="位置"
                    style={{ width: 80 }}
                    addonAfter="位"
                  />
                )}
              </>
            )}
            {/* 删除规则参数 */}
            {selectedRuleType === 'delete' && (
              <>
                <Select
                  value={ruleParams.mode || 'text'}
                  onChange={(v) => setRuleParams(p => ({ ...p, mode: v }))}
                  style={{ width: 140 }}
                  options={[
                    { value: 'text', label: '删除文本' },
                    { value: 'first', label: '删除前N个字符' },
                    { value: 'last', label: '删除后N个字符' },
                    { value: 'toText', label: '从开头删到文本' },
                    { value: 'fromText', label: '从文本删到结尾' },
                    { value: 'range', label: '删除范围' },
                  ]}
                />
                {/* 删除指定文本 */}
                {(ruleParams.mode === 'text' || !ruleParams.mode) && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder="要删除的文本"
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      区分大小写
                    </label>
                  </>
                )}
                {/* 删除前N个字符 */}
                {ruleParams.mode === 'first' && (
                  <InputNumber
                    value={ruleParams.count || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                    min={0}
                    placeholder="字符数"
                    style={{ width: 100 }}
                  />
                )}
                {/* 删除后N个字符 */}
                {ruleParams.mode === 'last' && (
                  <InputNumber
                    value={ruleParams.count || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                    min={0}
                    placeholder="字符数"
                    style={{ width: 100 }}
                  />
                )}
                {/* 从开头删到文本 */}
                {ruleParams.mode === 'toText' && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder="删除到此文本"
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      区分大小写
                    </label>
                  </>
                )}
                {/* 从文本删到结尾 */}
                {ruleParams.mode === 'fromText' && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder="从此文本删除"
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      区分大小写
                    </label>
                  </>
                )}
                {/* 删除范围 */}
                {ruleParams.mode === 'range' && (
                  <>
                    <span className="text-sm">从位置</span>
                    <InputNumber
                      value={ruleParams.from || 0}
                      onChange={(v) => setRuleParams(p => ({ ...p, from: v }))}
                      min={0}
                      placeholder="起始位置"
                      style={{ width: 90 }}
                    />
                    <span className="text-sm">删除</span>
                    <InputNumber
                      value={ruleParams.count || 0}
                      onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                      min={0}
                      placeholder="字符数"
                      style={{ width: 80 }}
                    />
                    <span className="text-sm">个字符</span>
                  </>
                )}
              </>
            )}
            {/* 序列化规则参数 */}
            {selectedRuleType === 'serialize' && (
              <div className="w-full flex flex-col gap-2 p-2 bg-gray-100 dark:bg-gray-700 rounded">
                {/* 第一行：格式结构 */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-gray-500">格式结构:</span>
                  <Input
                    value={ruleParams.prefix || ''}
                    onChange={(e) => setRuleParams(p => ({ ...p, prefix: e.target.value }))}
                    placeholder="第"
                    style={{ width: 120 }}
                    addonBefore="前缀"
                    size="small"
                  />
                  <span className="text-xs text-gray-400">+</span>
                  <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 rounded text-xs font-mono">
                    序号
                  </span>
                  <span className="text-xs text-gray-400">+</span>
                  <Input
                    value={ruleParams.suffix || ''}
                    onChange={(e) => setRuleParams(p => ({ ...p, suffix: e.target.value }))}
                    placeholder="集"
                    style={{ width: 120 }}
                    addonBefore="后缀"
                    size="small"
                  />
                </div>
                {/* 第二行：序号参数 */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-gray-500">序号设置:</span>
                  <InputNumber
                    value={ruleParams.start || 1}
                    onChange={(v) => setRuleParams(p => ({ ...p, start: v }))}
                    min={0}
                    placeholder="起始"
                    style={{ width: 130 }}
                    addonBefore="起始值"
                    size="small"
                  />
                  <InputNumber
                    value={ruleParams.digits || 2}
                    onChange={(v) => setRuleParams(p => ({ ...p, digits: v }))}
                    min={1}
                    max={5}
                    placeholder="位数"
                    style={{ width: 130 }}
                    addonBefore="补零位数"
                    size="small"
                  />
                  <Select
                    value={ruleParams.position || 'replace'}
                    onChange={(v) => setRuleParams(p => ({ ...p, position: v }))}
                    style={{ width: 100 }}
                    size="small"
                    options={[
                      { value: 'start', label: '添加到开头' },
                      { value: 'end', label: '添加到结尾' },
                      { value: 'replace', label: '替换标题' }
                    ]}
                  />
                </div>
                {/* 第三行：效果预览 */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500 dark:text-gray-400">效果预览:</span>
                  <span className="text-sm font-mono text-blue-600 dark:text-blue-400 font-semibold">
                    {
                      ruleParams.position === 'start'
                        ? `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}原标题`
                        : ruleParams.position === 'end'
                        ? `原标题${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                        : `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                    }
                  </span>
                </div>
              </div>
            )}
            {/* 大小写规则参数 */}
            {selectedRuleType === 'case' && (
              <Select value={ruleParams.mode || 'upper'} onChange={(v) => setRuleParams(p => ({ ...p, mode: v }))} style={{ width: 120 }} options={[{ value: 'upper', label: '全大写' }, { value: 'lower', label: '全小写' }, { value: 'title', label: '首字母大写' }]} />
            )}
            {/* 清理规则参数 */}
            {selectedRuleType === 'strip' && (
              <>
                <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={ruleParams.trimSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimSpaces: e.target.checked }))} />首尾空格</label>
                <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={ruleParams.trimDuplicateSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimDuplicateSpaces: e.target.checked }))} />重复空格</label>
                <Input value={ruleParams.chars || ''} onChange={(e) => setRuleParams(p => ({ ...p, chars: e.target.value }))} placeholder="删除字符" style={{ width: 100 }} />
              </>
            )}
            <Button type="primary" onClick={handleAddRule}>+ 添加</Button>
          </div>
          {/* 已添加的规则列表 */}
          {renameRules.length > 0 && (
            <div className="border border-gray-200 dark:border-gray-600 rounded p-2 mb-3 max-h-32 overflow-auto" style={{ backgroundColor: 'var(--color-card)' }}>
              {renameRules.map((rule, idx) => (
                <div key={rule.id} className="flex items-center gap-2 py-1 border-b border-gray-200 dark:border-gray-600 last:border-b-0">
                  <input type="checkbox" checked={rule.enabled} onChange={() => handleToggleRule(rule.id)} />
                  <span className="text-gray-500 dark:text-gray-400 text-xs">{idx + 1}.</span>
                  <Tag color={rule.enabled ? 'blue' : 'default'}>{ruleTypeOptions.find(r => r.value === rule.type)?.label}</Tag>
                  <span className="text-sm flex-1 truncate">
                    {rule.type === 'replace' && `"${rule.params.search}" → "${rule.params.replace || ''}"`}
                    {rule.type === 'regex' && `/${rule.params.pattern}/ → "${rule.params.replace || ''}"`}
                    {rule.type === 'insert' && `"${rule.params.text}" (${rule.params.position === 'start' ? '开头' : '结尾'})`}
                    {rule.type === 'delete' && (() => {
                      const mode = rule.params.mode || 'text'
                      switch (mode) {
                        case 'text':
                          return `删除文本 "${rule.params.text}"`
                        case 'first':
                          return `删除前 ${rule.params.count || 0} 个字符`
                        case 'last':
                          return `删除后 ${rule.params.count || 0} 个字符`
                        case 'toText':
                          return `从开头删到 "${rule.params.text}"`
                        case 'fromText':
                          return `从 "${rule.params.text}" 删到结尾`
                        case 'range':
                          return `从位置 ${rule.params.from || 0} 删除 ${rule.params.count || 0} 个字符`
                        default:
                          return '删除'
                      }
                    })()}
                    {rule.type === 'serialize' && `${rule.params.prefix || ''}{${String(rule.params.start || 1).padStart(rule.params.digits || 2, '0')}}${rule.params.suffix || ''}`}
                    {rule.type === 'case' && (rule.params.mode === 'upper' ? '全大写' : rule.params.mode === 'lower' ? '全小写' : '首字母大写')}
                    {rule.type === 'strip' && '清理空格/字符'}
                  </span>
                  <Button type="text" danger size="small" onClick={() => handleDeleteRule(rule.id)}>🗑</Button>
                </div>
              ))}
            </div>
          )}
          {/* 预览和应用按钮 */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-sm">👁 预览效果</span>
              <Switch
                checked={isPreviewMode}
                onChange={(checked) => {
                  if (checked) handlePreviewRules()
                  else { setIsPreviewMode(false); setPreviewData({}) }
                }}
                disabled={renameRules.length === 0}
              />
            </div>
            <Button type="primary" onClick={handleApplyBatchRename} disabled={renameRules.length === 0}>✅ 应用规则</Button>
          </div>
        </div>

        {/* 可拖拽编辑表格 */}
        <div className="border border-gray-200 dark:border-gray-600 rounded overflow-auto" style={{ maxHeight: 400, backgroundColor: 'var(--color-card)' }}>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={batchEditData.map(item => item.episodeId)} strategy={verticalListSortingStrategy}>
              <table className="w-full text-sm text-gray-900 dark:text-gray-100">
                <thead className="bg-gray-100 dark:bg-gray-700 sticky top-0 z-10">
                  <tr>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-10">拖拽</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-32">ID</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600">剧集名</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-24">集数</th>
                  </tr>
                </thead>
                <tbody>
                  {batchEditData.map((item, index) => (
                    <SortableRow key={item.episodeId} id={item.episodeId} data={item} index={index} />
                  ))}
                </tbody>
              </table>
            </SortableContext>
          </DndContext>
        </div>
        <div className="mt-2 text-gray-500 dark:text-gray-400 text-sm">
          💡 拖拽行可调整顺序，点击"确认提交"后才会保存更改
        </div>
      </Modal>
      <BatchImportModal
        open={isBatchModalOpen}
        sourceInfo={sourceInfo}
        onCancel={() => setIsBatchModalOpen(false)}
        onSuccess={handleBatchImportSuccess}
      />
      <DanmakuEditModal
        open={isDanmakuEditModalOpen}
        onCancel={() => setIsDanmakuEditModalOpen(false)}
        onSuccess={() => {
          setIsDanmakuEditModalOpen(false)
          getDetail()
        }}
        episodes={episodeList}
        sourceInfo={sourceInfo}
      />
    </div>
  )
}

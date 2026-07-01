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
  importCollection,
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
import { Select, Segmented, Radio } from 'antd'
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
import { useTranslation } from 'react-i18next'

export const EpisodeDetail = () => {
  const { t } = useTranslation()
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
  // 合集导入模式: 'single'(仅此视频) | 'collection'(整个合集)，仅当解析结果含合集时生效
  const [collectionImportMode, setCollectionImportMode] = useState('single')

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
        messageApi.error(t('episodePage.invalidAnimeId'))
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
      messageApi.error(t('episodePage.fetchDetailFailed'))
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
      title: t('episodePage.colEpisodeName'),
      dataIndex: 'title',
      key: 'title',
      width: 200,
    },
    {
      title: t('episodePage.colEpisodeIndex'),
      dataIndex: 'episodeIndex',
      key: 'episodeIndex',
      width: 80,
      sorter: {
        compare: (a, b) => a.episodeIndex - b.episodeIndex,
        multiple: 1,
      },
    },
    {
      title: t('episodePage.colCommentCount'),
      dataIndex: 'commentCount',
      key: 'commentCount',
      width: 80,
    },

    {
      title: t('episodePage.colFetchedAt'),
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
      title: t('episodePage.colOfficialLink'),
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
                {t('episodePage.btnJump')}
              </a>
            ) : (
              '--'
            )}
          </div>
        )
      },
    },
    {
      title: t('episodePage.colAction'),
      width: isXmlImport ? 90 : 120,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title={t('episodePage.tipEditEpisode')}>
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
              <Tooltip title={t('episodePage.tipRefreshDanmaku')}>
                <span
                  className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                  onClick={() => handleRefresh(record)}
                >
                  <MyIcon icon="refresh" size={20} />
                </span>
              </Tooltip>
            )}

            <Tooltip title={t('episodePage.tipDanmakuDetail')}>
              <span
                className="cursor-pointer hover:text-primary text-gray-600 dark:text-gray-400"
                onClick={() => {
                  navigate(`/comment/${record.episodeId}?episodeId=${id}`)
                }}
              >
                <MyIcon icon="comment" size={20} />
              </span>
            </Tooltip>
            <Tooltip title={t('episodePage.tipDelete')}>
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
    { value: 'replace', label: t('episodePage.ruleReplace') },
    { value: 'regex', label: t('episodePage.ruleRegex') },
    { value: 'insert', label: t('episodePage.ruleInsert') },
    { value: 'delete', label: t('episodePage.ruleDelete') },
    { value: 'serialize', label: t('episodePage.ruleSerialize') },
    { value: 'case', label: t('episodePage.ruleCase') },
    { value: 'strip', label: t('episodePage.ruleStrip') },
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
      messageApi.error(t('episodePage.ruleExecError', { rule: ruleTypeOptions.find(r => r.value === rule.type)?.label, error: e.message }))
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
      messageApi.warning(t('episodePage.enterSearchText'))
      return
    }
    if (selectedRuleType === 'regex' && !ruleParams.pattern) {
      messageApi.warning(t('episodePage.enterRegex'))
      return
    }
    if (selectedRuleType === 'insert') {
      if (!ruleParams.text) {
        messageApi.warning(t('episodePage.enterInsertText'))
        return
      }
      if (ruleParams.position === 'index' && ruleParams.index === undefined) {
        messageApi.warning(t('episodePage.enterInsertPos'))
        return
      }
    }
    if (selectedRuleType === 'delete') {
      const mode = ruleParams.mode || 'text'
      if ((mode === 'text' || mode === 'toText' || mode === 'fromText') && !ruleParams.text) {
        messageApi.warning(t('episodePage.enterText'))
        return
      }
      if ((mode === 'first' || mode === 'last' || mode === 'range') && !ruleParams.count) {
        messageApi.warning(t('episodePage.enterCharCount'))
        return
      }
      if (mode === 'range' && ruleParams.from === undefined) {
        messageApi.warning(t('episodePage.enterStartPos'))
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
    messageApi.success(t('episodePage.ruleAdded'))
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
      messageApi.warning(t('episodePage.addRuleFirst'))
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
      messageApi.warning(t('episodePage.addRuleFirst'))
      return
    }
    setBatchEditData(prev => prev.map((item, index) => ({
      ...item,
      title: applyAllRules(item.title, index)
    })))
    setIsPreviewMode(false)
    setPreviewData({})
    messageApi.success(t('episodePage.ruleApplied'))
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
      messageApi.success(t('episodePage.batchEditSuccess'))
      setIsBatchEditModalOpen(false)
      getDetail()
    } catch (error) {
      messageApi.error(t('episodePage.batchEditFailed', { error: error.message }))
    } finally {
      setBatchEditLoading(false)
    }
  }

  const keepColumns = [
    {
      title: t('episodePage.colEpisodeIndex'),
      dataIndex: 'episodeIndex',
      key: 'episodeIndex',
      width: 60,
    },
    {
      title: t('episodePage.colTitle'),
      dataIndex: 'title',
      key: 'title',
      width: 200,
    },
    {
      title: t('episodePage.colCommentCount'),
      dataIndex: 'commentCount',
      key: 'commentCount',
      width: 60,
    },
  ]

  const handleBatchDelete = () => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: t('episodePage.deleteEpisodeTitle'),
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>{t('episodePage.deleteSelectedConfirm', { count: selectedRows.length })}</Typography.Text>
          <br />
          <Typography.Text>{t('episodePage.deleteBatchHint')}</Typography.Text>
          <div className="flex items-center gap-2 mt-3">
            <span>{t('episodePage.deleteAlsoFiles')}</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisode({
            episodeIds: selectedRows?.map(it => it.episodeId),
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('episodePage.deleteBatchSubmitFailed', { error: error.message }))
        }
      },
    })
  }

  const deleteEpisodeSingle = record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: t('episodePage.deleteEpisodeTitle'),
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>{t('episodePage.deleteSingleConfirm', { title: record.title })}</Typography.Text>
          <br />
          <Typography.Text>{t('episodePage.deleteBatchHint')}</Typography.Text>
          <div className="flex items-center gap-2 mt-3">
            <span>{t('episodePage.deleteAlsoFiles')}</span>
            <Switch
              defaultChecked={true}
              onChange={checked => {
                deleteFilesRef.current = checked
              }}
            />
          </div>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisodeSingle({
            id: record.episodeId,
            deleteFiles: deleteFilesRef.current,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('episodePage.deleteSubmitFailed', { error: error.message }))
        }
      },
    })
  }

  const handleRefresh = record => {
    modalApi.confirm({
      title: t('episodePage.refreshEpisodeTitle'),
      zIndex: 1002,
      content: <Typography.Text>{t('episodePage.refreshSingleConfirm', { title: record.title })}</Typography.Text>,
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await refreshEpisodeDanmaku({
            id: record.episodeId,
          })
          messageApi.success(res.message || t('episodePage.refreshStarted'))
        } catch (error) {
          messageApi.error(t('episodePage.refreshStartFailed', { error: error.message }))
        }
      },
    })
  }

  const handleBatchRefresh = () => {
    if (!selectedRows.length) {
      messageApi.warning(t('episodePage.selectRefreshFirst'))
      return
    }

    modalApi.confirm({
      title: t('episodePage.refreshBatchTitle'),
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>{t('episodePage.refreshBatchConfirm', { count: selectedRows.length })}</Typography.Text>
          <br />
          <Typography.Text>{t('episodePage.refreshBatchHint', { count: selectedRows.length })}</Typography.Text>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const episodeIds = selectedRows.map(row => row.episodeId)
          const res = await refreshEpisodesBulk({ episodeIds })
          messageApi.success(res.message || t('episodePage.refreshBatchSubmitted'))
        } catch (error) {
          messageApi.error(t('episodePage.refreshBatchSubmitFailed', { error: error.message }))
        }
      },
    })
  }

  const goTask = res => {
    modalApi.confirm({
      title: t('episodePage.taskTipTitle'),
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>{res.data?.message || t('episodePage.taskSubmitted')}</Typography.Text>
          <br />
          <Typography.Text>{t('episodePage.goTaskManager')}</Typography.Text>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
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
      messageApi.warning(t('episodePage.enterUrl'))
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
              errorMessage: t('episodePage.urlSourceMismatch', { provider: res.data.provider, source: sourceInfo?.providerName })
            })
            return
          }
        }
        setUrlValidationResult(res.data)
        // 每次解析新结果时重置合集选择为「仅此视频」
        setCollectionImportMode('single')
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
        errorMessage: error.detail || error.message || t('episodePage.urlValidateFailed')
      })
    } finally {
      setUrlValidating(false)
    }
  }

  // 清空URL解析状态
  const clearUrlValidation = () => {
    setUrlValidationResult(null)
    setUrlValidating(false)
    setCollectionImportMode('single')
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
          messageApi.warning(t('episodePage.parseUrlFirst'))
          setConfirmLoading(false)
          return
        }
        // 解析结果含合集且用户选择「整个合集」：批量导入合集全部视频为当前源分集
        if (urlValidationResult.collection && collectionImportMode === 'collection') {
          await importCollection({
            sourceId: Number(id),
            url: values.sourceUrl,
            title: urlValidationResult.collection.title,
            startEpisodeIndex: values.episodeIndex,
          })
        } else {
          await manualImportEpisode({
            sourceId: Number(id),
            episodeIndex: values.episodeIndex,
            title: values.title,
            sourceUrl: values.sourceUrl,
            urlProvider: urlValidationResult.provider,  // 传入解析出的真实平台名，后端用于 scraper 调用
          })
        }
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
      messageApi.success(t('episodePage.episodeUpdateSuccess'))
    } catch (error) {
      // 改进错误提示，处理对象类型的错误
      let errorMsg = t('episodePage.updateFailed')
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
      title: t('episodePage.offsetTitle'),
      icon: <VerticalAlignMiddleOutlined />,
      zIndex: 1002,
      content: (
        <div className="mt-4">
          <Typography.Text>{t('episodePage.offsetHint')}</Typography.Text>
          <br />
          <Typography.Text className="text-gray-500 dark:text-gray-400 text-xs">
            {t('episodePage.offsetExample')}
          </Typography.Text>
          <InputNumber
            placeholder={t('episodePage.offsetPlaceholder')}
            onChange={value => (offsetValue = value)}
            style={{ width: '100%' }}
            autoFocus
          />
        </div>
      ),
      onOk: async () => {
        if (!offsetValue || !Number.isInteger(offsetValue)) {
          messageApi.warning(t('episodePage.offsetInvalid'))
          return
        }
        try {
          const res = await offsetEpisodes({
            episodeIds: selectedRows.map(it => it.episodeId),
            offset: offsetValue,
          })
          goTask(res)
        } catch (error) {
          messageApi.error(error?.detail || t('episodePage.offsetSubmitFailed'))
        }
      },
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
    })
  }

  const handleResetEpisode = () => {
    modalApi.confirm({
      title: t('episodePage.reorderTitle'),
      zIndex: 1002,
      content: (
        <div>
          <Typography.Text>
            {t('episodePage.reorderConfirm', { title: animeDetail.title })}
          </Typography.Text>
          <br />
          <Typography.Text>{t('episodePage.reorderHint')}</Typography.Text>
        </div>
      ),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          const res = await resetEpisode({
            sourceId: Number(id),
          })
          goTask(res)
        } catch (error) {
          messageApi.error(t('episodePage.reorderSubmitFailed', { error: error.message }))
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
      messageApi.success(t('episodePage.resetSubmitted'))
    } catch (error) {
      messageApi.error(t('episodePage.resetSubmitFailed', { error: error.message }))
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
          messageApi.error(t('episodePage.fileParseFailed', { name: file.name, error: error.message }))
        }
      }

      reader.readAsText(file)
    } catch (error) {
      messageApi.error(t('episodePage.fileProcessFailed', { error: error.message }))
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
            title: <Link to="/library">{t('episodePage.breadcrumbLibrary')}</Link>,
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
            title: t('episodePage.breadcrumbEpisodeList'),
          },
        ]}
      />
      <Card loading={loading} title={t('episodePage.cardTitle', { title: animeDetail?.title ?? '' })}>
        <div className="mb-3 text-sm text-gray-600 dark:text-gray-400">
          💡 {isMobile ? t('episodePage.selectTipMobile') : t('episodePage.selectTipDesktop')}{t('episodePage.selectTipSuffix')}
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <Button
            onClick={() => {
              handleBatchDelete()
            }}
            type="primary"
            disabled={!selectedRows.length}
          >
            {t('episodePage.btnDeleteSelected')}
          </Button>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <Button
              onClick={() => openBatchEditModal(selectedRows)}
              disabled={!selectedRows.length}
            >
              <Tooltip title={t('episodePage.tipBatchEdit')}>
                <EditOutlined />
                <span className="ml-1">{t('episodePage.btnBatchEdit')}</span>
              </Tooltip>
            </Button>
            <Button
              onClick={handleOffset}
              disabled={!selectedRows.length}
            >
              <Tooltip title={t('episodePage.tipOffset')}>
                <VerticalAlignMiddleOutlined />
                <span className="ml-1">{t('episodePage.btnOffset')}</span>
              </Tooltip>
            </Button>
            <Button
              onClick={() => {
                const validCounts = episodeList
                  .map(ep => Number(ep.commentCount))
                  .filter(n => Number.isFinite(n) && n >= 0)
                if (validCounts.length === 0) {
                  messageApi.error(t('episodePage.danmakuUnavailable'))
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
                    t('episodePage.noEpisodeBelowAvg', { avg: average.toFixed(2) })
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
              {t('episodePage.btnReorderMain')}
            </Button>
            <Button
              onClick={() => {
                handleResetEpisode()
              }}
              disabled={!episodeList.length}
            >
              {t('episodePage.btnReorder')}
            </Button>
            <Button
              onClick={handleBatchRefresh}
              disabled={!selectedRows.length || isXmlImport}
            >
              <Tooltip title={t('episodePage.tipBatchRefresh')}>
                <MyIcon icon="refresh" size={16} />
                <span className="ml-1">{t('episodePage.btnBatchRefresh')}</span>
              </Tooltip>
            </Button>
            <Button
              onClick={() => setIsDanmakuEditModalOpen(true)}
              disabled={!episodeList.length}
            >
              <Tooltip title={t('episodePage.tipDanmakuEdit')}>
                <EditOutlined />
                <span className="ml-1">{t('episodePage.btnDanmakuEdit')}</span>
              </Tooltip>
            </Button>
            {isXmlImport && (
              <Button
                onClick={() => {
                  setIsBatchModalOpen(true)
                }}
              >
                {t('episodePage.btnBatchImport')}
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
              {t('episodePage.btnManualImport')}
            </Button>
          </div>
        </div>
        <div className="mb-4"></div>
        {!!episodeList?.length ? (
          <ResponsiveTable
            pagination={{
              ...pagination,
              showTotal: total => t('episodePage.totalItems', { total }),
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
            tableProps={{ className: 'library-table', rowClassName: () => '' }}
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
                              {t('episodePage.episodeIndexCard', { index: record.episodeIndex })}
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
                            title={t('episodePage.deleteEpisodeTitle')}
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
                                {t('episodePage.commentCountCard', { count: record.commentCount || 0 })}
                              </span>
                            </span>
                          </div>
                          {record.sourceUrl && isUrl(record.sourceUrl) && (
                            <div className="flex items-center gap-1">
                              <span className="text-xs text-gray-500 dark:text-gray-400">{t('episodePage.sourceLabel')}</span>
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
                            {t('episodePage.fetchedAtCard', { time: dayjs(record.fetchedAt).format('YYYY-MM-DD HH:mm') })}
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
                          title={t('episodePage.tipEditEpisode')}
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
                          {t('episodePage.btnEdit')}
                        </Button>
                        {!isXmlImport && (
                          <Button
                            size="small"
                            type="text"
                            icon={<MyIcon icon="refresh" size={14} />}
                            title={t('episodePage.tipRefreshDanmaku')}
                            onClick={(e) => {
                              e.stopPropagation()
                              handleRefresh(record)
                            }}
                          >
                            {t('episodePage.btnRefresh')}
                          </Button>
                        )}
                        <Button
                          size="small"
                          type="text"
                          icon={<MyIcon icon="comment" size={14} />}
                          title={t('episodePage.tipDanmakuDetail')}
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/comment/${record.episodeId}?episodeId=${id}`)
                          }}
                        >
                          {t('episodePage.btnDanmaku')}
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
        title={isEditing ? t('episodePage.modalEditTitle') : t('episodePage.modalImportTitle')}
        open={editOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText={t('common.cancel')}
        okText={t('common.confirm')}
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
                { label: <span><UploadOutlined className="mr-1" />{t('episodePage.importXml')}</span>, value: 'xml' },
                { label: <span><LinkOutlined className="mr-1" />{t('episodePage.importUrl')}</span>, value: 'url' },
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
                {t('episodePage.urlImportDesc')}
              </div>
              <Form.Item
                name="sourceUrl"
                label={t('episodePage.labelVideoUrl')}
                rules={[
                  {
                    required: true,
                    message: t('episodePage.ruleVideoUrl'),
                  },
                ]}
                className="mb-2"
              >
                <Input.Search
                  placeholder={t('episodePage.placeholderVideoUrl')}
                  onSearch={handleValidateUrl}
                  onChange={() => setUrlValidationResult(null)}
                  enterButton={
                    <Button loading={urlValidating}>
                      {t('episodePage.btnParseUrl')}
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
                        <span className="font-medium text-green-700 dark:text-green-400 text-sm">{t('episodePage.urlParseSuccess')}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-xs">
                        <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldPlatform')}</span><span className="dark:text-gray-200">{urlValidationResult.provider}</span></div>
                        <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldMediaId')}</span><span className="dark:text-gray-200">{urlValidationResult.mediaId}</span></div>
                        {urlValidationResult.title && (
                          <div className="col-span-2"><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldTitle')}</span><span className="dark:text-gray-200">{urlValidationResult.title}</span></div>
                        )}
                        {urlValidationResult.mediaType && (
                          <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldType')}</span><span className="dark:text-gray-200">{urlValidationResult.mediaType === 'movie' ? t('episodePage.typeMovie') : t('episodePage.typeSeries')}</span></div>
                        )}
                        {urlValidationResult.episodeIndex && (
                          <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldEpisode')}</span><span className="dark:text-gray-200">{t('episodePage.episodeNo', { index: urlValidationResult.episodeIndex })}</span></div>
                        )}
                      </div>
                      {urlValidationResult.imageUrl && (
                        <div className="mt-2">
                          <img src={urlValidationResult.imageUrl} alt={t('episodePage.coverAlt')} className="h-20 rounded" />
                        </div>
                      )}
                      {/* 检测到合集：让用户选择仅导入此视频或整个合集 */}
                      {urlValidationResult.collection && (
                        <div className="mt-3 pt-3 border-t border-green-200 dark:border-green-700">
                          <div className="text-xs text-gray-500 dark:text-gray-400 mb-1.5">
                            {t('episodePage.collectionDetected')}
                          </div>
                          <Radio.Group
                            value={collectionImportMode}
                            onChange={e => setCollectionImportMode(e.target.value)}
                          >
                            <Space direction="vertical" size={4}>
                              <Radio value="single">{t('episodePage.collectionSingleOnly')}</Radio>
                              <Radio value="collection">
                                {t('episodePage.collectionImportAll', {
                                  title: urlValidationResult.collection.title || '',
                                  total: urlValidationResult.collection.total || '?',
                                })}
                              </Radio>
                            </Space>
                          </Radio.Group>
                          {collectionImportMode === 'collection' && (
                            <div className="text-[11px] text-orange-500 dark:text-orange-400 mt-1.5">
                              {t('episodePage.collectionImportTip')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ExclamationCircleOutlined className="text-red-500" />
                      <span className="text-red-700 dark:text-red-400 text-sm">{urlValidationResult.errorMessage || t('episodePage.urlParseFailed')}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {!isXmlImport && !isEditing && (
            <div className="mb-4 p-3 rounded-lg" style={{ backgroundColor: 'var(--color-hover)' }}>
              <div className="text-gray-500 dark:text-gray-400 text-sm mb-2">
                <LinkOutlined className="mr-1" />
                {t('episodePage.urlImportDescSource', { source: sourceInfo?.providerName })}
              </div>
              <Form.Item
                name="sourceUrl"
                label={t('episodePage.labelOfficialLink')}
                rules={[
                  {
                    required: true,
                    message: t('episodePage.ruleOfficialLink'),
                  },
                ]}
                className="mb-2"
              >
                <Input.Search
                  placeholder={t('episodePage.placeholderSourceUrl', { source: sourceInfo?.providerName })}
                  onSearch={handleValidateUrl}
                  onChange={() => setUrlValidationResult(null)}
                  enterButton={
                    <Button loading={urlValidating}>
                      {t('episodePage.btnParseUrl')}
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
                        <span className="font-medium text-green-700 dark:text-green-400 text-sm">{t('episodePage.urlParseSuccess')}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-xs">
                        <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldPlatform')}</span><span className="dark:text-gray-200">{urlValidationResult.provider}</span></div>
                        <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldMediaId')}</span><span className="dark:text-gray-200">{urlValidationResult.mediaId}</span></div>
                        {urlValidationResult.title && (
                          <div className="col-span-2"><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldTitle')}</span><span className="dark:text-gray-200">{urlValidationResult.title}</span></div>
                        )}
                        {urlValidationResult.episodeIndex && (
                          <div><span className="text-gray-500 dark:text-gray-400">{t('episodePage.fieldEpisode')}</span><span className="dark:text-gray-200">{t('episodePage.episodeNo', { index: urlValidationResult.episodeIndex })}</span></div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ExclamationCircleOutlined className="text-red-500" />
                      <span className="text-red-700 dark:text-red-400 text-sm">{urlValidationResult.errorMessage || t('episodePage.urlParseFailed')}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <Form.Item
            name="title"
            label={t('episodePage.labelEpisodeTitle')}
            rules={[{ required: true, message: t('episodePage.ruleEpisodeTitle') }]}
          >
            <Input placeholder={t('episodePage.placeholderEpisodeTitle')} />
          </Form.Item>
          <Form.Item
            name="episodeIndex"
            label={t('episodePage.labelEpisodeIndex')}
            rules={[{ required: true, message: t('episodePage.ruleEpisodeIndex') }]}
          >
            <InputNumber
              style={{ width: '100%' }}
              placeholder={t('episodePage.placeholderEpisodeIndex')}
              min={1}
            />
          </Form.Item>

          {/* 自定义源 XML 导入模式 */}
          {isXmlImport && !isEditing && manualImportMode === 'xml' && (
            <>
              <Form.Item
                name="content"
                label={t('episodePage.labelXmlContent')}
                rules={[
                  {
                    required: true,
                    message: t('episodePage.ruleXmlContent'),
                  },
                ]}
              >
                <Input.TextArea
                  rows={6}
                  placeholder={t('episodePage.placeholderXmlContent')}
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
                    {t('episodePage.btnSelectXmlFile')}
                  </Button>
                </Upload>
              </div>
            </>
          )}

          {/* 非自定义源编辑模式下显示普通的官方链接输入框 */}
          {!isXmlImport && isEditing && (
            <Form.Item
              name="sourceUrl"
              label={t('episodePage.labelOfficialLink')}
              rules={[
                {
                  required: true,
                  message: t('episodePage.ruleOfficialLink'),
                },
              ]}
            >
              <Input placeholder={t('episodePage.placeholderOfficialLink')} />
            </Form.Item>
          )}

          {isEditing && (
            <Form.Item
              name="danmakuFilePath"
              label={t('episodePage.labelDanmakuPath')}
              tooltip={t('episodePage.tooltipDanmakuPath')}
            >
              <Input placeholder={t('episodePage.placeholderDanmakuPath')} />
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
        title={t('episodePage.resetPreviewTitle', { title: animeDetail.title })}
        open={resetOpen}
        onOk={handleResetMainEpisode}
        confirmLoading={resetLoading}
        cancelText={t('common.cancel')}
        okText={t('episodePage.btnConfirmExec')}
        onCancel={() => setResetOpen(false)}
        zIndex={100}
      >
        <div>
          <Typography.Text className="mb-2">{t('episodePage.resetPreviewDesc')}</Typography.Text>
          <ul>
            <li>
              <Typography.Text>
                {t('episodePage.avgCommentCount')}<strong>{resetInfo?.average?.toFixed(2)}</strong>
              </Typography.Text>
            </li>
            <li>
              <Typography.Text>
                {t('episodePage.estimateDelete')}
                <span className="text-red-400 font-bold">
                  {resetInfo?.toDelete?.length}
                </span>{' '}
                / {episodeList.length}
              </Typography.Text>
            </li>
            <li>
              <Typography.Text>
                {t('episodePage.estimateKeep')}
                <span className="text-green-500 font-bold">
                  {resetInfo?.toKeep?.length}
                </span>{' '}
                / {episodeList.length}
              </Typography.Text>
            </li>
          </ul>
        </div>
        <div className="my-4 text-sm font-semibold">
          <Typography.Text>{t('episodePage.previewKeepHint')}</Typography.Text>
        </div>
        <Table
          className="library-table"
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
        title={t('episodePage.batchEditTitle')}
        open={isBatchEditModalOpen}
        onCancel={() => setIsBatchEditModalOpen(false)}
        onOk={handleBatchEditSubmit}
        confirmLoading={batchEditLoading}
        width={800}
        okText={t('episodePage.btnConfirmSubmit')}
        cancelText={t('common.cancel')}
      >
        {/* 批量调整集数 */}
        <div className="mb-4 p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
          <div className="font-medium mb-2">{t('episodePage.sectionAdjustIndex')}</div>
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={batchIndexMode}
              onChange={setBatchIndexMode}
              style={{ width: 120 }}
              options={[
                { value: 'none', label: t('episodePage.indexModeNone') },
                { value: 'offset', label: t('episodePage.indexModeOffset') },
                { value: 'reorder', label: t('episodePage.indexModeReorder') },
              ]}
            />
            {batchIndexMode === 'offset' && (
              <>
                <InputNumber
                  value={batchOffsetValue}
                  onChange={setBatchOffsetValue}
                  placeholder={t('episodePage.placeholderOffset')}
                  className="w-28"
                />
                <span className="text-gray-500 dark:text-gray-400 text-sm">{t('episodePage.offsetHintPosNeg')}</span>
              </>
            )}
            {batchIndexMode === 'reorder' && (
              <>
                <span className="text-gray-500 dark:text-gray-400 text-sm">{t('episodePage.reorderFrom')}</span>
                <InputNumber
                  value={batchReorderStart}
                  onChange={setBatchReorderStart}
                  min={1}
                  className="w-20"
                />
                <span className="text-gray-500 dark:text-gray-400 text-sm">{t('episodePage.reorderStart')}</span>
              </>
            )}
            <Button
              onClick={batchIndexMode === 'offset' ? handleApplyBatchOffset : handleApplyBatchReorder}
              disabled={batchIndexMode === 'none' || (batchIndexMode === 'offset' && !batchOffsetValue)}
            >
              {t('episodePage.btnApply')}
            </Button>
          </div>
        </div>

        {/* 批量命名规则 - ReNamer风格 */}
        <div className="mb-4 p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
          <div className="font-medium mb-2">{t('episodePage.sectionRenameRules')}</div>
          {/* 添加规则区域 */}
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <span className="text-gray-500 dark:text-gray-400 text-sm">{t('episodePage.addRuleLabel')}</span>
            <Select
              value={selectedRuleType}
              onChange={(v) => { setSelectedRuleType(v); setRuleParams({}) }}
              style={{ width: 100 }}
              options={ruleTypeOptions}
            />
            {/* 替换规则参数 */}
            {selectedRuleType === 'replace' && (
              <>
                <Input value={ruleParams.search || ''} onChange={(e) => setRuleParams(p => ({ ...p, search: e.target.value }))} placeholder={t('episodePage.placeholderSearch')} style={{ width: 120 }} />
                <span>→</span>
                <Input value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder={t('episodePage.placeholderReplaceWith')} style={{ width: 120 }} />
              </>
            )}
            {/* 正则规则参数 */}
            {selectedRuleType === 'regex' && (
              <>
                <Input value={ruleParams.pattern || ''} onChange={(e) => setRuleParams(p => ({ ...p, pattern: e.target.value }))} placeholder={t('episodePage.placeholderRegex')} style={{ width: 150 }} />
                <span>→</span>
                <Input value={ruleParams.replace || ''} onChange={(e) => setRuleParams(p => ({ ...p, replace: e.target.value }))} placeholder={t('episodePage.placeholderReplaceWith')} style={{ width: 120 }} />
              </>
            )}
            {/* 插入规则参数 */}
            {selectedRuleType === 'insert' && (
              <>
                <Input value={ruleParams.text || ''} onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))} placeholder={t('episodePage.placeholderInsertText')} style={{ width: 120 }} />
                <Select
                  value={ruleParams.position || 'start'}
                  onChange={(v) => setRuleParams(p => ({ ...p, position: v }))}
                  style={{ width: 100 }}
                  options={[
                    { value: 'start', label: t('episodePage.posStart') },
                    { value: 'end', label: t('episodePage.posEnd') },
                    { value: 'index', label: t('episodePage.posIndex') }
                  ]}
                />
                {ruleParams.position === 'index' && (
                  <InputNumber
                    value={ruleParams.index || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, index: v }))}
                    min={0}
                    placeholder={t('episodePage.placeholderPosition')}
                    style={{ width: 80 }}
                    addonAfter={t('episodePage.addonChar')}
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
                    { value: 'text', label: t('episodePage.delText') },
                    { value: 'first', label: t('episodePage.delFirstN') },
                    { value: 'last', label: t('episodePage.delLastN') },
                    { value: 'toText', label: t('episodePage.delToText') },
                    { value: 'fromText', label: t('episodePage.delFromText') },
                    { value: 'range', label: t('episodePage.delRange') },
                  ]}
                />
                {/* 删除指定文本 */}
                {(ruleParams.mode === 'text' || !ruleParams.mode) && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder={t('episodePage.placeholderDelText')}
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      {t('episodePage.caseSensitive')}
                    </label>
                  </>
                )}
                {/* 删除前N个字符 */}
                {ruleParams.mode === 'first' && (
                  <InputNumber
                    value={ruleParams.count || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                    min={0}
                    placeholder={t('episodePage.placeholderCharCount')}
                    style={{ width: 100 }}
                  />
                )}
                {/* 删除后N个字符 */}
                {ruleParams.mode === 'last' && (
                  <InputNumber
                    value={ruleParams.count || 0}
                    onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                    min={0}
                    placeholder={t('episodePage.placeholderCharCount')}
                    style={{ width: 100 }}
                  />
                )}
                {/* 从开头删到文本 */}
                {ruleParams.mode === 'toText' && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder={t('episodePage.placeholderDelToText')}
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      {t('episodePage.caseSensitive')}
                    </label>
                  </>
                )}
                {/* 从文本删到结尾 */}
                {ruleParams.mode === 'fromText' && (
                  <>
                    <Input
                      value={ruleParams.text || ''}
                      onChange={(e) => setRuleParams(p => ({ ...p, text: e.target.value }))}
                      placeholder={t('episodePage.placeholderDelFromText')}
                      style={{ width: 120 }}
                    />
                    <label className="flex items-center gap-1 text-sm">
                      <input
                        type="checkbox"
                        checked={ruleParams.caseSensitive || false}
                        onChange={(e) => setRuleParams(p => ({ ...p, caseSensitive: e.target.checked }))}
                      />
                      {t('episodePage.caseSensitive')}
                    </label>
                  </>
                )}
                {/* 删除范围 */}
                {ruleParams.mode === 'range' && (
                  <>
                    <span className="text-sm">{t('episodePage.rangeFrom')}</span>
                    <InputNumber
                      value={ruleParams.from || 0}
                      onChange={(v) => setRuleParams(p => ({ ...p, from: v }))}
                      min={0}
                      placeholder={t('episodePage.placeholderStartPos')}
                      style={{ width: 90 }}
                    />
                    <span className="text-sm">{t('episodePage.rangeDelete')}</span>
                    <InputNumber
                      value={ruleParams.count || 0}
                      onChange={(v) => setRuleParams(p => ({ ...p, count: v }))}
                      min={0}
                      placeholder={t('episodePage.placeholderCharCount')}
                      style={{ width: 80 }}
                    />
                    <span className="text-sm">{t('episodePage.rangeChars')}</span>
                  </>
                )}
              </>
            )}
            {/* 序列化规则参数 */}
            {selectedRuleType === 'serialize' && (
              <div className="w-full flex flex-col gap-2 p-2 bg-gray-100 dark:bg-gray-700 rounded">
                {/* 第一行：格式结构 */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-gray-500">{t('episodePage.formatStructure')}</span>
                  <Input
                    value={ruleParams.prefix || ''}
                    onChange={(e) => setRuleParams(p => ({ ...p, prefix: e.target.value }))}
                    placeholder={t('episodePage.placeholderPrefixSample')}
                    style={{ width: 120 }}
                    addonBefore={t('episodePage.addonPrefix')}
                    size="small"
                  />
                  <span className="text-xs text-gray-400">+</span>
                  <span className="px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300 rounded text-xs font-mono">
                    {t('episodePage.serialNumber')}
                  </span>
                  <span className="text-xs text-gray-400">+</span>
                  <Input
                    value={ruleParams.suffix || ''}
                    onChange={(e) => setRuleParams(p => ({ ...p, suffix: e.target.value }))}
                    placeholder={t('episodePage.placeholderSuffixSample')}
                    style={{ width: 120 }}
                    addonBefore={t('episodePage.addonSuffix')}
                    size="small"
                  />
                </div>
                {/* 第二行：序号参数 */}
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-gray-500">{t('episodePage.serialSettings')}</span>
                  <InputNumber
                    value={ruleParams.start || 1}
                    onChange={(v) => setRuleParams(p => ({ ...p, start: v }))}
                    min={0}
                    placeholder={t('episodePage.placeholderStart')}
                    style={{ width: 130 }}
                    addonBefore={t('episodePage.addonStartValue')}
                    size="small"
                  />
                  <InputNumber
                    value={ruleParams.digits || 2}
                    onChange={(v) => setRuleParams(p => ({ ...p, digits: v }))}
                    min={1}
                    max={5}
                    placeholder={t('episodePage.placeholderDigits')}
                    style={{ width: 130 }}
                    addonBefore={t('episodePage.addonPadZero')}
                    size="small"
                  />
                  <Select
                    value={ruleParams.position || 'replace'}
                    onChange={(v) => setRuleParams(p => ({ ...p, position: v }))}
                    style={{ width: 100 }}
                    size="small"
                    options={[
                      { value: 'start', label: t('episodePage.serialAddStart') },
                      { value: 'end', label: t('episodePage.serialAddEnd') },
                      { value: 'replace', label: t('episodePage.serialReplace') }
                    ]}
                  />
                </div>
                {/* 第三行：效果预览 */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500 dark:text-gray-400">{t('episodePage.effectPreview')}</span>
                  <span className="text-sm font-mono text-blue-600 dark:text-blue-400 font-semibold">
                    {
                      ruleParams.position === 'start'
                        ? `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}${t('episodePage.originalTitle')}`
                        : ruleParams.position === 'end'
                        ? `${t('episodePage.originalTitle')}${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                        : `${ruleParams.prefix || ''}${String(ruleParams.start || 1).padStart(ruleParams.digits || 2, '0')}${ruleParams.suffix || ''}`
                    }
                  </span>
                </div>
              </div>
            )}
            {/* 大小写规则参数 */}
            {selectedRuleType === 'case' && (
              <Select value={ruleParams.mode || 'upper'} onChange={(v) => setRuleParams(p => ({ ...p, mode: v }))} style={{ width: 120 }} options={[{ value: 'upper', label: t('episodePage.caseUpper') }, { value: 'lower', label: t('episodePage.caseLower') }, { value: 'title', label: t('episodePage.caseTitle') }]} />
            )}
            {/* 清理规则参数 */}
            {selectedRuleType === 'strip' && (
              <>
                <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={ruleParams.trimSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimSpaces: e.target.checked }))} />{t('episodePage.stripTrimSpaces')}</label>
                <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={ruleParams.trimDuplicateSpaces || false} onChange={(e) => setRuleParams(p => ({ ...p, trimDuplicateSpaces: e.target.checked }))} />{t('episodePage.stripDuplicateSpaces')}</label>
                <Input value={ruleParams.chars || ''} onChange={(e) => setRuleParams(p => ({ ...p, chars: e.target.value }))} placeholder={t('episodePage.placeholderDelChars')} style={{ width: 100 }} />
              </>
            )}
            <Button type="primary" onClick={handleAddRule}>{t('episodePage.btnAdd')}</Button>
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
                    {rule.type === 'insert' && t('episodePage.insertDesc', { text: rule.params.text, position: rule.params.position === 'start' ? t('episodePage.posStart') : t('episodePage.posEnd') })}
                    {rule.type === 'delete' && (() => {
                      const mode = rule.params.mode || 'text'
                      switch (mode) {
                        case 'text':
                          return t('episodePage.delTextDesc', { text: rule.params.text })
                        case 'first':
                          return t('episodePage.delFirstDesc', { count: rule.params.count || 0 })
                        case 'last':
                          return t('episodePage.delLastDesc', { count: rule.params.count || 0 })
                        case 'toText':
                          return t('episodePage.delToTextDesc', { text: rule.params.text })
                        case 'fromText':
                          return t('episodePage.delFromTextDesc', { text: rule.params.text })
                        case 'range':
                          return t('episodePage.delRangeDesc', { from: rule.params.from || 0, count: rule.params.count || 0 })
                        default:
                          return t('episodePage.delDefaultDesc')
                      }
                    })()}
                    {rule.type === 'serialize' && `${rule.params.prefix || ''}{${String(rule.params.start || 1).padStart(rule.params.digits || 2, '0')}}${rule.params.suffix || ''}`}
                    {rule.type === 'case' && (rule.params.mode === 'upper' ? t('episodePage.caseUpper') : rule.params.mode === 'lower' ? t('episodePage.caseLower') : t('episodePage.caseTitle'))}
                    {rule.type === 'strip' && t('episodePage.stripDesc')}
                  </span>
                  <Button type="text" danger size="small" onClick={() => handleDeleteRule(rule.id)}>🗑</Button>
                </div>
              ))}
            </div>
          )}
          {/* 预览和应用按钮 */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-sm">{t('episodePage.previewEffect')}</span>
              <Switch
                checked={isPreviewMode}
                onChange={(checked) => {
                  if (checked) handlePreviewRules()
                  else { setIsPreviewMode(false); setPreviewData({}) }
                }}
                disabled={renameRules.length === 0}
              />
            </div>
            <Button type="primary" onClick={handleApplyBatchRename} disabled={renameRules.length === 0}>{t('episodePage.btnApplyRules')}</Button>
          </div>
        </div>

        {/* 可拖拽编辑表格 */}
        <div className="border border-gray-200 dark:border-gray-600 rounded overflow-auto" style={{ maxHeight: 400, backgroundColor: 'var(--color-card)' }}>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={batchEditData.map(item => item.episodeId)} strategy={verticalListSortingStrategy}>
              <table className="w-full text-sm text-gray-900 dark:text-gray-100">
                <thead className="bg-gray-100 dark:bg-gray-700 sticky top-0 z-10">
                  <tr>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-10">{t('episodePage.thDrag')}</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-32">ID</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600">{t('episodePage.thEpisodeName')}</th>
                    <th className="p-2 border border-gray-200 dark:border-gray-600 w-24">{t('episodePage.thEpisodeIndex')}</th>
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
          💡 {t('episodePage.dragTip')}
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

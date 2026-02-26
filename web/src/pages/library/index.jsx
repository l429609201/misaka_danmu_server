import React, { useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tooltip,
  Tag,
  Collapse,
  Typography,
  Dropdown,
  Image,
} from 'antd'
import { QuestionCircleOutlined, MenuOutlined, FolderOpenOutlined, SearchOutlined, LinkOutlined, EyeOutlined } from '@ant-design/icons'
import {
  createAnimeEntry,
  deleteAnime,
  fetchLocalEpisodeGroupUrl,
  applyLocalEpisodeGroup,
  getAllEpisode,
  getAnimeDetail,
  getAnimeInfoAsSource,
  getAnimeLibrary,
  getBgmSearch,
  getDoubanSearch,
  getEgidSearch,
  getEpisodeGroupDetail,
  getImdbSearch,
  getTmdbSearch,
  getTvdbSearch,
  refreshPoster,
  setAnimeDetail,
  toggleSourceFavorite,
  toggleSourceIncremental,
  downloadPosterToLocal,
} from '../../apis'
import { MyIcon } from '@/components/MyIcon'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../configs'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { CreateAnimeModal } from '../../components/CreateAnimeModal'
import { IncrementalRefreshModal } from '../../components/IncrementalRefreshModal'
import { ScanDuplicatesModal } from '../../components/ScanDuplicatesModal'
import { RoutePaths } from '../../general/RoutePaths'
import { padStart } from 'lodash'
import { useModal } from '../../ModalContext'
import { useMessage } from '../../MessageContext'
import { ResponsiveTable } from '@/components/ResponsiveTable'
import DirectoryBrowser from '../media-fetch/components/DirectoryBrowser'
import PosterSearchModal from '../media-fetch/components/PosterSearchModal'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import { useDefaultPageSize } from '../../hooks/useDefaultPageSize'

const ApplyField = ({ name, label, fetchedValue, form }) => {
  const currentValue = Form.useWatch(name, form)

  return (
    <Form.Item label={label}>
      <div className="flex items-center gap-2">
        <Form.Item name={name} noStyle>
          <Input />
        </Form.Item>
        {fetchedValue && currentValue !== fetchedValue && (
          <Button
            size="small"
            onClick={() => form.setFieldsValue({ [name]: fetchedValue })}
          >
            应用
          </Button>
        )}
      </div>
    </Form.Item>
  )
}

export const Library = () => {
  // 从后端配置获取默认分页大小
  const defaultPageSize = useDefaultPageSize('library')

  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [keyword, setKeyword] = useState('')
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const [pagination, setPagination] = useState({
    current: 1,
    pageSize: defaultPageSize,
    total: 0,
  })

  // 当默认分页大小加载完成后，更新 pagination
  useEffect(() => {
    if (defaultPageSize) {
      setPagination(prev => ({
        ...prev,
        pageSize: defaultPageSize
      }))
    }
  }, [defaultPageSize])
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isRefreshModalOpen, setIsRefreshModalOpen] = useState(false)
  const [isScanDuplicatesOpen, setIsScanDuplicatesOpen] = useState(false)

  const [form] = Form.useForm()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const title = Form.useWatch('title', form)
  const tmdbId = Form.useWatch('tmdbId', form)
  const tvdbId = Form.useWatch('tvdbId', form)
  const doubanId = Form.useWatch('doubanId', form)
  const bangumiId = Form.useWatch('bangumiId', form)
  const imdbId = Form.useWatch('imdbId', form)
  const type = Form.useWatch('type', form)
  const animeId = Form.useWatch('animeId', form)
  const imageUrl = Form.useWatch('imageUrl', form)
  const egidValue = Form.useWatch('tmdbEpisodeGroupId', form)
  const [fetchedMetadata, setFetchedMetadata] = useState(null)

  // 海报搜索与本地海报状态
  const [posterSearchVisible, setPosterSearchVisible] = useState(false)
  const [localImagePath, setLocalImagePath] = useState(null)
  const [downloadingLocal, setDownloadingLocal] = useState(false)
  const [previewVisible, setPreviewVisible] = useState(false)

  const modalApi = useModal()
  const messageApi = useMessage()
  const deleteFilesRef = useRef(true) // 删除时是否同时删除弹幕文件，默认为 true

  // 源选择弹窗状态（用于标记和追更操作）
  const [sourceSelectOpen, setSourceSelectOpen] = useState(false)
  const [sourceSelectAction, setSourceSelectAction] = useState(null) // 'favorite' | 'incremental'
  const [sourceSelectSources, setSourceSelectSources] = useState([])
  const [sourceSelectTitle, setSourceSelectTitle] = useState('')
  const [selectedSourceId, setSelectedSourceId] = useState(null)

  const getList = async () => {
    try {
      setLoading(true)
      const res = await getAnimeLibrary({
        keyword: keyword,
        page: pagination.current,
        pageSize: pagination.pageSize,
      })
      setList(res.data?.list || [])
      setPagination(prev => ({
        ...prev,
        total: res.data?.total || 0,
      }))
    } catch (error) {
      setList([])
    } finally {
      setLoading(false)
    }
  }

  const handleCreateSuccess = () => {
    setIsCreateModalOpen(false)
    setPagination(n => {
      return {
        ...n,
        current: 1,
      }
    })
  }

  useEffect(() => {
    setPagination(n => {
      return {
        ...n,
        current: 1,
      }
    })
  }, [keyword])

  useEffect(() => {
    getList()
  }, [keyword, pagination.current, pagination.pageSize])

  useEffect(() => {
    setSearchInputValue(keyword)
  }, [keyword])

  useEffect(() => {
    if (!fetchedMetadata) return

    const currentValues = form.getFieldsValue()
    const newValues = {}

    const fieldsToUpdate = {
      nameEn: fetchedMetadata.nameEn,
      nameJp: containsJapanese(fetchedMetadata?.nameJp)
        ? fetchedMetadata.nameJp
        : null,
      nameRomaji: fetchedMetadata.nameRomaji ?? null,
      aliasCn1: fetchedMetadata.aliasesCn?.[0] ?? null,
      aliasCn2: fetchedMetadata.aliasesCn?.[1] ?? null,
      aliasCn3: fetchedMetadata.aliasesCn?.[2] ?? null,
      tvdbId: fetchedMetadata.tvdbId,
      imdbId: fetchedMetadata.imdbId,
      doubanId: fetchedMetadata.doubanId,
      bangumiId: fetchedMetadata.bangumiId,
    }

    for (const [key, value] of Object.entries(fieldsToUpdate)) {
      if (!currentValues[key] && value) {
        newValues[key] = value
      }
    }

    if (Object.keys(newValues).length > 0) {
      form.setFieldsValue(newValues)
    }
    // 没有封面时填充url
    if (!imageUrl && !!fetchedMetadata?.imageUrl) {
      form.setFieldsValue({
        imageUrl: fetchedMetadata.imageUrl,
      })
    }
  }, [fetchedMetadata, form])

  const columns = [
    {
      title: '海报',
      dataIndex: 'imageUrl',
      key: 'imageUrl',
      width: 100,
      render: (_, record) => {
        let imageSrc = record.localImagePath || record.imageUrl
        // 兼容旧的、错误的缓存路径
        if (imageSrc && imageSrc.startsWith('/images/')) {
          imageSrc = imageSrc.replace('/images/', '/data/images/')
        }
        // 如果两个地址都为空，则不渲染img标签，避免出现损坏的图片图标
        return imageSrc ? <img src={imageSrc} className="w-12 cursor-pointer hover:opacity-80 transition-opacity" onClick={() => navigate(`/anime/${record.animeId}`)} /> : null
      },
    },
    {
      title: '影视名称',
      dataIndex: 'title',
      key: 'title',
      width: 200,
    },
    {
      title: '类型',
      width: 100,
      dataIndex: 'type',
      key: 'type',
      render: (_, record) => {
        return <span>{DANDAN_TYPE_DESC_MAPPING[record.type]}</span>
      },
    },
    {
      title: '季',
      dataIndex: 'season',
      key: 'season',
      width: 50,
    },
    {
      title: '年份',
      dataIndex: 'year',
      key: 'year',
      width: 70,
    },
    {
      title: '集数',
      dataIndex: 'episodeCount',
      key: 'episodeCount',
      width: 70,
    },
    {
      title: '源数量',
      dataIndex: 'sourceCount',
      key: 'sourceCount',
      width: 80,
    },
    {
      title: '收录时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 150,
      render: (_, record) => {
        return (
          <div>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: '操作',
      width: 150,
      fixed: 'right',
      render: (_, record) => {
        // 判断是否有已标记或已追更的源
        const hasFavorited = record.sources?.some(s => s.isFavorited)
        const hasIncremental = record.sources?.some(s => s.incrementalRefreshEnabled)
        return (
          <Space>
            <Tooltip title="编辑影视信息">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={async () => {
                  const res = await getAnimeDetail({
                    animeId: record.animeId,
                  })
                  form.resetFields()
                  form.setFieldsValue({
                    ...(res.data || {}),
                    animeId: record.animeId,
                  })
                  setLocalImagePath(res.data?.localImagePath || null)
                  setEditOpen(true)
                }}
              >
                <MyIcon icon="edit" size={20}></MyIcon>
              </span>
            </Tooltip>

            <Tooltip title={hasFavorited ? "已标记（点击管理）" : "标记精确源"}>
              <span
                className={`cursor-pointer hover:text-primary ${hasFavorited ? 'text-yellow-500' : ''}`}
                onClick={() => handleFavorite(record)}
              >
                <MyIcon icon={hasFavorited ? "favorites-fill" : "favorites"} size={20}></MyIcon>
              </span>
            </Tooltip>

            <Tooltip title={hasIncremental ? "追更中（点击管理）" : "开启追更"}>
              <span
                className={`cursor-pointer hover:text-primary ${hasIncremental ? 'text-green-500' : ''}`}
                onClick={() => handleIncremental(record)}
              >
                <MyIcon icon={hasIncremental ? "zengliang" : "clock"} size={20}></MyIcon>
              </span>
            </Tooltip>

            <Tooltip title="详情">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  if (!record.animeId || record.animeId === 0) {
                    messageApi.error('无效的作品ID')
                    return
                  }
                  navigate(`/anime/${record.animeId}`)
                }}
              >
                <MyIcon icon="book" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="删除">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  handleDelete(record)
                }}
              >
                <MyIcon icon="delete" size={20}></MyIcon>
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

  // 处理标记操作
  const handleFavorite = async (record) => {
    const sources = record.sources || []
    if (sources.length === 0) {
      messageApi.warning('该作品没有数据源')
      return
    }
    if (sources.length === 1) {
      // 只有一个源，直接切换
      try {
        await toggleSourceFavorite({ sourceId: sources[0].sourceId })
        messageApi.success('标记状态已更新')
        getList()
      } catch (error) {
        messageApi.error('操作失败')
      }
    } else {
      // 多个源，弹窗选择
      setSourceSelectAction('favorite')
      setSourceSelectSources(sources)
      setSourceSelectTitle(record.title)
      setSelectedSourceId(sources.find(s => s.isFavorited)?.sourceId || sources[0].sourceId)
      setSourceSelectOpen(true)
    }
  }

  // 处理追更操作
  const handleIncremental = async (record) => {
    const sources = record.sources || []
    if (sources.length === 0) {
      messageApi.warning('该作品没有数据源')
      return
    }
    if (sources.length === 1) {
      // 只有一个源，直接切换
      try {
        await toggleSourceIncremental({ sourceId: sources[0].sourceId })
        messageApi.success('追更状态已更新')
        getList()
      } catch (error) {
        messageApi.error('操作失败')
      }
    } else {
      // 多个源，弹窗选择
      setSourceSelectAction('incremental')
      setSourceSelectSources(sources)
      setSourceSelectTitle(record.title)
      setSelectedSourceId(sources.find(s => s.incrementalRefreshEnabled)?.sourceId || sources[0].sourceId)
      setSourceSelectOpen(true)
    }
  }

  // 确认源选择
  const handleSourceSelectConfirm = async () => {
    if (!selectedSourceId) {
      messageApi.warning('请选择一个数据源')
      return
    }
    try {
      if (sourceSelectAction === 'favorite') {
        await toggleSourceFavorite({ sourceId: selectedSourceId })
        messageApi.success('标记状态已更新')
      } else if (sourceSelectAction === 'incremental') {
        await toggleSourceIncremental({ sourceId: selectedSourceId })
        messageApi.success('追更状态已更新')
      }
      setSourceSelectOpen(false)
      getList()
    } catch (error) {
      messageApi.error('操作失败')
    }
  }

  const handleDelete = async record => {
    deleteFilesRef.current = true // 重置为默认值
    modalApi.confirm({
      title: '删除',
      zIndex: 1002,
      content: (
        <div>
          确定要删除{record.name}吗？
          <br />
          此操作将在后台提交一个删除任务
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
          const res = await deleteAnime({ animeId: record.animeId, deleteFiles: deleteFilesRef.current })
          goTask(res)
        } catch (error) {
          messageApi.error('提交删除任务失败')
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
          {res.message || '批量删除任务已提交'}
          <br />
          是否立即跳转到任务管理器查看进度？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        navigate(`${RoutePaths.TASK}?status=all`)
      },
      onCancel: () => {
        getList()
      },
    })
  }

  const handleSave = async () => {
    try {
      if (confirmLoading) return
      setConfirmLoading(true)
      const values = await form.validateFields()
      await setAnimeDetail({
        ...values,
        year: values.year ? Number(values.year) : null,
        tmdbId: values.tmdbId ? `${values.tmdbId}` : null,
        tvdbId: values.tvdbId ? `${values.tvdbId}` : null,
      })
      getList()
      messageApi.success('信息更新成功')
    } catch (error) {
      messageApi.error(error.detail || '编辑失败')
    } finally {
      setConfirmLoading(false)
      setEditOpen(false)
    }
  }

  const containsJapanese = str => {
    if (!str) return false
    // 此正则表达式匹配日文假名和常见的CJK统一表意文字
    return /[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]/.test(str)
  }

  /** 搜索相关 */
  /** 精准搜索loading */
  const [searchAsIdLoading, setSearchAsIdLoading] = useState(false)

  const handleSearchAsId = async ({ source, currentId, mediaType }) => {
    try {
      if (searchAsIdLoading || !currentId) return
      setSearchAsIdLoading(true)
      const res = await getAnimeInfoAsSource({ source, currentId, mediaType })
      applySearchSelectionData({
        data: res.data,
        source,
      })
      messageApi.success(
        `${source.toUpperCase()} 信息获取成功，请检查并应用建议的别名。`
      )
    } catch (error) {
      messageApi.error(
        `获取 ${source.toUpperCase()} 详情失败: ${error.message}`
      )
    } finally {
      setSearchAsIdLoading(false)
    }
  }

  const applySearchSelectionData = ({ data, source }) => {
    if (!data) return
    switch (source) {
      case 'bangumi':
        form.setFieldsValue({
          nameEn: data.nameEn,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameRomaji: data.nameRomaji,
          ...getAliasCn(data.aliasesCn, data.name),
        })
        break
      case 'tmdb':
        form.setFieldsValue({
          imdbId: data.imdbId,
          tvdbId: data.tvdbId,
          nameEn: data.nameEn,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameRomaji: data.nameRomaji,
          ...getAliasCn(data.aliasesCn, data.mainTitleFromSearch),
        })
        break
      case 'imdb':
        form.setFieldsValue({
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          ...getAliasCn(data.aliasesCn, data.nameEn),
        })
        break
      case 'tvdb':
        form.setFieldsValue({
          imdbId: data.imdbId,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameEn: data.nameEn,
          ...getAliasCn(data.aliasesCn, data.nameEn),
        })
        break
      case 'douban':
        form.setFieldsValue({
          imdbId: data.imdbId,
          nameJp: containsJapanese(data.nameJp) ? data.nameJp : '',
          nameEn: data.nameEn,
          ...getAliasCn(
            data.aliasesCn,
            // 修正：统一使用驼峰命名的 aliasesCn，并提供更好的备用标题
            data.aliasesCn && data.aliasesCn.length > 0
              ? data.aliasesCn[0]
              : data.name || ''
          ),
        })
        break
    }
  }

  const getAliasCn = (aliasesCn, name) => {
    const filteredAliases = (aliasesCn || []).filter(
      alias => !!alias && alias !== name
    )
    return {
      aliasCn1: filteredAliases?.[0],
      aliasCn2: filteredAliases?.[1],
      aliasCn3: filteredAliases?.[2],
    }
  }

  const [tmdbResult, setTmdbResult] = useState([])
  const [tmdbOpen, setTmdbOpen] = useState(false)
  const [searchTmdbLoading, setSearchTmdbLoading] = useState(false)
  const onTmdbSearch = async () => {
    try {
      if (searchTmdbLoading) return
      setSearchTmdbLoading(true)
      const res = await getTmdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'tv' : 'movie',
      })
      if (!!res?.data?.length) {
        setTmdbResult(res?.data || [])
        setTmdbOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error(`TMDB搜索失败:${error.message}`)
    } finally {
      setSearchTmdbLoading(false)
    }
  }

  const [tvdbResult, setTvdbResult] = useState([])
  const [tvdbOpen, setTvdbOpen] = useState(false)
  const [searchTvdbLoading, setSearchTvdbLoading] = useState(false)
  const onTvdbSearch = async () => {
    try {
      if (searchTvdbLoading) return
      setSearchTvdbLoading(true)
      const res = await getTvdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'series' : 'movie',
      })
      if (!!res?.data?.length) {
        setTvdbResult(res?.data || [])
        setTvdbOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error(`TVDB搜索失败:${error.message}`)
    } finally {
      setSearchTvdbLoading(false)
    }
  }

  const [doubanResult, setDoubanResult] = useState([])
  const [doubanOpen, setDoubanOpen] = useState(false)
  const [searchDoubanLoading, setSearchDoubanLoading] = useState(false)
  const onDoubanSearch = async () => {
    try {
      if (searchDoubanLoading) return
      setSearchDoubanLoading(true)
      const res = await getDoubanSearch({
        keyword: title,
      })
      if (!!res?.data?.length) {
        setDoubanResult(res?.data || [])
        setDoubanOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error(`豆瓣搜索失败:${error.message}`)
    } finally {
      setSearchDoubanLoading(false)
    }
  }

  const [imdbResult, setImdbResult] = useState([])
  const [imdbOpen, setImdbOpen] = useState(false)
  const [searchImdbLoading, setSearchImdbLoading] = useState(false)
  const onImdbSearch = async () => {
    try {
      if (searchImdbLoading) return
      setSearchImdbLoading(true)
      const res = await getImdbSearch({
        keyword: title,
        mediaType: type === DANDAN_TYPE_MAPPING.tvseries ? 'series' : 'movie',
      })
      if (!!res?.data?.length) {
        setImdbResult(res?.data || [])
        setImdbOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error(
        error.detail || `IMDB搜索失败: ${error.message || '未知错误'}`
      )
    } finally {
      setSearchImdbLoading(false)
    }
  }

  const [egidResult, setEgidResult] = useState([])
  const [egidOpen, setEgidOpen] = useState(false)
  const [searchEgidLoading, setSearchEgidLoading] = useState(false)
  const [searchAllEpisodeLoading, setSearchAllEpisodeLoading] = useState(false)
  const [allEpisode, setAllEpisode] = useState({})
  const [episodeOpen, setEpisodeOpen] = useState(false)

  // ---- 本地剧集组 state ----
  const [localEgOpen, setLocalEgOpen] = useState(false)
  const [localEgParsedData, setLocalEgParsedData] = useState(null)
  const [localEgApplyLoading, setLocalEgApplyLoading] = useState(false)
  const [pasteJsonOpen, setPasteJsonOpen] = useState(false)
  const [pasteJsonValue, setPasteJsonValue] = useState('')
  const [localPathOpen, setLocalPathOpen] = useState(false)
  const [localPathValue, setLocalPathValue] = useState('')
  const [localPathLoading, setLocalPathLoading] = useState(false)
  const [fileBrowserOpen, setFileBrowserOpen] = useState(false)

  // 公共校验：解析并校验剧集组JSON数据
  const validateAndApplyEgJson = (jsonStr) => {
    try {
      const data = JSON.parse(jsonStr)
      if (!data.groups || !Array.isArray(data.groups) || data.groups.length === 0) {
        messageApi.error('JSON 格式不正确：缺少有效的 groups 数组')
        return false
      }
      for (const g of data.groups) {
        if (!g.episodes || !Array.isArray(g.episodes)) {
          messageApi.error('JSON 格式不正确：每个 group 需要包含 episodes 数组')
          return false
        }
      }
      setLocalEgParsedData(data)
      setLocalEgOpen(true)
      return true
    } catch {
      messageApi.error('内容不是有效的 JSON')
      return false
    }
  }

  // 查询服务端本地路径的JSON
  const handleLocalPathConfirm = async () => {
    const pathVal = localPathValue.trim()
    if (!pathVal) {
      messageApi.warning('请输入服务端路径或网络URL')
      return
    }
    try {
      setLocalPathLoading(true)
      const res = await fetchLocalEpisodeGroupUrl({ url: pathVal })
      if (res?.data?.groups) {
        setLocalEgParsedData(res.data)
        setLocalEgOpen(true)
        setLocalPathOpen(false)
        setLocalPathValue('')
      } else {
        messageApi.error('获取的JSON格式不正确，缺少 groups 字段')
      }
    } catch (e) {
      messageApi.error(`获取失败: ${e?.response?.data?.detail || e.message}`)
    } finally {
      setLocalPathLoading(false)
    }
  }

  // 粘贴JSON确认
  const handlePasteJsonConfirm = () => {
    if (!pasteJsonValue.trim()) {
      messageApi.warning('请粘贴 JSON 内容')
      return
    }
    if (validateAndApplyEgJson(pasteJsonValue)) {
      setPasteJsonOpen(false)
      setPasteJsonValue('')
    }
  }

  // ---- 查看/编辑剧集组 ----
  const [editEgOpen, setEditEgOpen] = useState(false)
  const [editEgData, setEditEgData] = useState(null) // { id, name, description, groups: [...] }
  const [editEgLoading, setEditEgLoading] = useState(false)
  const [editEgSaving, setEditEgSaving] = useState(false)

  const handleOpenEditEg = async () => {
    const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()
    if (!egidValue) {
      messageApi.warning('当前没有剧集组ID')
      return
    }
    try {
      setEditEgLoading(true)
      // 从数据库读取已保存的剧集组映射
      const res = await getEpisodeGroupDetail(egidValue)
      if (res?.data?.id) {
        const raw = res.data
        const groups = (raw.groups || []).map((g, gi) => ({
          _key: gi,
          name: g.name || '',
          order: g.order ?? gi,
          episodes: (g.episodes || []).map((ep, ei) => ({
            _key: ei,
            seasonNumber: ep.seasonNumber ?? 0,
            episodeNumber: ep.episodeNumber ?? 0,
            order: ep.order ?? ei,
            name: ep.name || '',
          })),
        }))
        setEditEgData({ id: raw.id, name: raw.name || '', description: raw.description || '', groups })
        setEditEgOpen(true)
      } else {
        messageApi.error('没有找到该剧集组的映射数据')
      }
    } catch (error) {
      messageApi.error(`获取剧集组失败: ${error?.response?.data?.detail || error.message}`)
    } finally {
      setEditEgLoading(false)
    }
  }

  // 编辑剧集组内部操作
  const updateEditEgField = (path, value) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      let target = next
      for (let i = 0; i < path.length - 1; i++) target = target[path[i]]
      target[path[path.length - 1]] = value
      return next
    })
  }

  const addEgGroup = () => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups.push({ _key: Date.now(), name: '', order: next.groups.length, episodes: [] })
      return next
    })
  }

  const removeEgGroup = (gi) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups.splice(gi, 1)
      return next
    })
  }

  const addEgEpisode = (gi) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      const eps = next.groups[gi].episodes
      eps.push({ _key: Date.now(), seasonNumber: 0, episodeNumber: 0, order: eps.length, name: '' })
      return next
    })
  }

  const removeEgEpisode = (gi, ei) => {
    setEditEgData(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      next.groups[gi].episodes.splice(ei, 1)
      return next
    })
  }

  const handleSaveEditEg = async () => {
    if (!editEgData) return
    if (!tmdbId) { messageApi.warning('请先填写 TMDB ID'); return }
    try {
      setEditEgSaving(true)
      // 转换为 applyLocalEpisodeGroup 需要的格式
      const localEpisodeGroup = {
        description: editEgData.name || '本地剧集组',
        groups: editEgData.groups.map(g => ({
          name: g.name,
          order: g.order,
          episodes: g.episodes.map(ep => ({
            season_number: ep.seasonNumber,
            episode_number: ep.episodeNumber,
            order: ep.order,
          })),
        })),
      }
      const res = await applyLocalEpisodeGroup({
        tmdbId: Number(tmdbId),
        localEpisodeGroup,
      })
      if (res?.data?.groupId) {
        form.setFieldsValue({ tmdbEpisodeGroupId: res.data.groupId })
        messageApi.success(`剧集组已保存，共 ${res.data.episodeCount} 条映射`)
        setEditEgOpen(false)
        setEditEgData(null)
      }
    } catch (e) {
      messageApi.error(`保存失败: ${e?.response?.data?.detail || e.message}`)
    } finally {
      setEditEgSaving(false)
    }
  }

  const handleLocalEgApply = async () => {
    if (!localEgParsedData) { messageApi.warning('请先解析剧集组数据'); return }
    if (!tmdbId) { messageApi.warning('请先填写 TMDB ID'); return }
    try {
      setLocalEgApplyLoading(true)
      const res = await applyLocalEpisodeGroup({
        tmdbId: Number(tmdbId),
        localEpisodeGroup: localEgParsedData,
      })
      if (res?.data?.groupId) {
        form.setFieldsValue({ tmdbEpisodeGroupId: res.data.groupId })
        messageApi.success(`本地剧集组已应用，共 ${res.data.episodeCount} 条映射`)
        setLocalEgOpen(false)
        setLocalEgParsedData(null)
      }
    } catch (e) {
      messageApi.error(`应用失败: ${e?.response?.data?.detail || e.message}`)
    } finally {
      setLocalEgApplyLoading(false)
    }
  }

  // 判断输入是否为 URL 或本地路径
  const isEgidInputPath = (val) => {
    if (!val) return false
    const v = val.trim()
    return v.startsWith('http://') || v.startsWith('https://') || v.endsWith('.json')
  }

  const onEgidSearch = async () => {
    try {
      if (searchEgidLoading) return
      setSearchEgidLoading(true)

      const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()

      if (isEgidInputPath(egidValue)) {
        // URL 或本地路径 → 获取JSON → 弹预览窗
        const res = await fetchLocalEpisodeGroupUrl({ url: egidValue })
        if (res?.data?.groups) {
          setLocalEgParsedData(res.data)
          setLocalEgOpen(true)
        } else {
          messageApi.error('获取的JSON格式不正确')
        }
      } else if (egidValue) {
        // 有内容但不是路径 → 当作剧集组ID直接查详情
        const res = await getAllEpisode({ tmdbId: tmdbId, egid: egidValue })
        if (res?.data?.id) {
          setAllEpisode(res.data)
          setEpisodeOpen(true)
        } else {
          messageApi.error('没有找到该剧集组的分集信息')
        }
      } else {
        // 空 → 按TMDB ID搜索剧集组列表
        if (!tmdbId) {
          messageApi.warning('请先填写 TMDB ID，或输入本地剧集组路径 / 网络URL')
          return
        }
        const res = await getEgidSearch({ tmdbId: tmdbId, keyword: title })
        if (res?.data?.length) {
          setEgidResult(res.data)
          setEgidOpen(true)
        } else {
          messageApi.error('没有找到相关内容')
        }
      }
    } catch (error) {
      messageApi.error(`剧集组搜索失败: ${error?.response?.data?.detail || error.message}`)
    } finally {
      setSearchEgidLoading(false)
    }
  }

  const handleAllEpisode = async item => {
    try {
      if (searchAllEpisodeLoading) return
      setSearchAllEpisodeLoading(true)
      const res = await getAllEpisode({
        tmdbId: tmdbId,
        egid: item.id,
      })
      if (!!res?.data?.id) {
        setAllEpisode(res?.data || {})
        setEpisodeOpen(true)
      } else {
        messageApi.error('没有找到相关分集')
      }
    } catch (error) {
      messageApi.error('没有找到相关分集')
    } finally {
      setSearchAllEpisodeLoading(false)
    }
  }

  const [searchInputValue, setSearchInputValue] = useState('')

  const handleSearch = () => {
    setKeyword(searchInputValue)
  }

  const handleReset = () => {
    setKeyword('')
    setSearchInputValue('')
  }

  const [bgmResult, setBgmResult] = useState([])
  const [bgmOpen, setBgmOpen] = useState(false)
  const [searchBgmLoading, setSearchBgmLoading] = useState(false)
  const onBgmSearch = async () => {
    try {
      if (searchBgmLoading) return
      setSearchBgmLoading(true)
      const res = await getBgmSearch({
        keyword: title,
      })
      if (!!res?.data?.length) {
        setBgmResult(res?.data || [])
        setBgmOpen(true)
      } else {
        messageApi.error('没有找到相关内容')
      }
    } catch (error) {
      messageApi.error(`BGM搜索失败:${error.message}`)
    } finally {
      setSearchBgmLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card
        loading={loading}
        title="弹幕库"
        extra={
          !isMobile && (
            <Space>
              <Input.Search
                placeholder="请输入影视名称"
                value={searchInputValue}
                onChange={(e) => setSearchInputValue(e.target.value)}
                onSearch={handleSearch}
                enterButton="搜索"
                allowClear
                style={{ width: 300 }}
              />
              {keyword && (
                <Button onClick={handleReset}>
                  重置
                </Button>
              )}
              <Button onClick={() => setIsScanDuplicatesOpen(true)}>
                扫描重复项
              </Button>
              <Button onClick={() => setIsRefreshModalOpen(true)}>
                批量管理
              </Button>
              <Button type="primary" onClick={() => setIsCreateModalOpen(true)}>
                自定义影视条目
              </Button>
            </Space>
          )
        }
      >
        {isMobile && (
          <div className="mb-4">
            <div className="flex gap-2 mb-3 items-center">
              <div className="flex-1 flex items-center">
                <Input
                  placeholder="请输入影视名称"
                  value={searchInputValue}
                  onChange={(e) => setSearchInputValue(e.target.value)}
                  onPressEnter={handleSearch}
                  allowClear
                  style={{
                    height: 44,
                    lineHeight: '44px',
                    paddingTop: 0,
                    paddingBottom: 0,
                    borderTopRightRadius: 0,
                    borderBottomRightRadius: 0,
                    fontSize: 14
                  }}
                  className="flex-1"
                />
                <Button
                  type="primary"
                  onClick={handleSearch}
                  style={{
                    height: 44,
                    lineHeight: '44px',
                    borderTopLeftRadius: 0,
                    borderTopRightRadius: 9,
                    borderBottomLeftRadius: 0,
                    borderBottomRightRadius: 9,
                    fontSize: 14
                  }}
                >
                  搜索
                </Button>
              </div>
              {keyword && (
                <Button onClick={handleReset} style={{ height: 44 }}>
                  重置
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                block
                size="large"
                onClick={() => setIsScanDuplicatesOpen(true)}
              >
                扫描重复项
              </Button>
              <Button
                block
                size="large"
                onClick={() => setIsRefreshModalOpen(true)}
              >
                批量管理
              </Button>
              <Button
                type="primary"
                block
                size="large"
                onClick={() => setIsCreateModalOpen(true)}
              >
                自定义影视条目
              </Button>
            </div>
          </div>
        )}
        <ResponsiveTable
          dataSource={list}
          columns={columns}
          loading={loading}
          rowKey="animeId"
          pagination={{
            ...pagination,
            showTotal: total => `共 ${total} 条数据`,
            onChange: (page, pageSize) => {
              setPagination(n => ({
                ...n,
                current: page,
                pageSize,
              }))
            },
            onShowSizeChange: (_, size) => {
              setPagination(n => ({
                ...n,
                pageSize: size,
              }))
            },
            hideOnSinglePage: true,
          }}
          renderCard={(record) => (
            <div className="space-y-3">
              <div className="flex gap-3">
                {(() => {
                  let imageSrc = record.localImagePath || record.imageUrl
                  if (imageSrc && imageSrc.startsWith('/images/')) {
                    imageSrc = imageSrc.replace('/images/', '/data/images/')
                  }
                  return imageSrc ? (
                    <img src={imageSrc} className="w-20 h-28 object-cover rounded cursor-pointer hover:opacity-80 transition-opacity" alt={record.title} onClick={() => navigate(`/anime/${record.animeId}`)} />
                  ) : (
                    <div className="w-20 h-28 bg-gray-200 dark:bg-gray-700 rounded flex items-center justify-center cursor-pointer hover:opacity-70 transition-opacity" onClick={() => navigate(`/anime/${record.animeId}`)}>
                      <MyIcon icon="image" size={32} />
                    </div>
                  )
                })()}
                <div className="flex-1 space-y-2">
                  <div className="font-bold text-lg line-clamp-2">{record.title}</div>
                  <div className="flex flex-wrap gap-2">
                    <Tag color="blue">{DANDAN_TYPE_DESC_MAPPING[record.type]}</Tag>
                    {record.season && <Tag>第{record.season}季</Tag>}
                    {record.year && <Tag>{record.year}年</Tag>}
                  </div>
                  <div className="text-sm text-gray-600 dark:text-gray-400">
                    <span>集数: {record.episodeCount || 0}</span>
                    <span className="mx-2">·</span>
                    <span>源: {record.sourceCount || 0}</span>
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-500">
                    {dayjs(record.createdAt).format('YYYY-MM-DD HH:mm')}
                  </div>
                </div>
              </div>
              <div className="flex justify-center gap-2 pt-2 border-t border-gray-200 dark:border-gray-700 flex-wrap">
                <Button
                  size="small"
                  type="text"
                  icon={<MyIcon icon="edit" size={16} />}
                  onClick={async () => {
                    const res = await getAnimeDetail({ animeId: record.animeId })
                    form.resetFields()
                    form.setFieldsValue({
                      ...(res.data || {}),
                      animeId: record.animeId,
                    })
                    setLocalImagePath(res.data?.localImagePath || null)
                    setEditOpen(true)
                  }}
                >
                  编辑
                </Button>
                <Button
                  size="small"
                  type="text"
                  icon={<MyIcon icon={record.sources?.some(s => s.isFavorited) ? "favorites-fill" : "favorites"} size={16} />}
                  className={record.sources?.some(s => s.isFavorited) ? 'text-yellow-500' : ''}
                  onClick={() => handleFavorite(record)}
                >
                  标记
                </Button>
                <Button
                  size="small"
                  type="text"
                  icon={<MyIcon icon={record.sources?.some(s => s.incrementalRefreshEnabled) ? "zengliang" : "clock"} size={16} />}
                  className={record.sources?.some(s => s.incrementalRefreshEnabled) ? 'text-green-500' : ''}
                  onClick={() => handleIncremental(record)}
                >
                  追更
                </Button>
                <Button
                  size="small"
                  type="text"
                  icon={<MyIcon icon="book" size={16} />}
                  onClick={() => {
                    if (!record.animeId || record.animeId === 0) {
                      messageApi.error('无效的作品ID')
                      return
                    }
                    navigate(`/anime/${record.animeId}`)
                  }}
                >
                  详情
                </Button>
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<MyIcon icon="delete" size={16} />}
                  onClick={() => handleDelete(record)}
                >
                  删除
                </Button>
              </div>
            </div>
          )}
        />
      </Card>
      <CreateAnimeModal
        open={isCreateModalOpen}
        onCancel={() => setIsCreateModalOpen(false)}
        onSuccess={handleCreateSuccess}
      />
      <IncrementalRefreshModal
        open={isRefreshModalOpen}
        onCancel={() => setIsRefreshModalOpen(false)}
      />
      <ScanDuplicatesModal
        open={isScanDuplicatesOpen}
        onCancel={() => setIsScanDuplicatesOpen(false)}
        onSuccess={() => { setIsScanDuplicatesOpen(false); fetchList() }}
      />
      <Modal
        title="编辑影视信息"
        open={editOpen}
        onOk={handleSave}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => {
          setEditOpen(false)
          setFetchedMetadata(null)
          setLocalImagePath(null)
        }}
        zIndex={100}
      >
        <Form form={form} layout="horizontal">
          <Form.Item
            name="title"
            label="影视名称"
            rules={[{ required: true, message: '请输入影视名称' }]}
          >
            <Input placeholder="请输入影视名称" />
          </Form.Item>
          <Form.Item
            name="type"
            label="类型"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select
              options={[
                {
                  value: 'tv_series',
                  label: DANDAN_TYPE_DESC_MAPPING['tv_series'],
                },
                {
                  value: 'movie',
                  label: DANDAN_TYPE_DESC_MAPPING['movie'],
                },
              ]}
            />
          </Form.Item>
          <Form.Item name="season" label="季度">
            <InputNumber style={{ width: '100%' }} placeholder="请输入季度" />
          </Form.Item>
          <Form.Item name="episodeCount" label="集数">
            <InputNumber
              style={{ width: '100%' }}
              placeholder="留空则自动计算"
            />
          </Form.Item>
          <Form.Item name="year" label="年份">
            <InputNumber
              style={{ width: '100%' }}
              placeholder="请输入发行年份"
            />
          </Form.Item>
          <Form.Item label="海报URL">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="imageUrl" noStyle>
                <Input placeholder="https://..." style={{ flex: 1 }} />
              </Form.Item>
              <Tooltip title="搜索海报">
                <Button
                  icon={<SearchOutlined />}
                  onClick={() => setPosterSearchVisible(true)}
                />
              </Tooltip>
              <Tooltip title="URL直搜（下载到本地）">
                <Button
                  icon={<LinkOutlined />}
                  loading={downloadingLocal}
                  onClick={async () => {
                    if (!imageUrl) {
                      messageApi.warning('请先填写海报URL')
                      return
                    }
                    setDownloadingLocal(true)
                    try {
                      const res = await downloadPosterToLocal({
                        imageUrl,
                        title: title || '',
                        season: form.getFieldValue('season') || 1,
                        year: form.getFieldValue('year') || undefined,
                      })
                      if (res?.data?.localImagePath) {
                        setLocalImagePath(res.data.localImagePath)
                        messageApi.success('海报已下载到本地')
                      } else {
                        messageApi.error('下载失败')
                      }
                    } catch (error) {
                      messageApi.error(`下载失败: ${error?.response?.data?.detail || error.message}`)
                    } finally {
                      setDownloadingLocal(false)
                    }
                  }}
                />
              </Tooltip>
              <Tooltip title="刷新海报缓存">
                <Button
                  icon={<MyIcon icon="refresh" size={16} />}
                  onClick={async () => {
                    try {
                      await refreshPoster({ animeId, imageUrl })
                      messageApi.success('海报已刷新并缓存成功！')
                    } catch (error) {
                      messageApi.error(`刷新海报失败: ${error.message}`)
                    }
                  }}
                />
              </Tooltip>
            </Space.Compact>
          </Form.Item>
          {!!fetchedMetadata?.imageUrl &&
            fetchedMetadata?.imageUrl !== imageUrl && (
              <Form.Item className="text-right">
                <Button
                  className="cursor-pointer"
                  onClick={() => {
                    form.setFieldsValue({
                      imageUrl: fetchedMetadata.imageUrl,
                    })
                  }}
                >
                  应用URL
                </Button>
              </Form.Item>
            )}

          {/* 本地海报行 */}
          <Form.Item label="本地海报">
            <Space style={{ width: '100%' }}>
              <Input
                value={localImagePath || '暂无'}
                readOnly
                style={{ flex: 1, minWidth: 300, color: localImagePath ? undefined : 'var(--text-tertiary, #999)' }}
              />
              <Tooltip title="预览海报">
                <Button
                  icon={<EyeOutlined />}
                  disabled={!localImagePath}
                  onClick={() => setPreviewVisible(true)}
                />
              </Tooltip>
            </Space>
          </Form.Item>

          <Form.Item name="tmdbId" label="TMDB ID">
            <Input.Search
              placeholder="例如：1396"
              allowClear
              enterButton="搜索"
              suffix={
                <Tooltip title="ID直搜">
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'tmdb',
                        currentId: tmdbId,
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'tv'
                            : 'movie',
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchTmdbLoading}
              onSearch={() => {
                onTmdbSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="tmdbEpisodeGroupId" label="剧集组ID">
            <Input.Search
              placeholder="剧集组ID / URL / 本地路径(.json)"
              allowClear
              enterButton="搜索"
              loading={searchEgidLoading}
              prefix={egidValue?.startsWith?.('local-') ? <Tag color="green" className="mr-0">本地</Tag> : null}
              onSearch={() => {
                onEgidSearch()
              }}
              suffix={
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: 'id-search',
                        label: '剧集组ID直搜',
                        onClick: async () => {
                          const egidValue = form.getFieldValue('tmdbEpisodeGroupId')?.trim()
                          if (!egidValue) {
                            messageApi.warning('请先输入剧集组ID')
                            return
                          }
                          try {
                            setSearchEgidLoading(true)
                            const res = await getAllEpisode({ tmdbId: tmdbId, egid: egidValue })
                            if (res?.data?.id) {
                              setAllEpisode(res.data)
                              setEpisodeOpen(true)
                            } else {
                              messageApi.error('没有找到该剧集组的分集信息')
                            }
                          } catch (error) {
                            messageApi.error(`搜索失败: ${error?.response?.data?.detail || error.message}`)
                          } finally {
                            setSearchEgidLoading(false)
                          }
                        },
                      },
                      {
                        key: 'local-json',
                        label: '查询本地JSON',
                        onClick: () => {
                          setLocalPathOpen(true)
                        },
                      },
                      {
                        key: 'paste-json',
                        label: '粘贴JSON',
                        onClick: () => {
                          setPasteJsonOpen(true)
                        },
                      },
                      { type: 'divider' },
                      {
                        key: 'edit-eg',
                        label: '查看/编辑剧集组',
                        onClick: () => {
                          handleOpenEditEg()
                        },
                      },
                    ],
                  }}
                  trigger={['click']}
                  placement="bottomRight"
                >
                  <MenuOutlined
                    className="cursor-pointer opacity-60 transition-all hover:opacity-100"
                    style={{ fontSize: 14 }}
                  />
                </Dropdown>
              }
            />
          </Form.Item>
          <Form.Item name="bangumiId" label="BGM ID">
            <Input.Search
              placeholder="例如：296100"
              allowClear
              enterButton="搜索"
              suffix={
                <Tooltip title="ID直搜">
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'bangumi',
                        currentId: bangumiId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchBgmLoading}
              onSearch={() => {
                onBgmSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="tvdbId" label="TVDB ID">
            <Input.Search
              placeholder="例如：364093"
              allowClear
              enterButton="搜索"
              suffix={
                <Tooltip title="ID直搜">
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'tvdb',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: tvdbId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchTvdbLoading}
              onSearch={() => {
                onTvdbSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="doubanId" label="豆瓣ID">
            <Input.Search
              placeholder="例如：35297708"
              allowClear
              enterButton="搜索"
              suffix={
                <Tooltip title="ID直搜">
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'douban',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: doubanId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchDoubanLoading}
              onSearch={() => {
                onDoubanSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="imdbId" label="IMDB ID">
            <Input.Search
              placeholder="例如：tt9140554"
              allowClear
              enterButton="搜索"
              suffix={
                <Tooltip title="ID直搜">
                  <span
                    className="cursor-pointer opacity-80 transition-all hover:opacity-100"
                    onClick={() => {
                      handleSearchAsId({
                        source: 'imdb',
                        mediaType:
                          type === DANDAN_TYPE_MAPPING.tvseries
                            ? 'series'
                            : 'movie',
                        currentId: imdbId,
                      })
                    }}
                  >
                    <MyIcon icon="jingzhun" size={20} />
                  </span>
                </Tooltip>
              }
              loading={searchImdbLoading}
              onSearch={() => {
                onImdbSearch()
              }}
            />
          </Form.Item>
          <ApplyField
            name="nameEn"
            label="英文名"
            fetchedValue={fetchedMetadata?.nameEn}
            form={form}
          />
          <ApplyField
            name="nameJp"
            label="日文名"
            fetchedValue={
              containsJapanese(fetchedMetadata?.nameJp)
                ? fetchedMetadata.nameJp
                : null
            }
            form={form}
          />
          <ApplyField
            name="nameRomaji"
            label="罗马音"
            fetchedValue={fetchedMetadata?.nameRomaji}
            form={form}
          />
          <ApplyField
            name="aliasCn1"
            label="中文别名1"
            fetchedValue={fetchedMetadata?.aliasesCn?.[0]}
            form={form}
          />
          <ApplyField
            name="aliasCn2"
            label="中文别名2"
            fetchedValue={fetchedMetadata?.aliasesCn?.[1]}
            form={form}
          />
          <ApplyField
            name="aliasCn3"
            label="中文别名3"
            fetchedValue={fetchedMetadata?.aliasesCn?.[2]}
            form={form}
          />
          <Form.Item
            name="aliasLocked"
            label={
              <Space>
                <span>锁定别名</span>
                <Tooltip title="锁定后,TMDB自动刮削任务将不会自动更新此作品的别名信息">
                  <QuestionCircleOutlined />
                </Tooltip>
              </Space>
            }
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="animeId" hidden>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 TMDB ID`}
        open={tmdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setTmdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tmdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="text-sm mt-2 line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'tmdb',
                          mediaType: type === 'tv_series' ? 'tv' : type,
                          currentId: item.id,
                        })
                        form.setFieldsValue({ tmdbId: res.data.id })

                        setFetchedMetadata(res.data)
                        setTmdbOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 IMDB ID`}
        open={imdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setImdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={imdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'imdb',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ imdbId: res.data.id })

                        setFetchedMetadata(res.data)
                        setImdbOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 TVDB ID`}
        open={tvdbOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setTvdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tvdbResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'tvdb',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ tvdbId: res.data.id })
                        setFetchedMetadata(res.data)
                        setTvdbOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 剧集组 ID`}
        open={egidOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setEgidOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={egidResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div>
                    <div className="text-xl font-bold mb-3">
                      {item.name} ({item.groupCount} 组, {item.episodeCount} 集)
                    </div>
                    <div>{item.description || '无描述'}</div>
                  </div>
                  <div className="flex item-center justify-center gap-2">
                    <Button
                      type="primary"
                      size="small"
                      onClick={() => {
                        form.setFieldsValue({
                          tmdbEpisodeGroupId: item.id,
                        })
                        setEgidOpen(false)
                      }}
                    >
                      应用此组
                    </Button>
                    <Button
                      type="default"
                      size="small"
                      loading={searchAllEpisodeLoading}
                      onClick={() => handleAllEpisode(item)}
                    >
                      查看分集
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`分集详情 ${allEpisode.name}`}
        open={episodeOpen}
        footer={null}
        zIndex={120}
        onCancel={() => setEpisodeOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={allEpisode?.groups || []}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="text-base font-bold mb-2">
                  {item.name} (Order: {item.order})
                </div>
                {item.episodes?.map((ep, i) => {
                  // 计算绝对集数显示格式
                  // 特别季(season_number=0): S00EXX
                  // 正片(season_number=1): S01EXX (使用episode_number作为绝对序号)
                  const seasonNum = ep.season_number || ep.seasonNumber || 0
                  const episodeNum = ep.episode_number || ep.episodeNumber || (ep.order + 1)
                  const absoluteDisplay = `S${String(seasonNum).padStart(2, '0')}E${String(episodeNum).padStart(2, '0')}`

                  return (
                    <div key={i}>
                      第{ep.order + 1}集（绝对：{absoluteDisplay}）|
                      {ep.name || '无标题'}
                    </div>
                  )
                })}
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 BGM ID`}
        open={bgmOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setBgmOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={bgmResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="text-sm mt-2 line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'bangumi',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ bangumiId: res.data.id })
                        setFetchedMetadata(res.data)
                        setBgmOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      <Modal
        title={`为 "${title}" 搜索 豆瓣 ID`}
        open={doubanOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setDoubanOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={doubanResult}
          pagination={{
            pageSize: 4,
            showSizeChanger: false,
            hideOnSinglePage: true,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.title || item.name}
                      </div>
                      <div>ID: {item.id}</div>
                      {!!item.details && (
                        <div className="mt-2 text-sm line-clamp-4">
                          {item.details}
                        </div>
                      )}
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getAnimeInfoAsSource({
                          source: 'douban',
                          currentId: item.id,
                        })
                        form.setFieldsValue({ doubanId: res.data.id })
                        setFetchedMetadata(res.data)
                        setDoubanOpen(false)
                      }}
                    >
                      选择
                    </Button>
                  </div>
                </div>
              </List.Item>
            )
          }}
        />
      </Modal>
      {/* 源选择弹窗 */}
      <Modal
        title={`${sourceSelectAction === 'favorite' ? '选择要标记的源' : '选择要追更的源'} - ${sourceSelectTitle}`}
        open={sourceSelectOpen}
        onOk={handleSourceSelectConfirm}
        onCancel={() => setSourceSelectOpen(false)}
        okText="确认"
        cancelText="取消"
        zIndex={110}
      >
        <div className="py-4">
          <Radio.Group
            value={selectedSourceId}
            onChange={(e) => setSelectedSourceId(e.target.value)}
            className="w-full"
          >
            <Space direction="vertical" className="w-full">
              {sourceSelectSources.map((source) => (
                <Radio key={source.sourceId} value={source.sourceId} className="w-full">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{source.providerName}</span>
                    {source.isFavorited && (
                      <Tag color="gold" className="ml-2">已标记</Tag>
                    )}
                    {source.incrementalRefreshEnabled && (
                      <Tag color="green" className="ml-2">追更中</Tag>
                    )}
                  </div>
                </Radio>
              ))}
            </Space>
          </Radio.Group>
          <div className="mt-4 text-gray-500 text-sm">
            {sourceSelectAction === 'favorite'
              ? '提示：每个作品只能有一个标记的源，选择后其他源的标记会被取消。'
              : '提示：每个作品只能有一个追更的源，选择后其他源的追更会被取消。'
            }
          </div>
        </div>
      </Modal>
      {/* 本地剧集组预览 Modal */}
      <Modal
        title="本地剧集组预览"
        open={localEgOpen}
        footer={null}
        zIndex={110}
        onCancel={() => {
          setLocalEgOpen(false)
          setLocalEgParsedData(null)
        }}
        width={600}
      >
        {localEgParsedData && (
          <div>
            <Card size="small" title={
              <span>
                共 {localEgParsedData.groups?.length || 0} 组，
                {localEgParsedData.groups?.reduce((sum, g) => sum + (g.episodes?.length || 0), 0)} 集
              </span>
            }>
              {localEgParsedData.description && (
                <Typography.Paragraph type="secondary" ellipsis={{ rows: 2, expandable: true }}>
                  {localEgParsedData.description}
                </Typography.Paragraph>
              )}
              <Collapse
                size="small"
                items={localEgParsedData.groups?.map((g, i) => ({
                  key: i,
                  label: `${g.name || `组 ${i + 1}`} (${g.episodes?.length || 0} 集)`,
                  children: (
                    <div className="max-h-40 overflow-y-auto text-sm">
                      {g.episodes?.map((ep, j) => (
                        <div key={j}>
                          S{String(ep.season_number ?? 0).padStart(2, '0')}
                          E{String(ep.episode_number ?? 0).padStart(2, '0')}
                          {' → '}Order: {ep.order}
                        </div>
                      ))}
                    </div>
                  ),
                }))}
              />
            </Card>
            <Button
              type="primary"
              className="mt-3 w-full"
              loading={localEgApplyLoading}
              onClick={handleLocalEgApply}
            >
              应用此剧集组
            </Button>
          </div>
        )}
      </Modal>
      {/* 查询本地JSON路径 Modal */}
      <Modal
        title="查询服务端剧集组 JSON"
        open={localPathOpen}
        onOk={handleLocalPathConfirm}
        confirmLoading={localPathLoading}
        okText="获取"
        cancelText="取消"
        zIndex={110}
        onCancel={() => {
          setLocalPathOpen(false)
          setLocalPathValue('')
        }}
      >
        <div className="text-gray-500 text-sm mb-3">
          输入弹幕库服务端的本地文件路径或网络URL，服务端将读取并解析 JSON 文件。
        </div>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            placeholder="例如：/path/to/episodegroup.json 或 https://..."
            value={localPathValue}
            onChange={(e) => setLocalPathValue(e.target.value)}
            onPressEnter={handleLocalPathConfirm}
          />
          <Button
            icon={<FolderOpenOutlined />}
            onClick={() => setFileBrowserOpen(true)}
            title="浏览服务端文件"
          />
        </Space.Compact>
      </Modal>
      {/* 服务端文件浏览器（选择JSON文件） */}
      <DirectoryBrowser
        visible={fileBrowserOpen}
        onClose={() => setFileBrowserOpen(false)}
        onSelect={(path) => {
          setLocalPathValue(path)
          setFileBrowserOpen(false)
        }}
        selectMode="file"
        fileFilter=".json"
      />
      {/* 粘贴JSON Modal */}
      <Modal
        title="粘贴剧集组 JSON"
        open={pasteJsonOpen}
        onOk={handlePasteJsonConfirm}
        okText="确认"
        cancelText="取消"
        zIndex={110}
        onCancel={() => {
          setPasteJsonOpen(false)
          setPasteJsonValue('')
        }}
      >
        <Input.TextArea
          rows={12}
          placeholder='请粘贴 StrmAssistant 格式的剧集组 JSON，需包含 "groups" 数组'
          value={pasteJsonValue}
          onChange={(e) => setPasteJsonValue(e.target.value)}
        />
      </Modal>
      {/* 查看/编辑剧集组 Modal */}
      <Modal
        title={`查看/编辑剧集组 ${editEgData?.id || ''}`}
        open={editEgOpen}
        width={700}
        zIndex={110}
        onCancel={() => { setEditEgOpen(false); setEditEgData(null) }}
        footer={
          <div className="flex justify-end gap-2">
            <Button onClick={() => { setEditEgOpen(false); setEditEgData(null) }}>取消</Button>
            <Button type="primary" loading={editEgSaving} onClick={handleSaveEditEg}>
              保存修改
            </Button>
          </div>
        }
      >
        {editEgData && (
          <div className="max-h-[60vh] overflow-y-auto">
            <div className="flex gap-3 mb-4">
              <div className="flex-1">
                <div className="text-xs text-gray-500 mb-1">剧集组名称</div>
                <Input
                  value={editEgData.name}
                  onChange={e => updateEditEgField(['name'], e.target.value)}
                  placeholder="剧集组名称"
                />
              </div>
              <div className="flex-1">
                <div className="text-xs text-gray-500 mb-1">描述</div>
                <Input
                  value={editEgData.description}
                  onChange={e => updateEditEgField(['description'], e.target.value)}
                  placeholder="描述"
                />
              </div>
            </div>
            <Collapse
              size="small"
              defaultActiveKey={editEgData.groups.map((_, i) => i)}
              items={editEgData.groups.map((group, gi) => ({
                key: gi,
                label: (
                  <div className="flex items-center gap-2 w-full">
                    <span className="font-bold">{group.name || `组 ${gi + 1}`}</span>
                    <Tag>{group.episodes.length} 集</Tag>
                    <span className="text-xs text-gray-400">Order: {group.order}</span>
                  </div>
                ),
                extra: (
                  <Button
                    type="text" danger size="small"
                    onClick={e => { e.stopPropagation(); removeEgGroup(gi) }}
                  >
                    删除组
                  </Button>
                ),
                children: (
                  <div>
                    <div className="flex gap-2 mb-2">
                      <Input
                        size="small" placeholder="组名"
                        value={group.name}
                        onChange={e => updateEditEgField(['groups', gi, 'name'], e.target.value)}
                        style={{ width: 150 }}
                      />
                      <InputNumber
                        size="small" placeholder="Order"
                        value={group.order} min={0}
                        onChange={v => updateEditEgField(['groups', gi, 'order'], v ?? 0)}
                        style={{ width: 90 }}
                      />
                    </div>
                    <Table
                      size="small" pagination={false}
                      dataSource={group.episodes}
                      rowKey={(_, i) => i}
                      columns={[
                        {
                          title: '季', dataIndex: 'seasonNumber', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'seasonNumber'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: '集', dataIndex: 'episodeNumber', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'episodeNumber'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: '顺序', dataIndex: 'order', width: 70,
                          render: (v, _, ei) => (
                            <InputNumber size="small" value={v} min={0}
                              onChange={val => updateEditEgField(['groups', gi, 'episodes', ei, 'order'], val ?? 0)}
                              style={{ width: '100%' }}
                            />
                          ),
                        },
                        {
                          title: '标题', dataIndex: 'name',
                          render: (v, _, ei) => (
                            <Input size="small" value={v}
                              onChange={e => updateEditEgField(['groups', gi, 'episodes', ei, 'name'], e.target.value)}
                            />
                          ),
                        },
                        {
                          title: '', width: 50,
                          render: (_, __, ei) => (
                            <Button type="text" danger size="small"
                              onClick={() => removeEgEpisode(gi, ei)}
                            >
                              删除
                            </Button>
                          ),
                        },
                      ]}
                    />
                    <Button size="small" type="dashed" className="mt-2 w-full"
                      onClick={() => addEgEpisode(gi)}
                    >
                      + 添加分集
                    </Button>
                  </div>
                ),
              }))}
            />
            <Button type="dashed" className="mt-3 w-full" onClick={addEgGroup}>
              + 添加组
            </Button>
          </div>
        )}
      </Modal>

      {/* 海报搜索弹窗 */}
      <PosterSearchModal
        visible={posterSearchVisible}
        onClose={() => setPosterSearchVisible(false)}
        onSelect={(posterUrl) => {
          form.setFieldsValue({ imageUrl: posterUrl })
          messageApi.success('已填入海报URL')
        }}
        defaultKeyword={title || ''}
        tmdbId={tmdbId}
        tvdbId={tvdbId}
        mediaType={type === 'movie' ? 'movie' : 'tv'}
      />

      {/* 本地海报预览 */}
      {previewVisible && localImagePath && (
        <Image
          style={{ display: 'none' }}
          preview={{
            visible: previewVisible,
            src: localImagePath,
            onVisibleChange: (vis) => setPreviewVisible(vis),
          }}
        />
      )}
    </div>
  )
}

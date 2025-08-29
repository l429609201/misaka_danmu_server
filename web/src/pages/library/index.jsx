import React, { useEffect, useState } from 'react'
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
  Select,
  Space,
  Table,
  Tooltip,
} from 'antd'
import {
  deleteAnime,
  getAllEpisode,
  getAnimeDetail,
  getAnimeInfoAsSource,
  getAnimeLibrary,
  getBgmSearch,
  getDoubanSearch,
  getEgidSearch,
  getImdbSearch,
  getTmdbSearch,
  getTvdbSearch,
  refreshPoster,
  setAnimeDetail,
} from '../../apis'
import { MyIcon } from '@/components/MyIcon'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../configs'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { RoutePaths } from '../../general/RoutePaths'
import { padStart } from 'lodash'

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
  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [renderData, setRenderData] = useState([])
  const [keyword, setKeyword] = useState('')
  const navigate = useNavigate()
  const [libraryPageSize, setLibraryPageSize] = useState(50)

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
  const [fetchedMetadata, setFetchedMetadata] = useState(null)

  const getList = async () => {
    try {
      setLoading(true)
      const res = await getAnimeLibrary()
      setList(res.data?.animes || [])
      setRenderData(res.data?.animes || [])
    } catch (error) {
      setList([])
      setRenderData([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    getList()
  }, [])

  useEffect(() => {
    setRenderData(list?.filter(it => it.title.includes(keyword)) || [])
  }, [list, keyword])

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
        return imageSrc ? <img src={imageSrc} className="w-12" /> : null
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
      width: 80,
    },
    {
      title: '集数',
      dataIndex: 'episodeCount',
      key: 'episodeCount',
      width: 50,
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
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: '操作',
      width: 100,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <Tooltip title="编辑影视信息">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={async () => {
                  const res = await getAnimeDetail({
                    animeId: record.animeId,
                  })
                  form.setFieldsValue({
                    ...(res.data || {}),
                    animeId: record.animeId,
                  })
                  setEditOpen(true)
                }}
              >
                <MyIcon icon="edit" size={20}></MyIcon>
              </span>
            </Tooltip>

            <Tooltip title="详情">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
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

  const handleDelete = async record => {
    Modal.confirm({
      title: '删除',
      zIndex: 1002,
      content: (
        <div>
          确定要删除{record.name}吗？
          <br />
          此操作将在后台提交一个删除任务
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnime({ animeId: record.animeId })
          goTask(res)
        } catch (error) {
          message.error('提交删除任务失败')
        }
      },
    })
  }

  const goTask = res => {
    Modal.confirm({
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
      message.success('信息更新成功')
    } catch (error) {
      message.error(error.detail || '编辑失败')
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
      message.success(
        `${source.toUpperCase()} 信息获取成功，请检查并应用建议的别名。`
      )
    } catch (error) {
      message.error(`获取 ${source.toUpperCase()} 详情失败: ${error.message}`)
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
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(`TMDB搜索失败:${error.message}`)
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
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(`TVDB搜索失败:${error.message}`)
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
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(`豆瓣搜索失败:${error.message}`)
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
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(
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

  const onEgidSearch = async () => {
    try {
      if (searchEgidLoading) return
      setSearchEgidLoading(true)
      const res = await getEgidSearch({
        tmdbId: tmdbId,
        keyword: title,
      })
      if (!!res?.data?.length) {
        setEgidResult(res?.data || [])
        setEgidOpen(true)
      } else {
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(`剧集组搜索失败:${error.message}`)
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
        message.error('没有找到相关分集')
      }
    } catch (error) {
      message.error('没有找到相关分集')
    } finally {
      setSearchAllEpisodeLoading(false)
    }
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
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error(`BGM搜索失败:${error.message}`)
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
          <>
            <Input
              placeholder="搜索已收录的影视"
              onChange={e => setKeyword(e.target.value)}
            />
          </>
        }
      >
        {!!renderData?.length ? (
          <Table
            pagination={
              renderData?.length > 50
                ? {
                    pageSize: libraryPageSize,
                    showTotal: total => `共 ${total} 条数据`,
                    showSizeChanger: true,
                    showQuickJumper: true,
                    onShowSizeChange: (_, size) => {
                      setLibraryPageSize(size)
                    },
                  }
                : null
            }
            size="small"
            dataSource={renderData}
            columns={columns}
            rowKey={'animeId'}
            scroll={{ x: '100%' }}
          />
        ) : (
          <Empty />
        )}
      </Card>
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
          <Form.Item name="imageUrl" label="海报URL">
            <Input
              addonAfter={
                <div
                  className="cursor-pointer"
                  onClick={async () => {
                    try {
                      await refreshPoster({
                        animeId,
                        imageUrl: imageUrl,
                      })
                      message.success('海报已刷新并缓存成功！')
                    } catch (error) {
                      message.error(`刷新海报失败: ${error.message}`)
                    }
                  }}
                >
                  <MyIcon icon="refresh" size={20} />
                </div>
              }
            />
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
              placeholder="TMDB Episode Group Id"
              allowClear
              enterButton="搜索"
              loading={searchEgidLoading}
              onSearch={() => {
                onEgidSearch()
              }}
              disabled={type === DANDAN_TYPE_MAPPING.movie || !tmdbId}
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
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="text-base font-bold mb-2">
                  {item.name} (Order: {item.order})
                </div>
                {item.episodes?.map((ep, i) => {
                  return (
                    <div key={i}>
                      第{ep.order + 1}集（绝对：S
                      {padStart(ep.seasonNumber, 2, '0')}E
                      {padStart(ep.episodeNumber, 2, '0')}）|
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
    </div>
  )
}

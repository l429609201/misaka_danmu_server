import {
  getEditEpisodes,
  getInLibraryEpisodes,
  getTmdbSearch,
  importDanmu,
  importEdit,
} from '../../../apis'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  Col,
  List,
  message,
  Checkbox,
  Row,
  Tag,
  Input,
  Modal,
  Radio,
  Form,
  Empty,
  InputNumber,
} from 'antd'
import { useAtom } from 'jotai'
import { lastSearchResultAtom, searchLoadingAtom } from '../../../../store'
import { CheckOutlined, CloseCircleOutlined } from '@ant-design/icons'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../../configs'
import { useWatch } from 'antd/es/form/Form'

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

const IMPORT_MODE = [
  {
    key: 'separate',
    label: '作为多个独立条目导入',
  },
  {
    key: 'merge',
    label: '统一导入为单个条目',
  },
]

export const SearchResult = () => {
  const [form] = Form.useForm()
  const title = useWatch('title', form)
  const tmdbid = useWatch('tmdbid', form)
  const [tmdbList, setTmdbResult] = useState([])
  const [searchTmdbLoading, setSearchTmdbLoading] = useState(false)
  const [tmdbOpen, setTmdbOpen] = useState(false)

  const [searchLoading] = useAtom(searchLoadingAtom)
  const [lastSearchResultData] = useAtom(lastSearchResultAtom)

  const [selectList, setSelectList] = useState([])

  /** 编辑导入相关 */
  const [editImportOpen, setEditImportOpen] = useState(false)
  const [editEpisodeList, setEditEpisodeList] = useState([])
  const [editLoading, setEditLoading] = useState(false)
  const [editItem, setEditItem] = useState({})
  const [editAnimeTitle, setEditAnimeTitle] = useState('')
  const [activeItem, setActiveItem] = useState(null)
  const dragOverlayRef = useRef(null)
  const [editConfirmLoading, setEditConfirmLoading] = useState(false)
  const [range, setRange] = useState([1, 1])
  const [episodePageSize, setEpisodePageSize] = useState(10)

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

  const searchSeason = lastSearchResultData?.season

  const [loading, setLoading] = useState(false)

  const [batchOpen, setBatchOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)

  /** 导入模式 */
  const [importMode, setImportMode] = useState(IMPORT_MODE[0].key)

  /** 筛选条件 */
  const [checkedList, setCheckedList] = useState([
    DANDAN_TYPE_MAPPING.movie,
    DANDAN_TYPE_MAPPING.tvseries,
  ])

  const [keyword, setKeyword] = useState('')

  /** 渲染使用的数据 */
  const [renderData, setRenderData] = useState(
    lastSearchResultData.results || []
  )

  const importModeText = useMemo(() => {
    const uniqueTitles = new Set(selectList.map(item => item.title))
    if (uniqueTitles.size === 1) {
      setImportMode('merge')
      return `您选择了 ${selectList.length} 个标题相同的条目。请确认导入模式。`
    } else {
      setImportMode('separate')
      return `检测到您选择的媒体标题不一致。请指定导入模式。`
    }
  }, [selectList])

  useEffect(() => {
    form.setFieldsValue({
      title: selectList?.[0]?.title?.split?.(' ')?.[0],
      tmdbid: null,
    })
  }, [selectList])

  useEffect(() => {
    const list = lastSearchResultData.results
      ?.filter(it => it.title.includes(keyword))
      ?.filter(it => checkedList.includes(it.type))
    console.log(
      keyword,
      checkedList,
      lastSearchResultData.results,
      list,
      'list'
    )
    setRenderData(list)
  }, [keyword, checkedList, lastSearchResultData])

  const onTypeChange = values => {
    console.log(values, 'values')
    setCheckedList(values)
  }

  const handleImportDanmu = async item => {
    try {
      if (loading) return
      setLoading(true)
      const res = await importDanmu({
        provider: item.provider,
        mediaId: item.mediaId,
        animeTitle: item.title,
        type: item.type,
        // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
        // 否则，使用从单个结果中解析出的季度
        season: searchSeason ?? item.season,
        year: item.year, // 新增年份
        imageUrl: item.imageUrl,
        doubanId: item.doubanId,
        currentEpisodeIndex: item.currentEpisodeIndex,
      })
      message.success(res.data.message || '导入成功')
    } catch (error) {
      message.error(`提交导入任务失败: ${error.detail || error}`)
    } finally {
      setLoading(false)
    }
  }

  const handleImportEdit = async () => {
    try {
      if (editConfirmLoading) return
      setEditConfirmLoading(true)
      const res = await importEdit(
        JSON.stringify({
          provider: editItem.provider,
          mediaId: editItem.mediaId,
          animeTitle: editItem.title,
          mediaType: editItem.type,
          // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
          // 否则，使用从单个结果中解析出的季度
          season: searchSeason !== null ? searchSeason : editItem.season,
          year: editItem.year, // 新增年份
          imageUrl: editItem.imageUrl,
          doubanId: editItem.doubanId,
          currentEpisodeIndex: editItem.currentEpisodeIndex,
          ...editItem,
          episodes: editEpisodeList ?? [],
        })
      )
      message.success(res.data?.message || '编辑导入任务已提交。')
    } catch (error) {
      message.error(`提交导入任务失败: ${error.message}`)
    } finally {
      setEditConfirmLoading(false)
      setEditImportOpen(false)
      setEditEpisodeList([])
      setEditItem({})
      setEditAnimeTitle('')
    }
  }

  const handleBatchImport = () => {
    let tmdbparams = {}
    if (importMode === 'merge') {
      if (!title) {
        message.error('最终导入名称不能为空。')
        return
      }
      tmdbparams = {
        tmdbId: `${tmdbid}`,
      }
    }
    Modal.confirm({
      title: '批量导入',
      zIndex: 1002,
      content: (
        <div>
          确定要将 {selectList.length} 个条目
          {importMode === 'merge' ? '合并' : '分开'}导入吗？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          setConfirmLoading(true)
          await Promise.all(
            selectList.map(item => {
              console.log(item, '1')
              return importDanmu(
                JSON.stringify({
                  provider: item.provider,
                  mediaId: item.mediaId,
                  type: item.type,
                  season: item.season,
                  year: item.year,
                  imageUrl: item.imageUrl,
                  doubanId: item.doubanId,
                  currentEpisodeIndex: item.currentEpisodeIndex,
                  animeTitle: title ?? item.title,
                  ...tmdbparams,
                })
              )
            })
          )
          message.success('批量导入任务已提交，请在任务管理器中查看进度。')
          setSelectList([])
          setConfirmLoading(false)
          setBatchOpen(false)
        } catch (err) {
        } finally {
          setConfirmLoading(false)
          setBatchOpen(false)
        }
      },
    })
  }

  const onTmdbSearch = async () => {
    try {
      if (searchTmdbLoading) return
      setSearchTmdbLoading(true)
      const res = await getTmdbSearch({
        keyword: title,
        mediaType:
          selectList?.[0]?.type === DANDAN_TYPE_MAPPING.tvseries
            ? 'tv'
            : 'movie',
      })
      if (!!res?.data?.length) {
        setTmdbResult(res?.data || [])
        setTmdbOpen(true)
      } else {
        message.error('没有找到相关内容')
      }
    } catch (error) {
      message.error('TMDB搜索失败')
    } finally {
      setSearchTmdbLoading(false)
    }
  }

  const handleDragEnd = event => {
    const { active, over } = event
    // 拖拽无效或未改变位置
    if (!over || active.id === over.id) {
      setActiveItem(null)
      return
    }

    // 找到原位置和新位置

    setEditEpisodeList(list => {
      const activeIndex = list.findIndex(
        item => item.episodeId === active.data.current.item.episodeId
      )
      const overIndex = list.findIndex(
        item => item.episodeId === over.data.current.item.episodeId
      )

      if (activeIndex !== -1 && overIndex !== -1) {
        // 1. 重新排列数组
        const newList = [...editEpisodeList]
        const [movedItem] = newList.splice(activeIndex, 1)
        newList.splice(overIndex, 0, movedItem)

        // // 2. 重新计算所有项的display_order（从1开始连续编号）
        // const updatedList = newList.map((item, index) => ({
        //   ...item,
        //   episodeIndex: index + 1, // 排序值从1开始
        // }))

        return newList
      }
      return list
    })

    setActiveItem(null)
  }

  // 处理拖拽开始
  const handleDragStart = event => {
    const { active } = event
    // 找到当前拖拽的项
    const item = editEpisodeList.find(item => item.episodeId === active.id)
    setActiveItem(item)
  }

  const handleDelete = item => {
    // 3. 更新状态
    setEditEpisodeList(list => {
      const activeIndex = list.findIndex(o => o.episodeId === item.episodeId)
      const newList = [...list]
      newList.splice(activeIndex, 1)

      // const updatedList = newList.map((item, index) => ({
      //   ...item,
      //   episodeIndex: index + 1, // 排序值从1开始
      // }))
      return newList
    })
  }

  const handleEditTitle = (item, value) => {
    setEditEpisodeList(list => {
      return list.map(it => {
        if (it.episodeId === item.episodeId) {
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

  const handleEditIndex = (item, value) => {
    setEditEpisodeList(list => {
      return list.map(it => {
        if (it.episodeId === item.episodeId) {
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
            <div>
              <MyIcon icon="drag" size={24} />
            </div>
            <div className="w-full flex items-center justify-between gap-3">
              <div>{activeItem.episodeIndex}</div>
              <Input
                style={{
                  width: '100%',
                }}
                value={activeItem.title}
                onChange={e => {}}
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

  return (
    <div className="my-4">
      <Card title="搜索结果" loading={searchLoading}>
        <div>
          <Row gutter={[12, 12]} className="mb-6">
            <Col md={20} xs={24}>
              <div className="flex items-center justify-start flex-wrap md:flex-nowrap gap-4">
                <Button
                  type="primary"
                  className="w-32"
                  onClick={() => {
                    setSelectList(list =>
                      list.length === renderData.length ? [] : renderData
                    )
                  }}
                  disabled={!renderData.length}
                >
                  {selectList.length === renderData.length && renderData.length
                    ? '取消全选'
                    : '全选'}
                </Button>
                <Checkbox.Group
                  className="shrink-0"
                  options={[
                    {
                      label: '电影/剧场版',
                      value: DANDAN_TYPE_MAPPING.movie,
                    },
                    {
                      label: '电视节目',
                      value: DANDAN_TYPE_MAPPING.tvseries,
                    },
                  ]}
                  value={checkedList}
                  onChange={onTypeChange}
                />
                <div className="w-full md:w-40">
                  <Input
                    placeholder="在结果中过滤标题"
                    onChange={e => setKeyword(e.target.value)}
                  />
                </div>
              </div>
            </Col>
            <Col md={4} xs={24}>
              <Button
                block
                type="primary"
                onClick={() => {
                  if (selectList.length === 0) {
                    message.error('请选择要导入的媒体')
                    return
                  }

                  setBatchOpen(true)
                }}
              >
                批量导入
              </Button>
            </Col>
          </Row>
          {!!renderData?.length ? (
            <List
              itemLayout="vertical"
              size="large"
              dataSource={renderData}
              renderItem={(item, index) => {
                const isActive = selectList.includes(item)
                return (
                  <List.Item key={index}>
                    <Row gutter={[12, 12]}>
                      <Col md={16} xs={24}>
                        <div
                          className="flex items-center justify-start relative cursor-pointer"
                          onClick={() =>
                            setSelectList(list => {
                              return list.includes(item)
                                ? list.filter(i => i !== item)
                                : [...list, item]
                            })
                          }
                        >
                          <Checkbox checked={isActive} />
                          <img
                            width={60}
                            alt="logo"
                            src={item.imageUrl}
                            className="ml-3"
                          />
                          <div className="ml-4">
                            <div className="text-xl font-bold mb-3">
                              {item.title}
                            </div>
                            <div className="flex items-center flex-wrap gap-2">
                              <Tag color="magenta">
                                源：{item.provider ?? '未知'}
                              </Tag>
                              <Tag color="red">
                                {DANDAN_TYPE_DESC_MAPPING[item.type]}
                              </Tag>
                              <Tag color="volcano">
                                年份：{item.year ?? '未知'}
                              </Tag>
                              <Tag color="orange">
                                季度：{item.season ?? '未知'}
                              </Tag>
                              <Tag color="gold">
                                总集数：{item.episodeCount ?? 0}
                              </Tag>
                            </div>
                          </div>
                        </div>
                      </Col>
                      <Col md={4} xs={12}>
                        <Button
                          block
                          type="default"
                          className="mt-3"
                          loading={editLoading}
                          onClick={async () => {
                            try {
                              if (editLoading) return
                              setEditLoading(true)
                              const res = await getEditEpisodes({
                                provider: item.provider,
                                media_id: item.mediaId,
                                media_type: item.type,
                              })
                              setEditEpisodeList(res.data)
                              setEditImportOpen(true)
                              setEditItem(item)
                            } catch (error) {
                            } finally {
                              setEditLoading(false)
                            }
                          }}
                        >
                          编辑导入
                        </Button>
                      </Col>
                      <Col md={4} xs={12}>
                        <Button
                          block
                          loading={loading}
                          type="primary"
                          className="mt-3"
                          onClick={() => {
                            handleImportDanmu(item)
                          }}
                        >
                          导入弹幕
                        </Button>
                      </Col>
                    </Row>
                  </List.Item>
                )
              }}
            />
          ) : (
            <Empty description="暂无搜索结果" />
          )}
        </div>
      </Card>
      <Modal
        title="批量导入确认"
        open={batchOpen}
        onOk={handleBatchImport}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setBatchOpen(false)}
      >
        <div>
          <div className="mb-2">{importModeText}</div>
          <div className="text-base mb-2 font-bold">已选择的条目</div>
          <div className="max-h-[300px] overflow-y-auto">
            {selectList.map((item, index) => {
              return (
                <div
                  key={index}
                  className="my-3 p-2 rounded-xl border-gray-300/45 border"
                >
                  <div className="text-xl font-bold mb-2">{item.title}</div>
                  <div className="flex items-center flex-wrap gap-2">
                    <Tag color="magenta">源：{item.provider}</Tag>
                    <Tag color="red">
                      类型：{DANDAN_TYPE_DESC_MAPPING[item.type]}
                    </Tag>
                    <Tag color="volcano">年份：{item.year}</Tag>
                    <Tag color="orange">季度：{item.season}</Tag>
                    <Tag color="gold">总集数：{item.episodeCount}</Tag>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="text-base my-3 font-bold">导入模式</div>
          <Radio.Group
            value={importMode}
            onChange={e => setImportMode(e.target.value)}
            className="!mb-4"
          >
            {IMPORT_MODE.map(item => (
              <Radio key={item.key} value={item.key}>
                {item.label}
              </Radio>
            ))}
          </Radio.Group>
          {importMode === 'merge' && (
            <Form form={form} layout="horizontal">
              <Form.Item
                name="title"
                label="最终导入名称"
                rules={[{ required: true, message: '请输入最终导入名称' }]}
              >
                <Input.Search
                  placeholder="请输入最终导入名称"
                  allowClear
                  enterButton="搜索"
                  loading={searchTmdbLoading}
                  onSearch={onTmdbSearch}
                />
              </Form.Item>
              <Form.Item name="tmdbid" label="最终TMDB ID">
                <Input disabled placeholder="从TMDB搜索选择后自动填充" />
              </Form.Item>
            </Form>
          )}
        </div>
      </Modal>
      <Modal
        title="批量导入搜索 TMDB ID"
        open={tmdbOpen}
        footer={null}
        onCancel={() => setTmdbOpen(false)}
      >
        <List
          itemLayout="vertical"
          size="large"
          dataSource={tmdbList}
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
                      onClick={() => {
                        form.setFieldsValue({
                          tmdbid: item.id,
                        })
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
        title={`编辑导入: ${editItem.title}`}
        open={editImportOpen}
        onOk={() => {
          handleImportEdit()
        }}
        confirmLoading={editConfirmLoading}
        cancelText="取消"
        okText="确认导入"
        onCancel={() => setEditImportOpen(false)}
      >
        <div className="flex item-wrap md:flex-nowrap justify-between items-center gap-3 my-6">
          <div className="shrink-0">作品标题:</div>
          <div className="w-full">
            <Input
              value={editAnimeTitle || editItem.title}
              placeholder="请输入作品标题"
              onChange={e => {
                setEditAnimeTitle(e.target.value)
              }}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <Button
              type="default"
              onClick={async () => {
                try {
                  const res = await getInLibraryEpisodes({
                    title: editAnimeTitle || editItem.title,
                    season: editItem.season ?? 1,
                  })
                  if (!res.data?.length) {
                    message.error(
                      `在弹幕库中未找到作品 "${editAnimeTitle || editItem.title}" 或该作品没有任何分集。`
                    )
                    return
                  }
                  setEditEpisodeList(list => {
                    return list.filter(
                      it => !(res.data ?? []).includes(it.episodeIndex)
                    )
                  })
                  const removedCount = editEpisodeList.reduce((total, item) => {
                    return (
                      total +
                      (res.data ?? []).includes(item.episodeIndex ? 1 : 0)
                    )
                  }, 0)

                  message.success(
                    `重整完成！根据库内记录，移除了 ${removedCount} 个已存在的分集。`
                  )
                } catch (error) {
                  message.error(`查询已存在分集失败: ${error.message}`)
                }
              }}
            >
              重整分集导入
            </Button>
          </div>
        </div>
        <div className="flex item-wrap md:flex-nowrap justify-between items-center gap-3 my-6">
          <div className="shrink-0">集数区间:</div>
          <div className="w-full flex items-center justify-between flex-wrap md:flex-nowrap gap-2">
            <div className="flex items-center justify-start gap-2">
              <span>从</span>
              <InputNumber
                value={range[0]}
                onChange={value => setRange(r => [value, r[1]])}
                min={1}
                max={range[1]}
                step={1}
                style={{
                  width: '100%',
                }}
              />
              <span>到</span>
              <InputNumber
                value={range[1]}
                onChange={value => setRange(r => [r[0], value])}
                min={range[0]}
                step={1}
                style={{
                  width: '100%',
                }}
              />
            </div>
            <Button
              type="primary"
              block
              onClick={() => {
                console.log(range)
                setEditEpisodeList(list => {
                  return list.filter(
                    it =>
                      it.episodeIndex >= range[0] && it.episodeIndex <= range[1]
                  )
                })
              }}
            >
              确认区间
            </Button>
          </div>
        </div>
        <div>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCorners}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={editEpisodeList.map(item => item.episodeId)}
              strategy={verticalListSortingStrategy}
            >
              <List
                itemLayout="vertical"
                size="large"
                pagination={{
                  pageSize: episodePageSize,
                  onShowSizeChange: (_, size) => {
                    setEpisodePageSize(size)
                  },
                }}
                dataSource={editEpisodeList}
                renderItem={(item, index) => (
                  <SortableItem
                    key={item.episodeId}
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
        </div>
      </Modal>
    </div>
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
    id: item.episodeId,
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
        <div {...listeners} style={{ cursor: 'grab' }}>
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
              console.log(e.target.value, 'e.target.value')
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

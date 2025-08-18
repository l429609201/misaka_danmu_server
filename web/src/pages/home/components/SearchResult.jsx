import { getTmdbSearch, importDanmu } from '../../../apis'
import { useEffect, useMemo, useState } from 'react'
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
} from 'antd'
import { useAtom } from 'jotai'
import { lastSearchResultAtom, searchLoadingAtom } from '../../../../store'
import { CheckOutlined } from '@ant-design/icons'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../../configs'
import { useWatch } from 'antd/es/form/Form'

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

  console.log(selectList, 'selectList')

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
      const res = await importDanmu(
        JSON.stringify({
          provider: item.provider,
          mediaId: item.mediaId,
          animeTitle: item.title,
          type: item.type,
          // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
          // 否则，使用从单个结果中解析出的季度
          season: searchSeason !== null ? searchSeason : item.season,
          imageUrl: item.imageUrl,
          doubanId: item.doubanId,
          currentEpisodeIndex: item.currentEpisodeIndex,
        })
      )
      message.success(res.data.message || '导入成功')
    } catch (error) {
      message.error(`提交导入任务失败: ${error.detail || error}`)
    } finally {
      setLoading(false)
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

  return (
    <div className="my-4">
      <Card title="搜索结果" loading={searchLoading}>
        <div>
          <Row gutter={[12, 12]} className="mb-6">
            <Col md={20} xs={24}>
              <div className="flex items-center justify-start gap-4">
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
                <div className="w-40">
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
                    <Row gutter={12}>
                      <Col md={20} xs={24}>
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
                          <div className="shrink-0 mr-3 w-6 h-6 border-2 border-base-text rounded-full flex items-center justify-center">
                            {isActive && (
                              <CheckOutlined className="font-base font-bold" />
                            )}
                          </div>
                          <img width={60} alt="logo" src={item.imageUrl} />
                          <div className="ml-4">
                            <div className="text-xl font-bold mb-3">
                              {item.title}
                            </div>
                            <div className="flex items-center flex-wrap gap-2">
                              <Tag color="magenta">源：{item.provider}</Tag>
                              <Tag color="red">
                                类型：{DANDAN_TYPE_DESC_MAPPING[item.type]}
                              </Tag>
                              <Tag color="volcano">年份：{item.year}</Tag>
                              <Tag color="orange">季度：{item.season}</Tag>
                              <Tag color="gold">
                                总集数：{item.episodeCount}
                              </Tag>
                            </div>
                          </div>
                        </div>
                      </Col>
                      <Col md={4} xs={24}>
                        <Button
                          block
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
                  enterButton="Search"
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
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">{item.name}</div>
                      <div>ID: {item.id}</div>
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
    </div>
  )
}

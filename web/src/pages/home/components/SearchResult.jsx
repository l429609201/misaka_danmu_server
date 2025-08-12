import { importDanmu } from '../../../apis'
import { useEffect, useState } from 'react'
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
} from 'antd'
import { useAtom } from 'jotai'
import { lastSearchResultAtom } from '../../../../store'
import { CheckOutlined } from '@ant-design/icons'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../../configs'

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
  const [lastSearchResultData] = useAtom(lastSearchResultAtom)

  const [selectList, setSelectList] = useState([])

  console.log(selectList, 'selectList')

  const searchSeason = lastSearchResultData?.season

  const [loading, setLoading] = useState(false)
  const [batchLoading, setBatchLoading] = useState(false)

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

  useEffect(() => {
    setRenderData(lastSearchResultData.results || [])
  }, [lastSearchResultData])

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
          media_id: item.mediaId,
          anime_title: item.title,
          type: item.type,
          // 关键修正：如果用户搜索时指定了季度，则优先使用该季度
          // 否则，使用从单个结果中解析出的季度
          season: searchSeason !== null ? searchSeason : item.season,
          image_url: item.imageUrl,
          douban_id: item.douban_id,
          current_episode_index: item.currentEpisodeIndex,
        })
      )
      message.success(res.data.message || '导入成功')
    } catch (error) {
      message.error(`提交导入任务失败: ${error.message || error}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="my-4">
      <Card title="搜索结果">
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
                    className="rounded-lg border-gray-300 focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
                    onChange={e => setKeyword(e.target.value)}
                  />
                </div>
              </div>
            </Col>
            <Col md={4} xs={24}>
              <Button block type="primary" onClick={() => setBatchOpen(true)}>
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
            '暂无搜索结果'
          )}
        </div>
      </Card>
      <Modal
        title="批量导入确认"
        open={batchOpen}
        onOk={() => {}}
        confirmLoading={confirmLoading}
        cancelText="取消"
        okText="确认"
        onCancel={() => setBatchOpen(false)}
      >
        <div>
          <div className="mb-2">
            检测到您选择的媒体标题不一致。请指定一个统一的名称用于导入，或从TMDB搜索。
          </div>
          <div className="text-base font-bold">已选择的条目</div>
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
          <Radio.Group
            onChange={e => {
              console.log(e.target.value)
              setImportMode(e.target.value)
            }}
            options={IMPORT_MODE}
            defaultValue={importMode}
          ></Radio.Group>
        </div>
      </Modal>
    </div>
  )
}

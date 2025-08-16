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
} from 'antd'
import {
  deleteAnime,
  getAnimeDetail,
  getAnimeLibrary,
  getEgidSearch,
  getTMdbDetail,
  getTmdbSearch,
  setAnimeDetail,
} from '../../apis'
import { useEffect, useState } from 'react'
import { MyIcon } from '@/components/MyIcon'
import { DANDAN_TYPE_DESC_MAPPING, DANDAN_TYPE_MAPPING } from '../../configs'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { RoutePaths } from '../../general/RoutePaths'

export const Library = () => {
  const [loading, setLoading] = useState(true)
  const [list, setList] = useState([])
  const [renderData, setRenderData] = useState([])
  const [keyword, setKeyword] = useState('')
  const navigate = useNavigate()

  const [form] = Form.useForm()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const title = Form.useWatch('title', form)
  const tmdbId = Form.useWatch('tmdb_id', form)
  const type = Form.useWatch('type', form)

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

  const columns = [
    {
      title: '海报',
      dataIndex: 'imageUrl',
      key: 'imageUrl',
      width: 100,
      render: (_, record) => {
        return <img src={record.imageUrl} className="w-12" />
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
      width: 120,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
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
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                navigate(`/anime/${record.animeId}`)
              }}
            >
              <MyIcon icon="book" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                handleDelete(record)
              }}
            >
              <MyIcon icon="delete" size={20}></MyIcon>
            </span>
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
      title: '删除',
      zIndex: 1002,
      content: (
        <div>
          {res.message || '删除任务已提交'}
          <br />
          是否立即跳转到任务管理器查看进度？
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        navigate(`${RoutePaths.TASKstatus}?status=all`)
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
        tmdb_id: values.tmdb_id ? `${values.tmdb_id}` : null,
        tvdb_id: values.tmvb_id ? `${values.tmvb_id}` : null,
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

  /** 搜索相关 */
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
      message.error('TMDB搜索失败')
    } finally {
      setSearchTmdbLoading(false)
    }
  }

  const [egidResult, setEgidResult] = useState([])
  const [egidOpen, setEgidOpen] = useState(false)
  const [searchEgidLoading, setSearchEgidLoading] = useState(false)
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
      message.error('剧集组搜索失败')
    } finally {
      setSearchEgidLoading(false)
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
                    pageSize: 50,
                    showTotal: total => `共 ${total} 条数据`,
                    showSizeChanger: true,
                    showQuickJumper: true,
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
        onCancel={() => setEditOpen(false)}
        destroyOnHidden
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
          <Form.Item name="tmdb_id" label="TMDB ID">
            <Input.Search
              placeholder="例如：1396"
              allowClear
              enterButton="Search"
              loading={searchTmdbLoading}
              onSearch={() => {
                onTmdbSearch()
              }}
            />
          </Form.Item>
          <Form.Item name="tmdb_episode_group_id" label="剧集组ID">
            <Input.Search
              placeholder="TMDB Episode Group Id"
              allowClear
              enterButton="Search"
              loading={searchEgidLoading}
              onSearch={() => {
                onEgidSearch()
              }}
              disabled={type === DANDAN_TYPE_MAPPING.movie || !tmdbId}
            />
          </Form.Item>
          <Form.Item name="bangumi_id" label="BGM ID">
            <Input.Search
              placeholder="例如：296100"
              allowClear
              enterButton="Search"
              //   loading={searchTmdbLoading}
              onSearch={() => {}}
            />
          </Form.Item>
          <Form.Item name="tvdb_id" label="TVDB ID">
            <Input.Search
              placeholder="例如：364093"
              allowClear
              enterButton="Search"
              //   loading={searchTmdbLoading}
              onSearch={() => {}}
            />
          </Form.Item>
          <Form.Item name="douban_id" label="豆瓣ID">
            <Input.Search
              placeholder="例如：35297708"
              allowClear
              enterButton="Search"
              //   loading={searchTmdbLoading}
              onSearch={() => {}}
            />
          </Form.Item>
          <Form.Item name="imdb_id" label="IMDB ID">
            <Input.Search
              placeholder="例如：tt9140554"
              allowClear
              enterButton="Search"
              //   loading={searchTmdbLoading}
              onSearch={() => {}}
            />
          </Form.Item>
          <Form.Item name="name_en" label="英文名">
            <Input />
          </Form.Item>
          <Form.Item name="name_jp" label="日文名">
            <Input />
          </Form.Item>
          <Form.Item name="name_romaji" label="罗马音">
            <Input />
          </Form.Item>
          <Form.Item name="alias_cn_1" label="中文别名1">
            <Input />
          </Form.Item>
          <Form.Item name="alias_cn_2" label="中文别名2">
            <Input />
          </Form.Item>
          <Form.Item name="alias_cn_3" label="中文别名3">
            <Input />
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
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.image_url} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">{item.name}</div>
                      <div>ID: {item.id}</div>
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={async () => {
                        const res = await getTMdbDetail({
                          mediaType: type === 'tv_series' ? 'tv' : type,
                          tmdbId: item.id,
                        })
                        form.setFieldsValue({
                          tmdb_id: res.data.id,
                          tvdb_id: res.data.tvdb_id,
                          imdb_id: res.data.imdb_id,
                          name_en: res.data.name_en,
                          name_jp: res.data.name_jp,
                          name_romaji: res.data.name_romaji,
                          aliases_cn1: res.data.aliases_cn?.[1] ?? null,
                          aliases_cn2: res.data.aliases_cn?.[2] ?? null,
                          aliases_cn3: res.data.aliases_cn?.[3] ?? null,
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
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.image_url} />
                    <div className="ml-4">
                      <div className="text-xl font-bold mb-3">
                        {item.name} ({item.group_count} 组, {item.episode_count}{' '}
                        集)
                      </div>
                      <div>{item.description || '无描述'}</div>
                    </div>
                  </div>
                  <div className="flex item-center justify-center">
                    <Button type="primary">应用此组</Button>
                    <Button type="primary">查看分集</Button>
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

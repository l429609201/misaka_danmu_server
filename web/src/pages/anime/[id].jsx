import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  deleteAnimeSource,
  getAnimeDetail,
  getAnimeLibrary,
  getAnimeSource,
  setAnimeSource,
} from '../../apis'
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  List,
  message,
  Modal,
  Row,
  Space,
  Table,
} from 'antd'
import { DANDAN_TYPE_DESC_MAPPING } from '../../configs'
import { RoutePaths } from '../../general/RoutePaths'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon'

export const AnimeDetail = () => {
  const { id } = useParams()
  const [loading, setLoading] = useState(true)
  const [soueceList, setSourceList] = useState([])
  const [animeDetail, setAnimeDetail] = useState({})
  const [libraryList, setLibraryList] = useState([])
  const [renderList, setRenderList] = useState([])
  const [editOpen, setEditOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [selectedRows, setSelectedRows] = useState([])

  const navigate = useNavigate()

  console.log(soueceList, 'soueceList')

  const getDetail = async () => {
    setLoading(true)
    try {
      const [detailRes, sourceRes] = await Promise.all([
        getAnimeDetail({
          animeId: id,
        }),
        getAnimeSource({
          animeId: id,
        }),
      ])
      setAnimeDetail(detailRes.data)
      setSourceList(sourceRes.data)
      setLoading(false)
    } catch (error) {
      navigate('/library')
    }
  }

  const handleEditSource = async () => {
    try {
      const res = await getAnimeLibrary()
      const list =
        res.data?.animes?.filter(it => it.animeId !== animeDetail.anime_id) ||
        []
      setLibraryList(list)
      setRenderList(list)
      setEditOpen(true)
    } catch (error) {
      message.error('获取数据源失败')
    }
  }

  useEffect(() => {
    setRenderList(libraryList.filter(it => it.title.includes(keyword)))
  }, [keyword, libraryList])

  const handleConfirmSource = item => {
    Modal.confirm({
      title: '关联数据源',
      zIndex: 1002,
      content: (
        <div>
          您确定要将当前作品的所有数据源关联到 "{item.title}" (ID:
          {item.animeId}) 吗？
          <br />
          此操作不可撤销！
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          await setAnimeSource({
            sourceAnimeId: animeDetail.anime_id,
            targetAnimeId: item.animeId,
          })
          message.success('关联成功')
          setEditOpen(false)
          navigate(RoutePaths.LIBRARY)
        } catch (error) {
          message.error(`关联失败:${error.message}`)
        }
      },
    })
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '删除数据源',
      zIndex: 1002,
      content: (
        <div>
          您确定要删除选中的 {selectedRows.length} 个数据源吗？
          <br />
          此操作将在后台提交一个批量删除任务。
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeSource({
            source_ids: selectedRows?.map(it => it.source_id),
          })
          goTask(res)
        } catch (error) {
          message.error(`提交批量删除任务失败:${error.message}`)
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
          {res.data.message || '删除任务已提交'}
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
        getDetail()
      },
    })
  }

  const columns = [
    {
      title: '源提供方',
      dataIndex: 'provider_name',
      key: 'provider_name',
      width: 100,
    },
    {
      title: '媒体库ID',
      dataIndex: 'media_id',
      key: 'media_id',
      width: 200,
    },
    {
      title: '状态',
      width: 100,
      dataIndex: 'is_favorited',
      key: 'is_favorited',
      render: (_, record) => {
        return record.is_favorited ? (
          <MyIcon icon="favorites-fill" size={20} className="text-yellow-300" />
        ) : (
          <MyIcon icon="favorites" size={20} />
        )
      },
    },

    {
      title: '收录时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.created_at).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: '操作',
      width: 180,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {}}
            >
              {record.is_favorited ? (
                <MyIcon
                  icon="favorites-fill"
                  size={20}
                  className="text-yellow-300"
                />
              ) : (
                <MyIcon icon="favorites" size={20} />
              )}
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {}}
            >
              <MyIcon icon="clock" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {}}
            >
              <MyIcon icon="zengliang" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {}}
            >
              <MyIcon icon="book" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {}}
            >
              <MyIcon icon="refresh" size={20}></MyIcon>
            </span>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => {
                // handleDelete(record)
              }}
            >
              <MyIcon icon="delete" size={20}></MyIcon>
            </span>
          </Space>
        )
      },
    },
  ]

  const rowSelection = {
    onChange: (_, selectedRows) => {
      console.log('selectedRows: ', selectedRows)
      setSelectedRows(selectedRows)
    },
  }

  useEffect(() => {
    getDetail()
  }, [])

  return (
    <div className="my-6">
      <Card loading={loading} title={null}>
        <Row gutter={[12, 12]}>
          <Col md={20} xs={24}>
            <div className="flex items-center justify-start gap-4">
              <img src={animeDetail.image_url} className="h-[100px]" />
              <div>
                <div className="text-xl font-bold mb-3">
                  {animeDetail.title}
                </div>
                <div className="flex items-center justify-start gap-2">
                  <span>季: {animeDetail.season}</span>|<span>总集数: 1</span>|
                  <span>已关联 {soueceList.length} 个源</span>
                </div>
              </div>
            </div>
          </Col>
          <Col md={4} xs={24}>
            <div className="h-full flex items-center">
              <Button
                type="primary"
                block
                onClick={() => {
                  handleEditSource()
                }}
              >
                调整关联数据源
              </Button>
            </div>
          </Col>
        </Row>
        <div className="mt-6">
          <Button
            onClick={() => {
              handleBatchDelete()
            }}
            type="primary"
            disabled={!selectedRows.length}
            style={{ marginBottom: 16 }}
          >
            删除选中
          </Button>
          {!!soueceList?.length ? (
            <Table
              rowSelection={{ type: 'checkbox', ...rowSelection }}
              pagination={false}
              size="small"
              dataSource={soueceList}
              columns={columns}
              rowKey={'source_id'}
              scroll={{ x: '100%' }}
            />
          ) : (
            <Empty />
          )}
        </div>
      </Card>
      <Modal
        title={`为 "${animeDetail.title}"调整关联`}
        open={editOpen}
        footer={null}
        zIndex={110}
        onCancel={() => setEditOpen(false)}
      >
        <div>
          此操作会将 "{animeDetail.title}" (ID: {animeDetail.anime_id})
          下的所有数据源移动到您选择的另一个作品条目下，然后删除原条目。
        </div>
        <div className="flex items-center justify-between my-4">
          <div className="text-base font-bold">选择目标作品</div>
          <div>
            <Input
              placeholder="搜索目标作品"
              onChange={e => setKeyword(e.target.value)}
            />
          </div>
        </div>
        <List
          itemLayout="vertical"
          size="large"
          dataSource={renderList}
          pagination={{
            pageSize: 10,
          }}
          renderItem={(item, index) => {
            return (
              <List.Item key={index}>
                <div className="flex justify-between items-center">
                  <div className="flex items-center justify-start">
                    <img width={60} alt="logo" src={item.imageUrl} />
                    <div className="ml-4">
                      <div className="text-base font-bold mb-2">
                        {item.title}（ID: {item.animeId}）
                      </div>
                      <div>
                        <span>季:{item.season}</span>
                        <span className="ml-3">
                          类型:{DANDAN_TYPE_DESC_MAPPING[item.type]}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div>
                    <Button
                      type="primary"
                      onClick={() => {
                        handleConfirmSource(item)
                      }}
                    >
                      关联
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

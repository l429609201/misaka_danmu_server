import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  deleteAnimeEpisode,
  deleteAnimeEpisodeSingle,
  editEpisode,
  getAnimeDetail,
  getEpisodes,
  manualImportEpisode,
  refreshEpisodeDanmaku,
  resetEpisode,
} from '../../apis'
import { useEffect, useState } from 'react'
import {
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Space,
  Table,
  Tooltip,
} from 'antd'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon'
import { HomeOutlined } from '@ant-design/icons'
import { RoutePaths } from '../../general/RoutePaths'

export const EpisodeDetail = () => {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const animeId = searchParams.get('animeId')
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [animeDetail, setAnimeDetail] = useState({})
  const [episodeList, setEpisodeList] = useState([])
  const [selectedRows, setSelectedRows] = useState([])

  const [form] = Form.useForm()
  const [editOpen, setEditOpen] = useState(false)
  const [confirmLoading, setConfirmLoading] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetLoading, setResetLoading] = useState(false)
  const [resetInfo, setResetInfo] = useState({})

  const getDetail = async () => {
    setLoading(true)
    try {
      const [detailRes, episodeRes] = await Promise.all([
        getAnimeDetail({
          animeId: Number(animeId),
        }),
        getEpisodes({
          sourceId: Number(id),
        }),
      ])
      setAnimeDetail(detailRes.data)
      setEpisodeList(episodeRes.data)
      setLoading(false)
    } catch (error) {
      navigate(`/anime/${animeId}`)
    }
  }

  useEffect(() => {
    getDetail()
  }, [])

  const columns = [
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
      width: 60,
      sorter: {
        compare: (a, b) => a.episodeIndex - b.episodeIndex,
        multiple: 1,
      },
    },
    {
      title: '弹幕数',
      dataIndex: 'commentCount',
      key: 'commentCount',
      width: 60,
    },

    {
      title: '采集时间',
      dataIndex: 'fetchedAt',
      key: 'fetchedAt',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.fetchedAt).format('YYYY-MM-DD HH:mm:ss')}</div>
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
            <a
              href={record.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              跳转
            </a>
          </div>
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
            <Tooltip title="编辑分集信息">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  form.setFieldsValue({
                    ...record,
                    episodeId: record.episodeId,
                    originalEpisodeIndex: record.episodeIndex,
                  })
                  setEditOpen(true)
                }}
              >
                <MyIcon icon="edit" size={20} />
              </span>
            </Tooltip>

            <Tooltip title="刷新分集弹幕">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => handleRefresh(record)}
              >
                <MyIcon icon="refresh" size={20}></MyIcon>
              </span>
            </Tooltip>

            <Tooltip title="弹幕详情">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => {
                  navigate(`/comment/${record.episodeId}?episodeId=${id}`)
                }}
              >
                <MyIcon icon="comment" size={20}></MyIcon>
              </span>
            </Tooltip>
            <Tooltip title="删除">
              <span
                className="cursor-pointer hover:text-primary"
                onClick={() => deleteEpisodeSingle(record)}
              >
                <MyIcon icon="delete" size={20}></MyIcon>
              </span>
            </Tooltip>
          </Space>
        )
      },
    },
  ]

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
    Modal.confirm({
      title: '删除分集',
      zIndex: 1002,
      content: (
        <div>
          您确定要删除选中的 {selectedRows.length} 个分集吗？
          <br />
          此操作将在后台提交一个批量删除任务。
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisode({
            episodeIds: selectedRows?.map(it => it.episodeId),
          })
          goTask(res)
        } catch (error) {
          message.error(`提交批量删除任务失败:${error.message}`)
        }
      },
    })
  }

  const deleteEpisodeSingle = record => {
    Modal.confirm({
      title: '删除分集',
      zIndex: 1002,
      content: (
        <div>
          您确定要删除分集 '{record.title}' 吗？
          <br />
          此操作将在后台提交一个批量删除任务。
        </div>
      ),
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await deleteAnimeEpisodeSingle({
            id: record.episodeId,
          })
          goTask(res)
        } catch (error) {
          message.error(`提交删除任务失败:${error.message}`)
        }
      },
    })
  }

  const handleRefresh = record => {
    Modal.confirm({
      title: '刷新分集',
      zIndex: 1002,
      content: <div>您确定要刷新分集 '{record.title}' 的弹幕吗？</div>,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const res = await refreshEpisodeDanmaku({
            id: record.episodeId,
          })
          message.success(res.message || '刷新任务已开始。')
        } catch (error) {
          message.error(`启动刷新任务失败:${error.message}`)
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
          {res.data?.message || '任务已提交'}
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
        setSelectedRows([])
      },
    })
  }

  const handleSave = async () => {
    try {
      if (confirmLoading) return
      setConfirmLoading(true)
      const values = await form.validateFields()
      if (values.episodeId) {
        await editEpisode({
          ...values,
          sourceId: Number(id),
        })
      } else {
        await manualImportEpisode({
          title: values.title,
          episodeIndex: values.episodeIndex,
          url: values.sourceUrl,
          sourceId: Number(id),
        })
      }
      getDetail()
      form.resetFields()
      message.success('分集信息更新成功！')
    } catch (error) {
      console.log(error)
      message.error(`更新失败: ${error.message || error?.detail || error}`)
    } finally {
      setConfirmLoading(false)
      setEditOpen(false)
    }
  }

  const handleResetEpisode = () => {
    Modal.confirm({
      title: '重整集数',
      zIndex: 1002,
      content: (
        <div>
          您确定要为 '{animeDetail.title}
          '的这个数据源重整集数吗？
          <br />
          此操作会按当前顺序将集数重新编号为 1, 2, 3...
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
          message.error(`提交重整任务失败:${error.message}`)
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
      message.success('已提交：批量删除 + 重整集数 两个任务。')
    } catch (error) {
      message.error(`提交任务失败: ${error.message}`)
    } finally {
      setResetInfo({})
      setResetOpen(false)
      setResetLoading(false)
    }
  }

  const rowSelection = {
    onChange: (_, selectedRows) => {
      console.log('selectedRows: ', selectedRows)
      setSelectedRows(selectedRows)
    },
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
        <div className="flex items-center justify-between flex-wrap md:flex-nowrap">
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
          <div className="w-full flex items-center justify-between md:justify-end gap-2 mb-4">
            <Button
              onClick={() => {
                const validCounts = episodeList
                  .map(ep => Number(ep.commentCount))
                  .filter(n => Number.isFinite(n) && n >= 0)
                if (validCounts.length === 0) {
                  message.error('所有分集的弹幕数不可用。')
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
                  message.error(
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
              type="primary"
            >
              正片重整
            </Button>
            <Button
              onClick={() => {
                handleResetEpisode()
              }}
              disabled={!episodeList.length}
              type="primary"
            >
              重整集数
            </Button>
            <Button
              onClick={() => {
                setEditOpen(true)
              }}
              type="primary"
            >
              手动导入
            </Button>
          </div>
        </div>
        {!!episodeList?.length ? (
          <Table
            rowSelection={{ type: 'checkbox', ...rowSelection }}
            pagination={false}
            size="small"
            dataSource={episodeList}
            columns={columns}
            rowKey={'episodeId'}
            scroll={{ x: '100%' }}
          />
        ) : (
          <Empty />
        )}
      </Card>
      <Modal
        title="编辑分集信息"
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
            />
          </Form.Item>
          <Form.Item
            name="sourceUrl"
            label="官方链接"
            rules={[{ required: true, message: '请输入官方链接' }]}
          >
            <Input placeholder="请输入官方链接" />
          </Form.Item>
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
        destroyOnHidden
        zIndex={100}
      >
        <div>
          <div className="mb-2">将基于平均弹幕数进行正片重整：</div>
          <ul>
            <li>
              平均弹幕数：<strong>{resetInfo?.average?.toFixed(2)}</strong>
            </li>
            <li>
              预计删除分集：
              <span className="text-red-400 font-bold">
                {resetInfo?.toDelete?.length}
              </span>{' '}
              / {episodeList.length}
            </li>
            <li>
              预计保留分集：
              <span className="text-green-500 font-bold">
                {resetInfo?.toKeep?.length}
              </span>{' '}
              / {episodeList.length}
            </li>
          </ul>
        </div>
        <div className="my-4 text-sm font-semibold">
          预览将保留的分集（最多显示 80 条）
        </div>
        <Table
          pagination={false}
          size="small"
          dataSource={resetInfo?.toKeep?.slice(0, 80) ?? []}
          columns={keepColumns}
          rowKey={'id'}
          scroll={{ x: '100%' }}
        />
      </Modal>
    </div>
  )
}

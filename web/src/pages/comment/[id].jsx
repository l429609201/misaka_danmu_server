import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { getDanmakuDetail, getEpisodes } from '../../apis'
import { Breadcrumb, Card } from 'antd'
import { HomeOutlined } from '@ant-design/icons'

export const CommentDetail = () => {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const episodeId = searchParams.get('episodeId')

  const [loading, setLoading] = useState(true)
  const [commentList, setCommentList] = useState([])
  const [episode, setEpisode] = useState({})

  const navigate = useNavigate()

  const getDetail = async () => {
    setLoading(true)
    try {
      const [episodeRes, commentRes] = await Promise.all([
        getEpisodes({
          sourceId: Number(episodeId),
        }),
        getDanmakuDetail({
          id: Number(id),
        }),
      ])
      setCommentList(commentRes.data?.comments || [])
      setEpisode(
        episodeRes?.data?.filter(it => it.episodeId === Number(id))?.[0] || {}
      )
      setLoading(false)
    } catch (error) {}
  }

  console.log(episode, 'episode')

  useEffect(() => {
    getDetail()
  }, [])

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
            title: <Link to="javascript:void(0)">分集列表</Link>,
            onClick: () => navigate(-1),
          },
          {
            title: '弹幕列表',
          },
        ]}
      />
      <Card loading={loading} title={`弹幕列表: ${episode?.title ?? ''}`}>
        <div
          className="overflow-y-auto"
          style={{
            height: 'calc(100vh - 260px)',
          }}
        >
          {commentList?.map((it, index) => (
            <div key={index}>
              <div className="my-1">
                <pre>
                  {it.p} | {it.m}
                </pre>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

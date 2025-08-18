import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { getDanmakuDetail, getEpisodes } from '../../apis'
import { Card } from 'antd'

export const CommentDetail = () => {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const episodeId = searchParams.get('episodeId')

  const [loading, setLoading] = useState(true)
  const [commentList, setCommentList] = useState([])
  const [episode, setEpisode] = useState({})

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
        episodeRes?.data?.filter(it => it.id === Number(id))?.[0] || {}
      )
      setLoading(false)
    } catch (error) {
      navigate(`/anime/${animeId}`)
    }
  }

  console.log(episode, 'episode')

  useEffect(() => {
    getDetail()
  }, [])

  return (
    <div className="my-6">
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

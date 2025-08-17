import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getAnimeDetail, getAnimeSource } from '../../apis'
import { Button, Card } from 'antd'

export const AnimeDetail = () => {
  const { id } = useParams()
  const [loading, setLoading] = useState(true)
  const [soueceList, setSourceList] = useState([])
  const [animeDetail, setAnimeDetail] = useState({})

  const navigate = useNavigate()

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

  useEffect(() => {
    getDetail()
  }, [])

  return (
    <div className="my-6">
      <Card loading={loading} title={null}>
        <div className="flex items-center justify-between">
          <div className="flex items-center justify-start gap-4">
            <img src={animeDetail.image_url} className="h-[120px]" />
            <div>
              <div className="text-xl font-bold mb-3">{animeDetail.title}</div>
              <div className="flex items-center justify-start gap-2">
                <span>季: {animeDetail.season}</span>|<span>总集数: 1</span>|
                <span>已关联 {soueceList.length} 个源</span>
              </div>
            </div>
          </div>
          <div className="justify-self-end">
            <Button type="primary">调整关联数据源</Button>
          </div>
        </div>
      </Card>
    </div>
  )
}

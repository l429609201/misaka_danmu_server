import { useParams } from 'react-router-dom'

export const AnimeDetail = () => {
  const { id } = useParams()

  return <div className="my-6">{id}</div>
}

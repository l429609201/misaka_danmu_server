import { Tabs } from 'antd'
import { Scrapers } from './components/Scrapers'
import { Metadata } from './components/Metadata'
import { useNavigate, useSearchParams } from 'react-router-dom'

export const Source = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'scrapers'
  const navigate = useNavigate()

  return (
    <Tabs
      defaultActiveKey={key}
      items={[
        {
          label: '弹幕搜索源',
          key: 'scrapers',
          children: <Scrapers></Scrapers>,
        },
        {
          label: '元信息搜索源',
          key: 'metadata',
          children: <Metadata></Metadata>,
        },
      ]}
      onChange={key => {
        navigate(`/source?key=${key}`, {
          replace: true,
        })
      }}
    />
  )
}

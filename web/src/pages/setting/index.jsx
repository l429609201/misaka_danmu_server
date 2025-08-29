import { Tabs } from 'antd'
import { Security } from './components/Security'
import { Webhook } from './components/Webhook'
import { Bangumi } from './components/Bangumi'
import { TMDB } from './components/TMDB'
import { Douban } from './components/Douban'
import { TVDB } from './components/TVDB'
import { Proxy } from './components/Proxy'
import { useNavigate, useSearchParams } from 'react-router-dom'

export const Setting = () => {
  const [searchParams] = useSearchParams()
  const key = searchParams.get('key') || 'security'
  const navigate = useNavigate()

  return (
    <Tabs
      defaultActiveKey={key}
      items={[
        {
          label: '账户安全',
          key: 'security',
          children: <Security />,
        },
        {
          label: '代理设置',
          key: 'proxy',
          children: <Proxy />,
        },
        {
          label: 'Webhook',
          key: 'webhook',
          children: <Webhook />,
        },
        {
          label: 'Bangumi配置',
          key: 'bangumi',
          children: <Bangumi />,
        },
        {
          label: 'TMDB配置',
          key: 'tmdb',
          children: <TMDB />,
        },
        {
          label: '豆瓣配置',
          key: 'douban',
          children: <Douban />,
        },
        {
          label: 'TVDB配置',
          key: 'tvdb',
          children: <TVDB />,
        },
      ]}
      onChange={key => {
        navigate(`/setting?key=${key}`, {
          replace: true,
        })
      }}
    />
  )
}

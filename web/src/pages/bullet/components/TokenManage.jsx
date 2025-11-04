import { Domain } from './Domain'
import { Token } from './Token'
import { Ua } from './Ua'
import { useState, useEffect } from 'react'
import { getCustomDomain } from '../../../apis'

export const TokenManage = () => {
  const [domain, setDomain] = useState('')

  useEffect(() => {
    // 初始加载域名
    getCustomDomain().then(res => {
      setDomain(res.data?.value ?? '')
    }).catch(err => {
      console.error('获取自定义域名失败:', err)
    })
  }, [])

  const handleDomainChange = (newDomain) => {
    setDomain(newDomain)
  }

  return (
    <>
      <Token domain={domain} />
      <Domain domain={domain} onDomainChange={handleDomainChange} />
      <Ua />
      <p>
        本项目参考了
        <a
          href="https://api.dandanplay.net/swagger/index.html"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          dandanplayapi
        </a>
        ，同时增加了使用访问令牌管理弹幕api,支持
        <a
          href="https://t.me/yamby_release"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          yamby
        </a>
        、
        <a
          href="https://play.google.com/store/search?q=hills&c=apps"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          hills
        </a>
        、
        <a
          href="https://apps.microsoft.com/detail/9NB0H051M4V4"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          小幻影视
        </a>
        、
        <a
          href="https://apps.apple.com/cn/app/senplayer-%E6%99%BA%E8%83%BD%E8%A7%86%E9%A2%91%E6%92%AD%E6%94%BE%E5%99%A8-8%E5%80%8D%E9%80%9F/id6443975850"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          SenPlayer
        </a>
        。
      </p>
    </>
  )
}

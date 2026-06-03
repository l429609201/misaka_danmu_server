import { useState, useEffect } from 'react'
import { Domain } from './Domain'
import { Token } from './Token'
import { Ua } from './Ua'
import { getCustomDomain } from '../../../apis'
import { useTranslation } from 'react-i18next'

export const TokenManage = () => {
  const { t } = useTranslation()
  const [domain, setDomain] = useState('')

  // 在父组件统一管理 domain 状态
  useEffect(() => {
    getCustomDomain().then(res => {
      setDomain(res.data?.value ?? '')
    })
  }, [])

  // 提供给子组件的回调，用于更新 domain
  const handleDomainChange = (newDomain) => {
    setDomain(newDomain)
  }

  return (
    <>
      <Token domain={domain} />
      <Domain domain={domain} onDomainChange={handleDomainChange} />
      <Ua />
      <p>
        {t('bullet.tokenDesc1')}
        <a
          href="https://api.dandanplay.net/swagger/index.html"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          {t('bullet.tokenDescDandanplay')}
        </a>
        {t('bullet.tokenDesc2')}
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
          {t('bullet.tokenDescXiaoHuan')}
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

import { Button, Card, Input, message } from 'antd'
import { useEffect, useState } from 'react'
import { setCustomDomain } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

export const Domain = ({ domain: propDomain, onDomainChange }) => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [domain, setDomain] = useState(propDomain || '')
  const messageApi = useMessage()

  // 监听 prop 变化，同步到本地状态
  useEffect(() => {
    setDomain(propDomain || '')
  }, [propDomain])

  const handleEdit = async () => {
    try {
      await setCustomDomain({ value: domain })
      messageApi.success(t('bullet.saveSuccess'))
      // 通知父组件更新 domain
      if (onDomainChange) {
        onDomainChange(domain)
      }
    } catch (error) {
      messageApi.error(t('bullet.saveFailed'))
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title={t('bullet.domainTitle')}>
        <div>
          {t('bullet.domainDesc')}
        </div>
        <div className="flex items-center justify-start mt-4">
          <Input
            placeholder={t('bullet.domainPlaceholder')}
            value={domain}
            onChange={e => setDomain(e.target.value)}
          />
          <Button type="primary" className="ml-2" onClick={handleEdit}>
            {t('bullet.domainSave')}
          </Button>
        </div>
      </Card>
    </div>
  )
}

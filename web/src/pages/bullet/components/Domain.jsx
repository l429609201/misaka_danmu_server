import { Button, Card, Input, Tooltip, Alert } from 'antd'
import { useEffect, useState } from 'react'
import { setCustomDomain, validateNotificationPublicDomain } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { CheckCircleOutlined, CloseCircleOutlined, SafetyOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

export const Domain = ({ domain: propDomain, onDomainChange }) => {
  const { t } = useTranslation()
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [domain, setDomain] = useState(propDomain || '')
  // probeResult: null | { ok: true, probeUrl } | { ok: false, detail }
  const [probeResult, setProbeResult] = useState(null)
  const messageApi = useMessage()

  // 监听 prop 变化，同步到本地状态；域名变化时重置探测结果
  useEffect(() => {
    setDomain(propDomain || '')
    setProbeResult(null)
  }, [propDomain])

  const handleEdit = async () => {
    try {
      setSaving(true)
      await setCustomDomain({ value: domain })
      messageApi.success(t('bullet.saveSuccess'))
      setProbeResult(null)
      if (onDomainChange) onDomainChange(domain)
    } catch {
      messageApi.error(t('bullet.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const handleProbe = async () => {
    try {
      setProbing(true)
      setProbeResult(null)
      const res = await validateNotificationPublicDomain()
      setProbeResult({ ok: true, probeUrl: res?.data?.probeUrl })
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || t('bullet.domainProbeUnknownError')
      setProbeResult({ ok: false, detail })
    } finally {
      setProbing(false)
    }
  }

  return (
    <div className="my-6">
      <Card id="feat-custom-domain" title={t('bullet.domainTitle')}>
        <div>{t('bullet.domainDesc')}</div>
        <div className="flex items-center justify-start mt-4 gap-2">
          <Input
            placeholder={t('bullet.domainPlaceholder')}
            value={domain}
            onChange={e => { setDomain(e.target.value); setProbeResult(null) }}
          />
          <Button type="primary" loading={saving} onClick={handleEdit}>
            {t('bullet.domainSave')}
          </Button>
          <Tooltip title={t('bullet.domainProbeTip')}>
            <Button
              icon={<SafetyOutlined />}
              loading={probing}
              onClick={handleProbe}
            >
              {t('bullet.domainProbe')}
            </Button>
          </Tooltip>
        </div>
        {probeResult && (
          <Alert
            className="mt-3"
            type={probeResult.ok ? 'success' : 'error'}
            icon={probeResult.ok ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
            showIcon
            message={
              probeResult.ok
                ? t('bullet.domainProbeSuccess')
                : t('bullet.domainProbeFailed')
            }
            description={
              probeResult.ok
                ? probeResult.probeUrl
                : probeResult.detail
            }
          />
        )}
      </Card>
    </div>
  )
}

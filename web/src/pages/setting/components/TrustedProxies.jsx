import { Button, Card, Form, Input } from 'antd'
import { useEffect, useState } from 'react'
import { getTrustedProxiesConfig, setTrustedProxiesConfig } from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

export const TrustedProxies = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const messageApi = useMessage()

  useEffect(() => {
    setLoading(true)
    getTrustedProxiesConfig()
      .then(res => {
        form.setFieldsValue({ trustedProxies: res.data?.value ?? '' })
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      await setTrustedProxiesConfig({
        value: values.trustedProxies || '',
      })
      setIsSaveLoading(false)
      messageApi.success(t('trustedProxies.saveSuccess'))
    } catch (error) {
      messageApi.error(t('trustedProxies.saveFailed'))
    } finally {
      setIsSaveLoading(false)
    }
  }

  return (
    <div className="my-6">
      <Card loading={loading} title={t('trustedProxies.title')}>
        <div className="mb-4">{t('trustedProxies.desc')}</div>
        <Form
          form={form}
          layout="horizontal"
          onFinish={handleSave}
          className="px-6 pb-6"
        >
          <Form.Item name="trustedProxies" label={t('trustedProxies.ipCidrList')} className="mb-6">
            <Input.TextArea rows={4} placeholder={t('trustedProxies.placeholder')} />
          </Form.Item>

          <Form.Item>
            <div className="flex justify-end">
              <Button type="primary" htmlType="submit" loading={isSaveLoading}>
                {t('trustedProxies.saveChanges')}
              </Button>
            </div>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}
import { useState } from 'react'
import { Form, Input, InputNumber, Modal, Select, message } from 'antd'
import { useTranslation } from 'react-i18next'
import { createAnimeEntry } from '../apis'
import { useMessage } from '../MessageContext'

export const CreateAnimeModal = ({ open, onCancel, onSuccess }) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const messageApi = useMessage()

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)
      const res = await createAnimeEntry(values)
      if (res.data) {
        messageApi.success(t('createAnime.createSuccess'))
        onSuccess(res.data) // 将新创建的作品数据传递回去，以便刷新列表
        form.resetFields()
      }
    } catch (error) {
      console.error('创建作品失败:', error)
      messageApi.error(error.detail || t('createAnime.createFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={t('createAnime.title')}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      confirmLoading={loading}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        name="create_anime_form"
        className="!px-4 !pt-6"
      >
        <Form.Item
          name="title"
          label={t('createAnime.animeTitle')}
          rules={[{ required: true, message: t('createAnime.inputTitle') }]}
        >
          <Input placeholder={t('createAnime.titlePlaceholder')} />
        </Form.Item>
        <Form.Item
          name="type"
          label={t('createAnime.type')}
          rules={[{ required: true, message: t('createAnime.selectType') }]}
          initialValue="tv_series"
        >
          <Select>
            <Select.Option value="tv_series">{t('createAnime.tvSeries')}</Select.Option>
            <Select.Option value="movie">{t('createAnime.movie')}</Select.Option>
          </Select>
        </Form.Item>
        <Form.Item name="season" label={t('createAnime.season')} initialValue={1}>
          <InputNumber min={0} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="year" label={t('createAnime.year')}>
          <InputNumber
            placeholder={t('createAnime.yearPlaceholder')}
            min={1900}
            max={new Date().getFullYear() + 5}
            style={{ width: '100%' }}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

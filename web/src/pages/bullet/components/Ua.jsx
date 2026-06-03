import {
  Button,
  Card,
  Col,
  Input,
  message,
  Modal,
  Row,
  Select,
  Space,
  Table,
} from 'antd'
import { useEffect, useState } from 'react'
import {
  addUaRule,
  deleteUaRule,
  getUaMode,
  getUaRules,
  setUaMode,
} from '../../../apis'
import dayjs from 'dayjs'
import { MyIcon } from '@/components/MyIcon.jsx'
import { useModal } from '../../../ModalContext'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

export const Ua = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState('off')

  const [open, setOpen] = useState(false)
  const [uaRules, setUaRules] = useState([])
  const [uakeyword, setUakeyword] = useState('')
  const [addLoading, setAddLoading] = useState(false)
  const modalApi = useModal()
  const messageApi = useMessage()

  const columns = [
    {
      title: t('bullet.uaColumnString'),
      dataIndex: 'uaString',
      key: 'uaString',
      width: 150,
    },
    {
      title: t('bullet.uaColumnCreated'),
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 200,
      render: (_, record) => {
        return (
          <div>{dayjs(record.createdAt).format('YYYY-MM-DD HH:mm:ss')}</div>
        )
      },
    },
    {
      title: t('bullet.uaColumnAction'),
      width: 60,
      fixed: 'right',
      render: (_, record) => {
        return (
          <Space>
            <span
              className="cursor-pointer hover:text-primary"
              onClick={() => handleDelete(record)}
            >
              <MyIcon icon="delete" size={20}></MyIcon>
            </span>
          </Space>
        )
      },
    },
  ]

  useEffect(() => {
    setLoading(true)
    getUaMode()
      .then(res => {
        setMode(res.data?.value ?? 'off')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleEdit = async () => {
    try {
      await setUaMode({ value: mode })
      messageApi.success(t('bullet.saveSuccess'))
    } catch (error) {
      messageApi.error(t('bullet.saveFailed'))
    }
  }

  const handleDelete = async record => {
    modalApi.confirm({
      title: t('bullet.uaDeleteTitle'),
      zIndex: 1002,
      content: <div>{t('bullet.uaDeleteConfirm', { ua: record.uaString })}</div>,
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          await deleteUaRule({
            id: record.id,
          })
          handleList()
          messageApi.success(t('bullet.uaDeleteSuccess'))
        } catch (error) {
          console.error(error)
          messageApi.error(t('bullet.uaDeleteFailed'))
        }
      },
    })
  }

  const handleAdd = async () => {
    try {
      if (addLoading) return
      if (!uakeyword) {
        messageApi.error(t('bullet.uaInputRequired'))
        return
      }
      setAddLoading(true)
      await addUaRule({
        uaString: uakeyword,
      })
    } catch (error) {
      messageApi.error(t('bullet.uaAddFailed'))
    } finally {
      setUakeyword('')
      handleList()
      setAddLoading(false)
    }
  }

  const handleList = async () => {
    try {
      const res = await getUaRules()
      setUaRules(res.data)
      setOpen(true)
    } catch (error) {
      messageApi.error(t('bullet.uaGetFailed'))
    }
  }

  return (
    <div className="my-6">
      <Card title={t('bullet.uaTitle')}>
        <div className="mb-4">
          {t('bullet.uaDesc')}
        </div>
        <Row gutter={[12, 12]}>
          <Col md={2} xs={6}>
            <div className="leading-8">{t('bullet.uaFilterMode')}</div>
          </Col>
          <Col md={10} xs={18}>
            <Select
              onChange={value => {
                setMode(value)
              }}
              style={{ width: '100%' }}
              value={['off', 'blacklist', 'whitelist'].includes(mode) ? mode : 'off'}
              loading={loading}
              options={[
                { value: 'off', label: t('bullet.uaModeOff') },
                { value: 'blacklist', label: t('bullet.uaModeBlacklist') },
                { value: 'whitelist', label: t('bullet.uaModeWhitelist') },
              ]}
            />
          </Col>
          <Col md={6} xs={12} className="mt-3 md:mt-0">
            <Button type="primary" block onClick={handleEdit} loading={loading}>
              {t('bullet.uaSaveMode')}
            </Button>
          </Col>
          <Col md={6} xs={12} className="mt-3 md:mt-0">
            <Button type="primary" block onClick={handleList}>
              {t('bullet.uaManageList')}
            </Button>
          </Col>
        </Row>
      </Card>
      <Modal
        title={t('bullet.uaManageTitle')}
        open={open}
        cancelText={t('common.cancel')}
        okText={t('common.confirm')}
        footer={null}
        onCancel={() => setOpen(false)}
      >
        <div className="flex items-center justify-start my-4 gap-2">
          <div>{t('bullet.uaAddTitle')}</div>
          <Input
            placeholder={t('bullet.uaPlaceholder')}
            value={uakeyword}
            onChange={e => setUakeyword(e.target.value)}
          />
          <Button type="primary" onClick={handleAdd} loading={addLoading}>
            {t('bullet.uaAdd')}
          </Button>
        </div>
        <Table
          pagination={false}
          size="small"
          dataSource={uaRules}
          columns={columns}
          rowKey={'id'}
          scroll={{
            x: '100%',
            y: 400,
          }}
        />
      </Modal>
    </div>
  )
}

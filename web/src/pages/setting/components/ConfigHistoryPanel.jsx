import { Card, Button, Table, Tag, Space, Popconfirm, message, Spin, Empty } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getConfigHistory, rollbackConfig, clearConfigHistory } from '@/apis'
import { HistoryOutlined, RollbackOutlined, DeleteOutlined, ReloadOutlined } from '@ant-design/icons'

export const ConfigHistoryPanel = () => {
  const { t } = useTranslation()
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)

  const reload = () => {
    setLoading(true)
    getConfigHistory({ limit: 50 }).then(res => { const d = res?.data; setData(Array.isArray(d) ? d : []); setLoading(false) }).catch(() => setLoading(false))
  }

  useEffect(() => { reload() }, [])

  const handleRollback = async (record) => {
    try {
      await rollbackConfig({ key: record.key, value: record.oldValue || '' })
      message.success(t('configHistory.rollbackSuccess'))
      reload()
    } catch { message.error(t('configHistory.rollbackFailed')) }
  }

  const columns = [
    {
      title: t('configHistory.time'), dataIndex: 'changedAt', width: 160,
      render: v => v ? new Date(v).toLocaleString() : '-',
    },
    { title: t('configHistory.key'), dataIndex: 'key', width: 180, ellipsis: true },
    {
      title: t('configHistory.source'), dataIndex: 'source', width: 80,
      render: v => <Tag color={v === 'rollback' ? 'purple' : v === 'system' ? 'blue' : 'green'}>{v}</Tag>,
    },
    {
      title: t('configHistory.oldValue'), dataIndex: 'oldValue', ellipsis: true,
      render: v => <span className="text-xs font-mono">{v || '-'}</span>,
    },
    {
      title: t('configHistory.newValue'), dataIndex: 'newValue', ellipsis: true,
      render: v => <span className="text-xs font-mono">{v || '-'}</span>,
    },
    {
      title: t('configHistory.action'), width: 80,
      render: (_, record) => record.oldValue ? (
        <Popconfirm title={t('configHistory.confirmRollback')} onConfirm={() => handleRollback(record)}>
          <Button icon={<RollbackOutlined />} size="small" type="link" />
        </Popconfirm>
      ) : null,
    },
  ]

  return (
    <Card title={<><HistoryOutlined className="mr-2" />{t('configHistory.title')}</>} size="small"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={reload} size="small" />
          <Popconfirm title={t('configHistory.confirmClear')} onConfirm={async () => { await clearConfigHistory(); reload(); message.success('ok') }}>
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      }>
      <Table columns={columns} dataSource={[...data].reverse()} rowKey={(_, i) => i} size="small" pagination={{ pageSize: 10 }} loading={loading} />
    </Card>
  )
}

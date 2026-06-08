import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Table, Button, Input, Select, Space, Tag, Popconfirm, message, Statistic, Row, Col, Card, Tooltip } from 'antd'
import { DeleteOutlined, ReloadOutlined, ClearOutlined, SearchOutlined, DatabaseOutlined, EyeOutlined } from '@ant-design/icons'
import { getCacheStats, getCacheList, clearCache, deleteCacheKey, getCacheDetail } from '@/apis'

const REGION_COLORS = {
  search: 'blue',
  metadata: 'green',
  episodes: 'orange',
  comments: 'purple',
  default: 'default',
}

export default function CacheManagerModal({ open, onClose }) {
  const { t } = useTranslation()
  const [stats, setStats] = useState({ total: 0, regions: {} })
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [region, setRegion] = useState('all')
  const [search, setSearch] = useState('')
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [detailModal, setDetailModal] = useState({ open: false, data: null, loading: false })

  const fetchStats = useCallback(async () => {
    try {
      const res = await getCacheStats()
      setStats(res.data)
    } catch { /* ignore */ }
  }, [])

  const fetchItems = useCallback(async (page = 1, size = 20) => {
    setLoading(true)
    try {
      const res = await getCacheList({ region, search: search || undefined, page, pageSize: size })
      setItems(res.data.items || [])
      setPagination(prev => ({ ...prev, current: page, pageSize: size, total: res.data.total }))
    } catch { /* ignore */ }
    setLoading(false)
  }, [region, search])

  useEffect(() => {
    if (open) {
      fetchStats()
      fetchItems(1)
    }
  }, [open, region])

  const handleSearch = () => fetchItems(1)

  const handleDeleteKey = async (key, itemRegion) => {
    try {
      await deleteCacheKey(key, itemRegion)
      message.success(t('cacheManager.deleted'))
      fetchStats()
      fetchItems(pagination.current)
    } catch {
      message.error(t('cacheManager.deleteFailed'))
    }
  }

  const handleClearRegion = async (r) => {
    try {
      const res = await clearCache(r)
      message.success(t('cacheManager.clearedCount', { count: res.data.cleared }))
      fetchStats()
      fetchItems(1)
    } catch {
      message.error(t('cacheManager.clearFailed'))
    }
  }

  const handleClearAll = async () => {
    try {
      const res = await clearCache(undefined)
      message.success(t('cacheManager.clearedAllCount', { count: res.data.cleared }))
      fetchStats()
      fetchItems(1)
    } catch {
      message.error(t('cacheManager.clearFailed'))
    }
  }

  const columns = [
    ...(region === 'all' ? [{
      title: t('cacheManager.region'),
      dataIndex: 'region',
      width: 100,
      render: (r) => <Tag color={REGION_COLORS[r] || 'default'}>{r}</Tag>,
    }] : []),
    {
      title: t('cacheManager.key'),
      dataIndex: 'key',
      ellipsis: true,
    },
    {
      title: t('cacheManager.value'),
      dataIndex: 'value_preview',
      ellipsis: true,
      render: (text) => (
        <Tooltip title={text} placement="topLeft">
          <span style={{ fontSize: 12, color: '#888' }}>{text}</span>
        </Tooltip>
      ),
    },
    {
      title: t('common.operation'),
      width: 100,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EyeOutlined />}
            onClick={async () => {
              setDetailModal({ open: true, data: null, loading: true })
              try {
                const res = await getCacheDetail(record.key, record.region)
                setDetailModal({ open: true, data: res.data, loading: false })
              } catch {
                setDetailModal({ open: false, data: null, loading: false })
              }
            }} />
          <Popconfirm title={t('common.delete_confirm')} onConfirm={() => handleDeleteKey(record.key, record.region)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
            <Button type="link" danger size="small" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const tableData = items.map((item, idx) => ({ ...item, _key: `${item.region}_${item.key}_${idx}` }))

  // 区域选项：全部 + 各个有数据的 region
  const regionOptions = [
    { label: t('cacheManager.allWithCount', { count: stats.total }), value: 'all' },
    ...Object.entries(stats.regions || {}).map(([r, count]) => ({ label: `${r} (${count})`, value: r })),
  ]

  const clearLabel = region === 'all' ? t('cacheManager.all') : region

  return (
    <Modal
      title={<><DatabaseOutlined /> {t('cacheManager.title')}</>}
      open={open}
      onCancel={onClose}
      footer={null}
      width={800}
      destroyOnClose
    >
      {/* 统计卡片 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small"><Statistic title={t('cacheManager.total')} value={stats.total} /></Card>
        </Col>
        {Object.entries(stats.regions || {}).map(([r, count]) => (
          <Col span={6} key={r}>
            <Card size="small">
              <Statistic title={<Tag color={REGION_COLORS[r] || 'default'}>{r}</Tag>} value={count} />
            </Card>
          </Col>
        ))}
      </Row>

      {/* 工具栏 */}
      <Space style={{ marginBottom: 12 }} wrap>
        <Select value={region} onChange={v => { setRegion(v); setSearch('') }} options={regionOptions} style={{ width: 180 }} />
        <Input placeholder={t('cacheManager.searchKey')} value={search} onChange={e => setSearch(e.target.value)} onPressEnter={handleSearch} prefix={<SearchOutlined />} style={{ width: 200 }} allowClear />
        <Button icon={<ReloadOutlined />} onClick={() => { fetchStats(); fetchItems(1) }}>{t('common.refresh')}</Button>
        <Popconfirm title={t('cacheManager.clearConfirm', { region: clearLabel })} onConfirm={() => region === 'all' ? handleClearAll() : handleClearRegion(region)} okText={t('common.confirm')} cancelText={t('common.cancel')}>
          <Button icon={<ClearOutlined />} danger>{t('cacheManager.clearWithRegion', { region: clearLabel })}</Button>
        </Popconfirm>
      </Space>

      {/* 缓存列表 */}
      <Table
        columns={columns}
        dataSource={tableData}
        rowKey="_key"
        loading={loading}
        size="small"
        pagination={{
          ...pagination,
          showSizeChanger: true,
          showTotal: tc => t('cacheManager.totalCount', { count: tc }),
          onChange: (page, size) => fetchItems(page, size),
        }}
      />

      {/* 详情弹窗 */}
      <Modal title={t('cacheManager.detailTitle')} open={detailModal.open}
        onCancel={() => setDetailModal({ open: false, data: null, loading: false })}
        footer={null} width={650}>
        {detailModal.loading ? (
          <div className="text-center py-8">{t('cacheManager.loading')}</div>
        ) : detailModal.data && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 text-sm">
              <Tag color={REGION_COLORS[detailModal.data.region] || 'default'}>
                {detailModal.data.region}
              </Tag>
              <Tag>{t('cacheManager.type')}: {detailModal.data.value_type}</Tag>
              <Tag>{t('cacheManager.size')}: {(detailModal.data.size_bytes / 1024).toFixed(1)} KB</Tag>
              {detailModal.data.item_count != null && (
                <Tag>{t('cacheManager.count')}: {detailModal.data.item_count}</Tag>
              )}
            </div>
            <div className="text-xs text-gray-500 break-all font-mono">
              Key: {detailModal.data.key}
            </div>
            <pre className="bg-gray-50 dark:bg-gray-800 p-3 rounded text-xs overflow-auto max-h-96 whitespace-pre-wrap break-all">
              {typeof detailModal.data.value === 'object'
                ? JSON.stringify(detailModal.data.value, null, 2)
                : String(detailModal.data.value)}
            </pre>
          </div>
        )}
      </Modal>
    </Modal>
  )
}

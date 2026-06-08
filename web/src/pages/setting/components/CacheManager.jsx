import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Card, Table, Space, Tag, Button, Select, Input, Modal,
  Statistic, Row, Col, Popconfirm, message, Empty, Tooltip,
} from 'antd'
import {
  DatabaseOutlined, DeleteOutlined, ReloadOutlined,
  SearchOutlined, EyeOutlined, ClearOutlined,
} from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../../store'
import {
  getCacheStats, getCacheList, clearCache, deleteCacheKey, getCacheDetail,
} from '../../../apis'

const REGION_COLORS = {
  search: 'blue', metadata: 'green', episodes: 'orange',
  comments: 'purple', default: 'default', ai: 'red',
}

export const CacheManager = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const [stats, setStats] = useState({ total: 0, regions: {} })
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [statsLoading, setStatsLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [region, setRegion] = useState('all')
  const [searchText, setSearchText] = useState('')
  const [detailModal, setDetailModal] = useState({ open: false, data: null, loading: false })

  const loadStats = useCallback(async () => {
    try {
      setStatsLoading(true)
      const res = await getCacheStats()
      setStats(res.data || { total: 0, regions: {} })
    } catch { /* ignore */ } finally { setStatsLoading(false) }
  }, [])

  const loadList = useCallback(async (p = page, r = region, s = searchText) => {
    try {
      setLoading(true)
      const res = await getCacheList({ region: r, search: s || undefined, page: p, pageSize })
      setItems(res.data?.items || [])
      setTotal(res.data?.total || 0)
    } catch (err) {
      message.error(t('cacheManager.loadFailed', { error: err.message }))
    } finally { setLoading(false) }
  }, [page, region, searchText, pageSize, t])

  useEffect(() => { loadStats(); loadList(1) }, [])  // eslint-disable-line

  const handleRefresh = () => { loadStats(); loadList(1) }

  const handleRegionChange = (v) => { setRegion(v); setPage(1); loadList(1, v) }
  const handleSearch = (v) => { setSearchText(v); setPage(1); loadList(1, region, v) }
  const handlePageChange = (p) => { setPage(p); loadList(p) }

  const handleClearRegion = async (r) => {
    try {
      await clearCache(r || undefined)
      message.success(t('cacheManager.clearSuccess'))
      handleRefresh()
    } catch (err) { message.error(err.message) }
  }

  const handleDeleteKey = async (key, r) => {
    try {
      await deleteCacheKey(key, r)
      message.success(t('cacheManager.deleteSuccess'))
      handleRefresh()
    } catch (err) { message.error(err.message) }
  }

  const handleViewDetail = async (key, r) => {
    setDetailModal({ open: true, data: null, loading: true })
    try {
      const res = await getCacheDetail(key, r)
      setDetailModal({ open: true, data: res.data, loading: false })
    } catch (err) {
      message.error(err.message)
      setDetailModal({ open: false, data: null, loading: false })
    }
  }

  const regionOptions = [
    { label: t('cacheManager.allRegions'), value: 'all' },
    ...Object.keys(stats.regions).map(r => ({
      label: `${r} (${stats.regions[r]})`, value: r,
    })),
  ]

  const columns = [
    {
      title: t('cacheManager.region'), dataIndex: 'region', width: 110,
      render: (r) => <Tag color={REGION_COLORS[r] || 'default'}>{r}</Tag>,
    },
    {
      title: t('cacheManager.key'), dataIndex: 'key', ellipsis: true,
      render: (k) => <Tooltip title={k}><span className="font-mono text-xs">{k}</span></Tooltip>,
    },
    {
      title: t('cacheManager.preview'), dataIndex: 'value_preview', ellipsis: true,
      width: isMobile ? 120 : 300,
      render: (v) => <span className="text-xs text-gray-500 dark:text-gray-400">{v}</span>,
    },
    {
      title: t('cacheManager.actions'), width: 100, fixed: 'right',
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record.key, record.region)} />
          <Popconfirm title={t('cacheManager.confirmDelete')}
            onConfirm={() => handleDeleteKey(record.key, record.region)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {/* 统计卡片 */}
      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card size="small" loading={statsLoading}>
            <Statistic title={t('cacheManager.totalEntries')} value={stats.total}
              prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
        {Object.entries(stats.regions).map(([r, count]) => (
          <Col xs={12} sm={6} key={r}>
            <Card size="small" loading={statsLoading}>
              <Statistic title={r} value={count}
                prefix={<Tag color={REGION_COLORS[r] || 'default'} className="mr-1">{r[0]}</Tag>} />
            </Card>
          </Col>
        ))}
      </Row>

      {/* 工具栏 */}
      <Card size="small">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={region} onChange={handleRegionChange} options={regionOptions}
            style={{ minWidth: 140 }} size="small" />
          <Input.Search placeholder={t('cacheManager.searchPlaceholder')}
            onSearch={handleSearch} allowClear size="small"
            style={{ width: isMobile ? '100%' : 240 }} />
          <div className="flex-1" />
          <Space size="small">
            <Button icon={<ReloadOutlined />} size="small" onClick={handleRefresh}>
              {!isMobile && t('cacheManager.refresh')}
            </Button>
            <Popconfirm title={t('cacheManager.confirmClearAll')}
              onConfirm={() => handleClearRegion(region === 'all' ? null : region)}>
              <Button icon={<ClearOutlined />} size="small" danger>
                {!isMobile && (region === 'all'
                  ? t('cacheManager.clearAll')
                  : t('cacheManager.clearRegion', { region }))}
              </Button>
            </Popconfirm>
          </Space>
        </div>
      </Card>

      {/* 缓存列表 */}
      <Card size="small" bodyStyle={{ padding: 0 }}>
        <Table dataSource={items} columns={columns} loading={loading}
          rowKey={(r) => `${r.region}:${r.key}`} size="small"
          scroll={{ x: 600 }}
          locale={{ emptyText: <Empty description={t('cacheManager.noData')} /> }}
          pagination={{
            current: page, pageSize, total, showTotal: (t) => `${t}`,
            onChange: handlePageChange, size: 'small', showSizeChanger: false,
          }}
        />
      </Card>

      {/* 详情弹窗 */}
      <Modal title={t('cacheManager.detailTitle')} open={detailModal.open}
        onCancel={() => setDetailModal({ open: false, data: null, loading: false })}
        footer={null} width={isMobile ? '95%' : 700}>
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
    </div>
  )
}

import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, Row, Col, Statistic, Progress, Tag, Spin, Descriptions } from 'antd'
import {
  DatabaseOutlined,
  CloudServerOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  HddOutlined,
} from '@ant-design/icons'
import { getDatabaseInfo } from '../../../apis'

// 数据库类型 → 显示信息
const DB_TYPE_MAP = {
  mysql: { label: 'MySQL', color: '#00758f' },
  postgresql: { label: 'PostgreSQL', color: '#336791' },
  sqlite: { label: 'SQLite', color: '#003b57' },
}

// 缓存后端类型 → 显示信息
const getCacheTypeMap = (t) => ({
  redis: { label: 'Redis', color: '#dc382d' },
  memory: { label: t('dbInfo.cacheMemory'), color: '#52c41a' },
  hybrid: { label: t('dbInfo.cacheHybrid'), color: '#1677ff' },
  database: { label: t('dbInfo.cacheDatabase'), color: '#722ed1' },
})

/** 格式化运行时长 */
const formatUptime = (seconds, t) => {
  if (!seconds && seconds !== 0) return '-'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return t('dbInfo.uptimeDays', { d, h })
  if (h > 0) return t('dbInfo.uptimeHours', { h, m })
  return t('dbInfo.uptimeMinutes', { m })
}

/**
 * 数据库与缓存连接信息面板 — 图表 + 详细信息
 */
export const DatabaseInfoPanel = () => {
  const { t } = useTranslation()
  const [info, setInfo] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { loadInfo() }, [])

  const loadInfo = async () => {
    try {
      const res = await getDatabaseInfo()
      setInfo(res.data)
    } catch (err) {
      console.error('获取数据库信息失败:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 mb-5 py-4">
        <Spin size="small" />
        <span className="text-gray-400 text-sm">{t('dbInfo.loadingText')}</span>
      </div>
    )
  }

  if (!info) return null

  const dbMeta = DB_TYPE_MAP[info.dbType] || { label: info.dbType, color: '#666' }
  const cacheMeta = getCacheTypeMap(t)[info.cacheBackend] || { label: info.cacheBackend, color: '#666' }

  // 连接池使用率
  // overflow 负数表示基础池还有空余槽位（如 -3 表示还有3个槽位没创建连接）
  const actualOverflow = Math.max(0, info.dbOverflow)
  const totalPool = info.dbPoolSize + info.dbMaxOverflow
  const poolUsed = info.dbActiveConnections + actualOverflow
  const poolPercent = totalPool > 0 ? Math.min(100, Math.round((poolUsed / totalPool) * 100)) : 0

  // Redis 内存使用率
  const memPercent = (info.redisMemoryMaxBytes && info.redisMemoryMaxBytes > 0)
    ? Math.round((info.redisMemoryUsedBytes / info.redisMemoryMaxBytes) * 100)
    : null

  const showRedis = info.redisConnected

  return (
    <div className="mb-5">
      <Row gutter={[16, 16]} align="stretch">
        {/* ========== 数据库 Card ========== */}
        <Col xs={24} lg={showRedis ? 12 : 24} className="flex">
          <Card
            size="small"
            className="w-full"
            title={
              <span className="inline-flex items-center gap-2">
                <DatabaseOutlined />
                <span>{t('dbInfo.titleDatabase')}</span>
                <Tag color={dbMeta.color} className="!ml-1">{dbMeta.label}</Tag>
              </span>
            }
            extra={
              <ReloadOutlined
                className="cursor-pointer text-gray-400 hover:text-blue-500 transition-colors"
                onClick={loadInfo}
              />
            }
          >
            <Row gutter={16} align="middle">
              {/* 连接池环形图 */}
              <Col xs={8} sm={6} className="text-center">
                <Progress
                  type="dashboard"
                  percent={poolPercent}
                  size={80}
                  strokeColor={poolPercent > 80 ? '#ff4d4f' : poolPercent > 50 ? '#faad14' : '#52c41a'}
                  format={(p) => <span className="text-xs font-medium">{p}%</span>}
                />
                <div className="text-xs text-gray-400 mt-1">{t('dbInfo.labelPool')}</div>
              </Col>
              {/* 统计数字 */}
              <Col xs={16} sm={18}>
                <Row gutter={[12, 8]}>
                  <Col span={6}>
                    <Statistic title={t('dbInfo.statActive')} value={info.dbActiveConnections} valueStyle={{ fontSize: 20, color: '#1677ff' }} />
                  </Col>
                  <Col span={6}>
                    <Statistic title={t('dbInfo.statIdle')} value={info.dbIdleConnections} valueStyle={{ fontSize: 20, color: '#52c41a' }} />
                  </Col>
                  <Col span={6}>
                    <Statistic title={t('dbInfo.statUnused')} value={Math.max(0, info.dbPoolSize - info.dbActiveConnections - info.dbIdleConnections)} valueStyle={{ fontSize: 20, color: '#d9d9d9' }} />
                  </Col>
                  <Col span={6}>
                    <Statistic title={t('dbInfo.statOverflow')} value={actualOverflow} valueStyle={{ fontSize: 20, color: actualOverflow > 0 ? '#faad14' : undefined }} />
                  </Col>
                </Row>
              </Col>
            </Row>
            {/* 连接详情 */}
            <Descriptions size="small" column={2} className="mt-3" colon={false}>
              <Descriptions.Item label={t('dbInfo.labelAddress')}>{info.dbHost}:{info.dbPort}</Descriptions.Item>
              <Descriptions.Item label={t('dbInfo.labelDbName')}>{info.dbName}</Descriptions.Item>
              <Descriptions.Item label={t('dbInfo.labelPoolSize')}>{info.dbPoolSize}</Descriptions.Item>
              <Descriptions.Item label={t('dbInfo.labelMaxOverflow')}>{info.dbMaxOverflow}</Descriptions.Item>
              <Descriptions.Item label={t('dbInfo.labelRecycleTime')}>{info.dbPoolRecycle}s</Descriptions.Item>
              <Descriptions.Item label={t('dbInfo.labelPoolType')}>{info.dbPoolType}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>

        {/* ========== 缓存 Card ========== */}
        <Col xs={24} lg={showRedis ? 12 : 24} className="flex">
          <Card
            size="small"
            className="w-full"
            title={
              <span className="inline-flex items-center gap-2">
                {showRedis ? <CloudServerOutlined /> : <HddOutlined />}
                <span>{t('dbInfo.titleCache')}</span>
                <Tag color={showRedis ? '#dc382d' : cacheMeta.color} className="!ml-1">
                  {showRedis ? 'Redis' : cacheMeta.label}
                </Tag>
                {showRedis && (
                  <Tag
                    icon={<CheckCircleOutlined />}
                    color="success"
                    className="!text-xs"
                  >
                    {t('dbInfo.tagConnected')}
                  </Tag>
                )}
              </span>
            }
          >
            {showRedis ? (
              <>
                <Row gutter={16} align="middle">
                  {/* 内存使用环形图 */}
                  <Col xs={8} sm={6} className="text-center">
                    <Progress
                      type="dashboard"
                      percent={memPercent ?? 0}
                      size={80}
                      strokeColor={
                        memPercent == null ? '#d9d9d9'
                          : memPercent > 80 ? '#ff4d4f'
                          : memPercent > 50 ? '#faad14'
                          : '#52c41a'
                      }
                      format={() => (
                        <span className="text-xs font-medium">
                          {memPercent != null ? `${memPercent}%` : 'N/A'}
                        </span>
                      )}
                    />
                    <div className="text-xs text-gray-400 mt-1">{t('dbInfo.labelMemory')}</div>
                  </Col>
                  {/* Redis 统计 */}
                  <Col xs={16} sm={18}>
                    <Row gutter={[12, 8]}>
                      <Col span={8}>
                        <Statistic
                          title={t('dbInfo.statKeys')}
                          value={info.redisTotalKeys ?? 0}
                          valueStyle={{ fontSize: 20, color: '#1677ff' }}
                        />
                      </Col>
                      <Col span={8}>
                        <Statistic
                          title={t('dbInfo.statClients')}
                          value={info.redisConnectedClients ?? 0}
                          valueStyle={{ fontSize: 20 }}
                        />
                      </Col>
                      <Col span={8}>
                        <Statistic
                          title={t('dbInfo.statUptime')}
                          value={formatUptime(info.redisUptimeSeconds, t)}
                          valueStyle={{ fontSize: 14 }}
                        />
                      </Col>
                    </Row>
                  </Col>
                </Row>
                {/* Redis 详情 */}
                <Descriptions size="small" column={2} className="mt-3" colon={false}>
                  <Descriptions.Item label={t('dbInfo.labelAddress')}>{info.redisUrl || '-'}</Descriptions.Item>
                  <Descriptions.Item label={t('dbInfo.labelVersion')}>{info.redisVersion || '-'}</Descriptions.Item>
                  <Descriptions.Item label={t('dbInfo.labelMemUsed')}>{info.redisMemoryUsed || '-'}</Descriptions.Item>
                  <Descriptions.Item label={t('dbInfo.labelMemMax')}>{info.redisMemoryMax || t('dbInfo.labelMemMaxUnlimited')}</Descriptions.Item>
                  <Descriptions.Item label={t('dbInfo.labelCacheBackend')}>{info.cacheBackend}</Descriptions.Item>
                  <Descriptions.Item label={t('dbInfo.labelCacheMode')}>
                    {info.cacheBackend === 'redis' ? t('dbInfo.cacheModeRedis') : t('dbInfo.cacheModeHybrid')}
                  </Descriptions.Item>
                </Descriptions>
              </>
            ) : (
              <div className="py-4 text-center text-gray-400">
                {info.cacheBackend === 'hybrid'
                  ? t('dbInfo.descHybrid')
                  : info.cacheBackend === 'memory'
                    ? t('dbInfo.descMemory')
                    : t('dbInfo.descDatabase')}
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
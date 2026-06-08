import { Card, Statistic, Tag, Row, Col, Progress, Tooltip, Spin, Modal } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getSystemHealthSummary } from '@/apis'
import {
  CheckCircleOutlined, WarningOutlined, CloseCircleOutlined,
  DatabaseOutlined, CloudServerOutlined, ThunderboltOutlined,
  SafetyCertificateOutlined, PlayCircleOutlined
} from '@ant-design/icons'

const HealthOverviewModal = ({ open, onClose }) => {
  const { t } = useTranslation()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (open) {
      setLoading(true)
      getSystemHealthSummary().then(res => {
        setData(res?.data)
        setLoading(false)
      }).catch(() => setLoading(false))
    }
  }, [open])

  const taskTotal = data ? Object.values(data.taskSummary || {}).reduce((a, b) => a + b, 0) : 0
  const taskSuccess = data ? (data.taskSummary?.completed || 0) + (data.taskSummary?.success || 0) : 0
  const taskFail = data ? (data.taskSummary?.failed || 0) + (data.taskSummary?.error || 0) : 0
  const scoreColor = data?.configScore >= 80 ? '#52c41a' : data?.configScore >= 50 ? '#faad14' : '#ff4d4f'

  return (
    <Modal title={t('healthOverview.title')} open={open} onCancel={onClose} footer={null} width={640}>
      {loading ? <Spin className="w-full flex justify-center py-8" /> : !data ? null : (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={12} sm={8}>
              <Statistic title={t('healthOverview.todayDanmaku')} value={data.todayNewDanmaku} prefix={<ThunderboltOutlined />} />
            </Col>
            <Col xs={12} sm={8}>
              <Statistic title={t('healthOverview.missingEp')} value={data.missingEpisodes} valueStyle={{ color: data.missingEpisodes > 0 ? '#faad14' : '#52c41a' }} prefix={<WarningOutlined />} />
            </Col>
            <Col xs={12} sm={8}>
              <Statistic title={t('healthOverview.taskSuccess')} value={taskSuccess} suffix={`/ ${taskTotal}`} prefix={<CheckCircleOutlined />} valueStyle={{ color: '#52c41a' }} />
            </Col>
            <Col xs={12} sm={8}>
              <Statistic title={t('healthOverview.taskFail')} value={taskFail} prefix={<CloseCircleOutlined />} valueStyle={{ color: taskFail > 0 ? '#ff4d4f' : '#52c41a' }} />
            </Col>
            <Col xs={12} sm={8}>
              <Tooltip title={`${data.scraperSummary?.enabled || 0} / ${data.scraperSummary?.total || 0}`}>
                <Statistic title={t('healthOverview.scrapers')} value={data.scraperSummary?.enabled || 0} suffix={`/ ${data.scraperSummary?.total || 0}`} prefix={<CloudServerOutlined />} />
              </Tooltip>
              {(data.scraperSummary?.unhealthy || 0) > 0 && <Tag color="orange" className="mt-1">{data.scraperSummary.unhealthy} {t('healthOverview.unhealthy')}</Tag>}
            </Col>
            <Col xs={12} sm={8}>
              <Tooltip title={`${t('healthOverview.configScore')}: ${data.configScore}%`}>
                <div className="text-center">
                  <div className="text-xs text-gray-500 mb-1">{t('healthOverview.configScore')}</div>
                  <Progress type="circle" percent={data.configScore} size={48} strokeColor={scoreColor} />
                </div>
              </Tooltip>
            </Col>
          </Row>
          {data.backupStatus?.lastBackup && (
            <div className="mt-3 text-xs text-gray-400">
              <SafetyCertificateOutlined className="mr-1" />
              {t('healthOverview.lastBackup')}: {new Date(data.backupStatus.lastBackup).toLocaleString()}
            </div>
          )}
        </>
      )}
    </Modal>
  )
}

export default HealthOverviewModal

import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Card, Button, Spin, Empty, Tag, Popconfirm, Tooltip } from 'antd'
import {
  DesktopOutlined,
  MobileOutlined,
  ClockCircleOutlined,
  GlobalOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  SafetyCertificateOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import { getUserSessions, revokeSession, revokeOtherSessions } from '../apis/index.js'
import { useMessage } from '../MessageContext'
import dayjs from 'dayjs'

/**
 * 解析 User-Agent 获取设备/浏览器信息
 */
const parseUserAgent = (ua, t) => {
  if (!ua) return { browser: t('sessionManager.unknownBrowser'), os: t('sessionManager.unknownOs'), isMobile: false }

  const isMobile = /Mobile|Android|iPhone|iPad/i.test(ua)

  // 解析浏览器
  let browser = t('sessionManager.unknownBrowser')
  if (ua.includes('Edg/')) browser = 'Edge'
  else if (ua.includes('Chrome/')) browser = 'Chrome'
  else if (ua.includes('Firefox/')) browser = 'Firefox'
  else if (ua.includes('Safari/') && !ua.includes('Chrome')) browser = 'Safari'
  else if (ua.includes('Opera') || ua.includes('OPR/')) browser = 'Opera'

  // 解析操作系统
  let os = t('sessionManager.unknownOs')
  if (ua.includes('Windows')) os = 'Windows'
  else if (ua.includes('Mac OS')) os = 'macOS'
  else if (ua.includes('Linux')) os = 'Linux'
  else if (ua.includes('Android')) os = 'Android'
  else if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS'

  return { browser, os, isMobile }
}

/**
 * 格式化时间显示
 */
const formatTime = (time) => {
  if (!time) return '-'
  return dayjs(time).format('YYYY-MM-DD HH:mm:ss')
}

/**
 * 计算过期状态
 */
const getExpireStatus = (expiresAt, isRevoked, t) => {
  if (isRevoked) return { text: t('sessionManager.revoked'), color: 'red' }
  if (!expiresAt) return { text: t('sessionManager.neverExpire'), color: 'green' }
  const now = dayjs()
  const expire = dayjs(expiresAt)
  if (expire.isBefore(now)) return { text: t('sessionManager.expired'), color: 'red' }
  const diff = expire.diff(now, 'day')
  if (diff < 1) return { text: t('sessionManager.expireInHours', { hours: expire.diff(now, 'hour') }), color: 'orange' }
  return { text: t('sessionManager.expireInDays', { days: diff }), color: 'blue' }
}

const SessionManager = ({ open, onClose }) => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [sessions, setSessions] = useState([])
  const [currentJti, setCurrentJti] = useState(null)
  const [revoking, setRevoking] = useState(null)
  const messageApi = useMessage()

  const fetchSessions = async () => {
    try {
      setLoading(true)
      const res = await getUserSessions()
      setSessions(res.data.sessions || [])
      setCurrentJti(res.data.currentJti)
    } catch (error) {
      messageApi.error(t('sessionManager.fetchFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      fetchSessions()
    }
  }, [open])

  const handleRevokeSession = async (sessionId) => {
    try {
      setRevoking(sessionId)
      await revokeSession(sessionId)
      messageApi.success(t('sessionManager.kickedDevice'))
      fetchSessions()
    } catch (error) {
      messageApi.error(error.response?.data?.detail || t('sessionManager.operationFailed'))
    } finally {
      setRevoking(null)
    }
  }

  const handleRevokeOthers = async () => {
    try {
      setRevoking('all')
      const res = await revokeOtherSessions()
      messageApi.success(t('sessionManager.kickedOthers', { count: res.data.revokedCount }))
      fetchSessions()
    } catch (error) {
      messageApi.error(error.response?.data?.detail || t('sessionManager.operationFailed'))
    } finally {
      setRevoking(null)
    }
  }

  // 过滤出有效会话（未撤销且未过期）
  const activeSessions = sessions.filter(s => !s.isRevoked && (!s.expiresAt || dayjs(s.expiresAt).isAfter(dayjs())))
  const otherActiveSessions = activeSessions.filter(s => s.jti !== currentJti)

  // 底部按钮
  const footerContent = otherActiveSessions.length > 0 && !loading ? (
    <div className="flex justify-end">
      <Popconfirm
        title={t('sessionManager.kickAllConfirm')}
        description={t('sessionManager.kickAllDesc', { count: otherActiveSessions.length })}
        onConfirm={handleRevokeOthers}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        icon={<ExclamationCircleOutlined style={{ color: 'red' }} />}
      >
        <Button
          type="primary"
          danger
          loading={revoking === 'all'}
        >
          {t('sessionManager.kickAll')}
        </Button>
      </Popconfirm>
    </div>
  ) : null

  return (
    <Modal
      title={t('sessionManager.title')}
      open={open}
      onCancel={onClose}
      footer={footerContent}
      width={700}
      styles={{ body: { maxHeight: '60vh', overflowY: 'auto' } }}
    >
      <div className="mb-4 text-gray-500 text-sm">
        {t('sessionManager.desc')}
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <Spin size="large" />
        </div>
      ) : activeSessions.length === 0 ? (
        <Empty description={t('sessionManager.noActiveSession')} />
      ) : (
        <div className="space-y-3">
          {activeSessions.map((session) => {
            const { browser, os, isMobile } = parseUserAgent(session.userAgent, t)
            const expireStatus = getExpireStatus(session.expiresAt, session.isRevoked, t)
            const isCurrent = session.jti === currentJti
            const isWhitelist = session.isWhitelist

            return (
              <Card
                key={session.id}
                size="small"
                className={`${isCurrent ? 'border-blue-400 border-2' : ''} ${isWhitelist ? 'bg-green-50 dark:bg-green-900/20' : ''}`}
              >
                <div className="flex justify-between items-start">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      {isWhitelist ? (
                        <Tooltip title={t('sessionManager.whitelistSession')}>
                          <SafetyCertificateOutlined className="text-green-500" />
                        </Tooltip>
                      ) : (
                        isMobile ? <MobileOutlined /> : <DesktopOutlined />
                      )}
                      <span className="font-medium">{browser} / {os}</span>
                      {isWhitelist && <Tag color="green" icon={<SafetyCertificateOutlined />}>{t('sessionManager.whitelist')}</Tag>}
                      {isCurrent && <Tag color="blue">{t('sessionManager.currentSession')}</Tag>}
                      <Tag color={expireStatus.color}>{expireStatus.text}</Tag>
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
                      <div className="flex items-center gap-1">
                        <GlobalOutlined />
                        <span>IP: {session.ipAddress || t('sessionManager.unknown')}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="ml-3.5">UA: {session.userAgent || t('sessionManager.unknown')}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <ClockCircleOutlined />
                        <span>{t('sessionManager.loginTime')}: {formatTime(session.createdAt)}</span>
                      </div>
                    </div>
                  </div>
                  {!isCurrent && (
                    <Popconfirm
                      title={t('sessionManager.kickConfirm')}
                      description={isWhitelist ? t('sessionManager.kickWhitelistDesc') : t('sessionManager.kickNormalDesc')}
                      onConfirm={() => handleRevokeSession(session.id)}
                      okText={t('common.confirm')}
                      cancelText={t('common.cancel')}
                    >
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        loading={revoking === session.id}
                      >
                        {t('sessionManager.kick')}
                      </Button>
                    </Popconfirm>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </Modal>
  )
}

export default SessionManager


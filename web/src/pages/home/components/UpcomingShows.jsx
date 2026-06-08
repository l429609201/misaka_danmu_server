import { Card, List, Tag, Empty, Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getUpcomingShows } from '@/apis'
import { CalendarOutlined, PlayCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'

const weekdayMap = { 1: 'Mon', 2: 'Tue', 3: 'Wed', 4: 'Thu', 5: 'Fri', 6: 'Sat', 7: 'Sun' }

export const UpcomingShows = () => {
  const { t } = useTranslation()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getUpcomingShows(7).then(res => {
      const d = res?.data
      setItems(Array.isArray(d) ? d : [])
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <Spin className="w-full flex justify-center py-4" />
  if (!items.length) return null

  return (
    <Card title={<><CalendarOutlined className="mr-2" />{t('upcoming.title')}</>} size="small" className="mb-4">
      <List
        size="small"
        dataSource={items.slice(0, 10)}
        renderItem={item => (
          <List.Item>
            <div className="flex items-center gap-2 w-full">
              <Tag color={item.dayLabel === 'today' ? 'green' : item.dayLabel === 'tomorrow' ? 'blue' : 'default'}>
                {item.dayLabel === 'today' ? t('upcoming.today') : item.dayLabel === 'tomorrow' ? t('upcoming.tomorrow') : `${item.daysUntil}${t('upcoming.daysLater')}`}
              </Tag>
              <span className="flex-1 truncate font-medium">{item.title} S{item.season}</span>
              <span className="text-xs text-gray-400">{weekdayMap[item.airWeekday]} {item.airTime || ''}</span>
              {item.latestHasDanmaku ? (
                <Tag color="green" icon={<PlayCircleOutlined />}>{t('upcoming.hasDanmaku')}</Tag>
              ) : (
                <Tag color="orange" icon={<CloseCircleOutlined />}>{t('upcoming.noDanmaku')}</Tag>
              )}
            </div>
          </List.Item>
        )}
      />
    </Card>
  )
}

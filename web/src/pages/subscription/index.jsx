import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { message, Card } from 'antd'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'
import {
  getWeeklyCalendar,
  syncSchedule,
  clearCalendarCache,
} from '../../apis'
import { CalendarView } from './CalendarView.jsx'
import { SubscriptionSearchBar } from './SubscriptionSearchBar.jsx'

// 订阅页：追番日历单视图 + 顶部常驻统一搜索区（创建 UP主/合集/番剧订阅）。
// 设计依据：Bilibili 番剧已并入追番日历周列，原「探索发现」视图已移除。
export const SubscriptionPage = () => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)

  // ---- 日历视图 state ----
  const [calendarData, setCalendarData] = useState({ weekly: {}, unscheduled: [], stats: {} })
  const [calendarLoading, setCalendarLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [calendarFilter, setCalendarFilter] = useState('all') // 默认全部
  const [selectedExtItems, setSelectedExtItems] = useState([])

  const fetchCalendar = useCallback(async () => {
    setCalendarLoading(true)
    try {
      const res = await getWeeklyCalendar()
      setCalendarData(res.data || res)
    } catch { message.error(t('calendar.loadFailed')) }
    finally { setCalendarLoading(false) }
  }, [t])

  useEffect(() => { fetchCalendar() }, [fetchCalendar])

  const handleSyncSchedule = async () => {
    setSyncing(true)
    try {
      const res = await syncSchedule()
      const d = res.data || res
      message.success(t('calendar.syncSuccess', { count: d.updatedCount }))
      fetchCalendar()
    } catch { message.error(t('calendar.syncFailed')) }
    finally { setSyncing(false) }
  }

  const handleClearCache = async () => {
    try {
      await clearCalendarCache()
      message.success(t('calendar.clearCacheSuccess'))
      fetchCalendar()
    } catch { message.error(t('calendar.clearCacheFailed')) }
  }

  return (
    <div className="container mx-auto px-4 my-6">
      <Card>
        {/* 页头 */}
        <div className="mb-4">
          <h1 className="text-2xl font-extrabold tracking-tight">{t('subscription.title', '订阅')}</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1.5">
            {t('subscription.pageDesc', '集中管理追番日历，自动追更新番。')}
          </p>
        </div>

        {/* 顶部常驻统一搜索区（创建 UP主/合集/番剧订阅） */}
        <div className="mb-4 p-3 rounded-lg bg-gray-50 dark:bg-gray-800/40">
          <SubscriptionSearchBar t={t} onSubscribed={() => fetchCalendar()} />
        </div>

        <CalendarView
          data={calendarData}
          loading={calendarLoading}
          isMobile={isMobile}
          t={t}
          filter={calendarFilter}
          onFilterChange={setCalendarFilter}
          syncing={syncing}
          onSync={handleSyncSchedule}
          onClearCache={handleClearCache}
          selectedExtItems={selectedExtItems}
          setSelectedExtItems={setSelectedExtItems}
          setCalendarData={setCalendarData}
        />
      </Card>
    </div>
  )
}

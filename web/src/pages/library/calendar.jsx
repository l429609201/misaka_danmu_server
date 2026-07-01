import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Spin, Empty, message, Tooltip } from 'antd'
import { ArrowLeftOutlined, SyncOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import dayjs from 'dayjs'

import { getWeeklyCalendar, syncBangumiSchedule } from '../../apis'
import { RoutePaths } from '../../general/RoutePaths'
import { MyIcon } from '../../components/MyIcon'
import { UpcomingShows } from '../home/components/UpcomingShows'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../../store/index.js'

const DAYS = ['calendar.mon', 'calendar.tue', 'calendar.wed', 'calendar.thu', 'calendar.fri', 'calendar.sat', 'calendar.sun']

export default function CalendarPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const isMobile = useAtomValue(isMobileAtom)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [data, setData] = useState({ weekly: {}, unscheduled: [], stats: {} })

  const todayWeekday = dayjs().day() === 0 ? 7 : dayjs().day() // 1=Mon...7=Sun

  const fetchData = useCallback(async () => {
    try {
      setLoading(true)
      const res = await getWeeklyCalendar()
      setData(res)
    } catch (e) {
      message.error(t('calendar.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => { fetchData() }, [fetchData])

  const handleSync = async () => {
    try {
      setSyncing(true)
      const res = await syncBangumiSchedule()
      message.success(t('calendar.syncSuccess', { count: res.updatedCount }))
      fetchData()
    } catch (e) {
      message.error(t('calendar.syncFailed'))
    } finally {
      setSyncing(false)
    }
  }

  const getPoster = (item) => item.poster || null

  const AnimeCard = ({ item, isToday }) => (
    <div className={`flex gap-2.5 p-2 rounded-xl transition cursor-default border ${isToday ? 'border-indigo-500/20 bg-indigo-500/4' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/2 hover:bg-gray-50 dark:hover:bg-white/4'}`}>
      {getPoster(item) ? <img src={getPoster(item)} alt={item.animeTitle} className="w-10 h-14 rounded-lg object-cover flex-shrink-0" /> : <div className="w-10 h-14 rounded-lg bg-gray-200/20 dark:bg-white/6 flex-shrink-0" />}
      <div className="min-w-0 flex-1">
        <Tooltip title={item.animeTitle} placement="topLeft">
          <div className="font-bold text-xs truncate cursor-default">{item.animeTitle}</div>
        </Tooltip>
        <div className="flex gap-1 mt-1 flex-wrap">
          {item.animeType !== 'movie' && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
          {item.latestEpisodeIndex != null && <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">EP{String(item.latestEpisodeIndex).padStart(2, '0')}</span>}
          <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-gray-500/8 text-gray-500 dark:text-gray-400">{item.providerName}</span>
        </div>
        {item.airTime && <div className="text-[10px] text-gray-500 dark:text-gray-400 mt-1">🕐 {item.airTime}</div>}
      </div>
    </div>
  )

  if (loading) return <div className="flex items-center justify-center h-[60vh]"><Spin size="large" /></div>

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(RoutePaths.LIBRARY)} className="w-8 h-8 rounded-xl flex items-center justify-center border border-gray-200 dark:border-white/6 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition">
            <ArrowLeftOutlined className="text-xs" />
          </button>
          <div>
            <h1 className="text-xl font-extrabold flex items-center gap-2">📅 {t('calendar.title')}</h1>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{t('calendar.desc')}</p>
          </div>
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-4 py-2 rounded-xl text-xs font-semibold border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/8 transition flex items-center gap-1.5 disabled:opacity-50"
        >
          <SyncOutlined spin={syncing} /> {t('calendar.syncBangumi')}
        </button>
      </div>

      {/* Stats */}
      <div className="flex gap-3 flex-wrap">
        <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
          <span className="text-gray-500 dark:text-gray-400">{t('calendar.statTotal')}</span> <strong className="text-indigo-400 ml-1">{data.stats.total || 0}</strong>
        </div>
        <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
          <span className="text-gray-500 dark:text-gray-400">{t('calendar.statScheduled')}</span> <strong className="text-emerald-400 ml-1">{data.stats.scheduled || 0}</strong>
        </div>
        {data.stats.unscheduled > 0 && (
          <div className="px-4 py-2 rounded-xl border border-gray-200 dark:border-white/6 bg-white dark:bg-[#1a1e2e] text-sm">
            <span className="text-gray-500 dark:text-gray-400">{t('calendar.statUnscheduled')}</span> <strong className="text-amber-400 ml-1">{data.stats.unscheduled}</strong>
          </div>
        )}
      </div>

      {/* 即将播出 */}
      <UpcomingShows />

      {/* Weekly Grid / Mobile List */}
      {data.stats.total === 0 ? (
        <Empty className="py-16" description={t('calendar.noData')} />
      ) : isMobile ? (
        /* Mobile: vertical day list */
        <div className="space-y-4">
          {[1,2,3,4,5,6,7].map(day => {
            const items = data.weekly[day] || []
            const isToday = day === todayWeekday
            return (
              <div key={day}>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`text-sm font-bold ${isToday ? 'text-indigo-400' : 'text-gray-500 dark:text-gray-400'}`}>{t(DAYS[day - 1])}</span>
                  {isToday && <span className="text-[9px] bg-indigo-500 text-white px-2 py-0.5 rounded-full font-semibold">TODAY</span>}
                  <span className="text-[10px] text-gray-400">{items.length}{t('calendar.unit')}</span>
                </div>
                {items.length === 0 ? (
                  <div className="text-center text-xs text-gray-400 py-4 border border-dashed border-gray-200 dark:border-white/6 rounded-xl">{t('calendar.noUpdate')}</div>
                ) : (
                  <div className="space-y-2">{items.map(item => <AnimeCard key={item.sourceId} item={item} isToday={isToday} />)}</div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        /* Desktop: 7-column grid */
        <div className="grid grid-cols-7 gap-3">
          {[1,2,3,4,5,6,7].map(day => {
            const items = data.weekly[day] || []
            const isToday = day === todayWeekday
            return (
              <div key={day} className={`rounded-2xl border overflow-hidden min-h-[200px] ${isToday ? 'border-indigo-500/30 bg-indigo-500/[0.03]' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/[0.02]'}`}>
                <div className={`px-3 py-2.5 border-b ${isToday ? 'border-indigo-500/20' : 'border-gray-100 dark:border-white/4'} flex items-center justify-between`}>
                  <span className={`text-xs font-bold ${isToday ? 'text-indigo-400' : 'text-gray-500 dark:text-gray-400'}`}>{t(DAYS[day - 1])}</span>
                  <div className="flex items-center gap-1.5">
                    {isToday && <span className="text-[8px] bg-indigo-500 text-white px-1.5 py-0.5 rounded-full font-bold">TODAY</span>}
                    <span className="text-[10px] text-gray-400 bg-gray-100 dark:bg-white/4 px-1.5 py-0.5 rounded-md">{items.length}</span>
                  </div>
                </div>
                <div className="p-2 space-y-2">
                  {items.length === 0 ? (
                    <div className="flex items-center justify-center h-28 text-gray-400 text-xs">{t('calendar.noUpdate')}</div>
                  ) : items.map(item => <AnimeCard key={item.sourceId} item={item} isToday={isToday} />)}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Unscheduled section */}
      {data.unscheduled.length > 0 && (
        <div className="rounded-2xl border border-amber-500/20 bg-amber-500/[0.03] p-4">
          <div className="text-sm font-bold text-amber-400 mb-3 flex items-center gap-2">
            ⚠️ {t('calendar.unscheduledTitle')} ({data.unscheduled.length})
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">{t('calendar.unscheduledDesc')}</p>
          <div className={`grid ${isMobile ? 'grid-cols-1' : 'grid-cols-3 lg:grid-cols-4'} gap-2`}>
            {data.unscheduled.map(item => <AnimeCard key={item.sourceId} item={item} isToday={false} />)}
          </div>
        </div>
      )}
    </div>
  )
}

import { useMemo, useState } from 'react'
import { Badge, Button, Card, Empty, EmptyTitle, EmptyDescription, SegmentedControl, StatusDot, cn } from '../../components/anibt'

/**
 * 时间表视图（仿 AniBT 放送时间表）：顶部 7 天日期条 + 当日汇总 + 类型筛选 + 按播出时间排序的列表。
 * 只负责展示，数据过滤/订阅/选择逻辑全部由 CalendarView 传入，保证两种视图行为一致。
 */

const DAYS_SHORT = ['calendar.mon', 'calendar.tue', 'calendar.wed', 'calendar.thu', 'calendar.fri', 'calendar.sat', 'calendar.sun']

// 类型分段：tv_series / movie / 其余（ova、web 等）
const TYPE_FILTERS = ['all', 'tv', 'movie', 'other']

const matchType = (item, type) => {
  if (type === 'all') return true
  const at = item.animeType || 'tv_series'
  if (type === 'tv') return at === 'tv_series'
  if (type === 'movie') return at === 'movie'
  return at !== 'tv_series' && at !== 'movie'
}

// "HH:MM" → 分钟数；非法或缺失返回 null（视为时间未定）
const toMinutes = (time) => {
  const m = /^(\d{1,2}):(\d{2})/.exec(time || '')
  return m ? Number(m[1]) * 60 + Number(m[2]) : null
}

/** 计算第 offset 天（0 = 今天）的 Date 与 ISO 星期（1-7） */
const dayInfo = (offset) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return { date: d, weekday: d.getDay() === 0 ? 7 : d.getDay() }
}

export function ScheduleView({
  weekly, unscheduled, filterItems, t, isMobile,
  getPoster, getTitle, isSelected, onToggleSelect, onSubscribe, onUnsubscribe, isSubscribing,
}) {
  const [offset, setOffset] = useState(0)
  const [type, setType] = useState('all')

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => {
    const info = dayInfo(i)
    return { ...info, offset: i, items: filterItems(weekly?.[info.weekday]) }
  }), [weekly, filterItems])

  const current = days[offset]
  const nowMinutes = new Date().getHours() * 60 + new Date().getMinutes()

  // 当前选中日：按类型过滤后，有时间的按时间升序，时间未定的放最后
  const list = useMemo(() => {
    const items = current.items.filter(i => matchType(i, type))
    return [...items].sort((a, b) => (toMinutes(a.airTime) ?? 9999) - (toMinutes(b.airTime) ?? 9999))
  }, [current, type])

  // 当日汇总：仅今天区分已播/待播，其他日期全部算待播
  const summary = useMemo(() => {
    let aired = 0, upcoming = 0, unknown = 0
    for (const i of list) {
      const m = toMinutes(i.airTime)
      if (m === null) unknown++
      else if (offset === 0 && m <= nowMinutes) aired++
      else upcoming++
    }
    return { aired, upcoming, unknown }
  }, [list, offset, nowMinutes])

  const unschedFiltered = useMemo(
    () => filterItems(unscheduled || []).filter(i => matchType(i, type)),
    [unscheduled, filterItems, type],
  )

  // 下一部待播（仅今天），用于高亮「即将播出」
  const nextKey = useMemo(() => {
    if (offset !== 0) return null
    const next = list.find(i => (toMinutes(i.airTime) ?? -1) > nowMinutes)
    return next ? (next.sourceId || `${next.origin}-${next.externalId || next.bangumiId || next.traktId}`) : null
  }, [list, offset, nowMinutes])

  const keyOf = (item, idx) => item.sourceId || `${item.origin}-${item.externalId || item.bangumiId || item.traktId || idx}`

  const renderRow = (item, idx, { showTime = true } = {}) => {
    const key = keyOf(item, idx)
    const m = toMinutes(item.airTime)
    const aired = offset === 0 && m !== null && m <= nowMinutes
    const subscribed = item.isLocal || item.isSubscribed
    const selected = !item.isLocal && isSelected(item)
    const poster = getPoster(item)
    const ep = item.latestEpisodeIndex
      ? (item.episodeCount ? `${item.latestEpisodeIndex}/${item.episodeCount}` : `${item.latestEpisodeIndex}`)
      : (item.episodeCount ? `?/${item.episodeCount}` : null)
    return (
      <li key={key}
        className={cn(
          'flex items-center gap-3 px-3 py-2.5 transition-colors',
          selected ? 'bg-ab-accent' : 'hover:bg-ab-muted/60',
          !item.isLocal && 'cursor-pointer',
        )}
        onClick={!item.isLocal ? () => onToggleSelect(item) : undefined}
      >
        {showTime && (
          <div className="w-12 shrink-0 text-center">
            <div className={cn('text-[13px] font-bold tabular-nums', aired ? 'text-ab-muted-foreground' : 'text-ab-foreground')}>
              {item.airTime || '--:--'}
            </div>
            {key === nextKey && <Badge variant="candy" className="mt-0.5">{t('calendar.scheduleNext', '即将')}</Badge>}
          </div>
        )}
        <div className="h-14 w-10 shrink-0 overflow-hidden rounded-md bg-ab-muted shadow-[0_0_0_1px_var(--ab-border)]">
          {poster && <img src={poster} alt="" loading="lazy" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className={cn('truncate text-[13px] font-semibold', aired ? 'text-anibt-ink2' : 'text-ab-foreground')}>{getTitle(item)}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {item.isLocal
              ? <Badge variant="success">{t('calendar.filterLocal')}</Badge>
              : <Badge>{item.origin}</Badge>}
            {item.animeType !== 'movie' && item.season ? <Badge variant="lang">{t('libraryGroup.seasonTag', { season: item.season })}</Badge> : null}
            {ep && <Badge variant="count-quiet">EP {ep}</Badge>}
            {aired && <span className="inline-flex items-center gap-1 text-[11px] text-ab-muted-foreground"><StatusDot variant="ok" />{t('calendar.scheduleAired', '已播')}</span>}
          </div>
        </div>
        <div className="shrink-0" onClick={e => e.stopPropagation()}>
          {subscribed
            ? <Button size="sm" variant="outline" onClick={() => onUnsubscribe(item)}>{t('calendar.scheduleSubscribed', '已订阅')}</Button>
            : <Button size="sm" disabled={isSubscribing(item)} onClick={() => onSubscribe(item)}>{t('calendar.subscribeAction')}</Button>}
        </div>
      </li>
    )
  }

  return (
    <div className="space-y-3">
      {/* 7 天日期条：移动端横向滑动 */}
      <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 scrollbar-thin" role="tablist">
        {days.map(d => {
          const active = d.offset === offset
          return (
            <button key={d.offset} type="button" role="tab" aria-selected={active}
              onClick={() => setOffset(d.offset)}
              className={cn(
                'flex min-w-[64px] flex-1 shrink-0 cursor-pointer flex-col items-center gap-0.5 rounded-xl border-0 px-2 py-2 transition-[transform,box-shadow,background-color] duration-150',
                active
                  ? 'bg-ab-card text-ab-primary shadow-[inset_0_0_0_1.5px_var(--ab-primary),0_1.5px_0_var(--ab-primary)]'
                  : 'bg-ab-muted text-anibt-ink2 shadow-[inset_0_0_0_1px_var(--ab-border)] hover:-translate-y-px',
              )}
            >
              <span className="text-[11px] font-semibold">{d.offset === 0 ? t('calendar.scheduleToday', '今天') : t(DAYS_SHORT[d.weekday - 1])}</span>
              <span className="text-lg leading-none font-bold tabular-nums">{d.date.getDate()}</span>
              <span className="text-[10px] tabular-nums opacity-70">{d.items.length}</span>
            </button>
          )
        })}
      </div>

      {/* 当日汇总 + 类型筛选 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          {offset === 0 && <Badge variant="success">{t('calendar.scheduleAired', '已播')} {summary.aired}</Badge>}
          <Badge variant="warning">{t('calendar.scheduleUpcoming', '待播')} {summary.upcoming}</Badge>
          <Badge>{t('calendar.scheduleUnknown', '时间未定')} {summary.unknown}</Badge>
        </div>
        <SegmentedControl
          size={isMobile ? 'default' : 'compact'}
          value={type}
          onValueChange={setType}
          options={TYPE_FILTERS.map(v => ({ value: v, label: t(`calendar.scheduleType_${v}`, { all: '全部', tv: 'TV', movie: '剧场版', other: '其他' }[v]) }))}
        />
      </div>

      <Card className="gap-0 overflow-hidden py-0">
        {list.length === 0
          ? (
            <Empty className="py-10">
              <EmptyTitle>{t('calendar.noUpdate')}</EmptyTitle>
              <EmptyDescription>{t('calendar.scheduleEmptyHint', '换一天或切换类型看看')}</EmptyDescription>
            </Empty>
          )
          : <ul className="m-0 list-none divide-y divide-ab-border p-0">{list.map((i, idx) => renderRow(i, idx))}</ul>}
      </Card>

      {unschedFiltered.length > 0 && (
        <Card className="gap-0 overflow-hidden py-0">
          <div className="border-b border-dashed border-ab-border px-3 py-2 text-[12px] font-bold text-anibt-ink2">
            {t('calendar.unscheduledTitle', '其他作品')} <Badge variant="count-quiet" className="ml-1">{unschedFiltered.length}</Badge>
          </div>
          <ul className="m-0 list-none divide-y divide-ab-border p-0">
            {unschedFiltered.map((i, idx) => renderRow(i, idx, { showTime: false }))}
          </ul>
        </Card>
      )}
    </div>
  )
}

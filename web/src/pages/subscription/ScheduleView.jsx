import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Empty, EmptyTitle, EmptyDescription, SegmentedControl, cn } from '../../components/anibt'
import './schedule.css'

/**
 * 时间表视图（仿 anibt.net「今日放送」）：
 * 页头（今日放送 · 日期 · 部数）→ 7 天日期卡 → 「现在」时间轴 + 下一部卡片 → 海报网格。
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

const pad = (n) => String(n).padStart(2, '0')
const fmtMinutes = (m) => `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`

/** 计算第 offset 天（0 = 今天）的 Date 与 ISO 星期（1-7） */
const dayInfo = (offset) => {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  return { date: d, weekday: d.getDay() === 0 ? 7 : d.getDay() }
}

/** 每分钟刷新一次「现在」，让已播/待播与时间轴随时间推进 */
const useNowMinutes = () => {
  const calc = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes() }
  const [now, setNow] = useState(calc)
  useEffect(() => {
    const id = setInterval(() => setNow(calc()), 60 * 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

const StarIcon = () => (
  <svg viewBox="0 0 24 24" className="ab-poster-star" aria-hidden="true">
    <path d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z" />
  </svg>
)

const BellIcon = ({ on }) => (
  <svg viewBox="0 0 24 24" width="15" height="15" fill={on ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
)

export function ScheduleView({
  weekly, unscheduled, filterItems, t, isMobile,
  getPoster, getTitle, isSelected, onToggleSelect, onSubscribe, onUnsubscribe, isSubscribing,
}) {
  const [offset, setOffset] = useState(0)
  const [type, setType] = useState('all')
  const nowMinutes = useNowMinutes()

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => {
    const info = dayInfo(i)
    return { ...info, offset: i, items: filterItems(weekly?.[info.weekday]).filter(it => matchType(it, type)) }
  }), [weekly, filterItems, type])

  const current = days[offset]
  const isToday = offset === 0

  // 当前选中日：有时间的按时间升序，时间未定的放最后
  const list = useMemo(
    () => [...current.items].sort((a, b) => (toMinutes(a.airTime) ?? 9999) - (toMinutes(b.airTime) ?? 9999)),
    [current],
  )

  // 当日汇总：仅今天区分已播/待播，其他日期全部算待播
  const summary = useMemo(() => {
    let aired = 0, upcoming = 0, unknown = 0
    for (const i of list) {
      const m = toMinutes(i.airTime)
      if (m === null) unknown++
      else if (isToday && m <= nowMinutes) aired++
      else upcoming++
    }
    return { aired, upcoming, unknown }
  }, [list, isToday, nowMinutes])

  const unschedFiltered = useMemo(
    () => filterItems(unscheduled || []).filter(i => matchType(i, type)),
    [unscheduled, filterItems, type],
  )

  const keyOf = (item, idx) => item.sourceId || `${item.origin}-${item.externalId || item.bangumiId || item.traktId || idx}`

  // 下一部待播（仅今天）：用于时间轴右侧卡片与海报高亮
  const next = useMemo(
    () => (isToday ? list.find(i => (toMinutes(i.airTime) ?? -1) > nowMinutes) || null : null),
    [list, isToday, nowMinutes],
  )
  const nextKey = next ? keyOf(next, list.indexOf(next)) : null

  // 时间轴窗口：今天以「现在」为中心取 6 小时（对齐整点），其他日期覆盖当日首末节目
  const timeline = useMemo(() => {
    const times = list.map(i => toMinutes(i.airTime)).filter(m => m !== null)
    let start, end
    if (isToday) {
      start = Math.max(0, Math.floor(nowMinutes / 60) * 60 - 60)
      end = Math.min(24 * 60, start + 6 * 60)
      start = end - 6 * 60
    } else if (times.length) {
      start = Math.floor(Math.min(...times) / 60) * 60
      end = Math.min(24 * 60, Math.max(start + 60, Math.ceil((Math.max(...times) + 1) / 60) * 60))
    } else {
      start = 0; end = 24 * 60
    }
    const pos = (m) => `${((m - start) / (end - start)) * 100}%`
    // 同一时间只画一个刻度
    const ticks = [...new Set(times.filter(m => m >= start && m <= end))]
    return { start, end, pos, ticks }
  }, [list, isToday, nowMinutes])

  const minutesUntil = next ? toMinutes(next.airTime) - nowMinutes : 0
  const fmtUntil = (m) => (m >= 60
    ? t('calendar.scheduleInHours', { h: Math.floor(m / 60), m: m % 60, defaultValue: `${Math.floor(m / 60)} 小时 ${m % 60} 分钟后` })
    : t('calendar.scheduleInMinutes', { m, defaultValue: `${m} 分钟后` }))

  const epText = (item) => {
    const n = item.latestEpisodeIndex
    if (n) return t('calendar.scheduleEpisode', { n, defaultValue: `第${n}集` })
    if (item.episodeCount) return t('calendar.scheduleEpisodeTotal', { n: item.episodeCount, defaultValue: `全${item.episodeCount}集` })
    return null
  }

  // 副标题：原名 / 别名，取第一个与主标题不同的值
  const subTitle = (item, title) => {
    const cands = [item.titleJp, item.nameJp, item.originalTitle, item.animeTitle, item.titleZh]
    return cands.find(s => s && s !== title) || ''
  }

  const renderPoster = (item, idx, { showTime = true } = {}) => {
    const key = keyOf(item, idx)
    const m = toMinutes(item.airTime)
    const aired = isToday && m !== null && m <= nowMinutes
    const isNext = key === nextKey
    const subscribed = item.isLocal || item.isSubscribed
    const selected = !item.isLocal && isSelected(item)
    const poster = getPoster(item)
    const title = getTitle(item)
    const ep = epText(item)
    const rating = Number(item.rating) > 0 ? Number(item.rating).toFixed(1) : null

    // 状态行：已入库 → 绿；下一部 → 蓝闪；已播 → 绿；待播 → 灰
    let status
    if (item.isLocal) status = { tone: 'ok', text: t('calendar.filterLocal', '已入库') }
    else if (isNext) status = { tone: 'next', text: `${t('calendar.scheduleNext', '下一部')} · ${fmtUntil(minutesUntil)}` }
    else if (aired) status = { tone: 'ok', text: t('calendar.scheduleAired', '已播') }
    else if (m === null) status = { tone: 'none', text: t('calendar.scheduleUnknown', '时间未定') }
    else status = { tone: 'wait', text: t('calendar.schedulePending', '待播出') }

    return (
      <div key={key}
        className="ab-poster"
        data-tone={isNext ? 'next' : undefined}
        data-selected={selected || undefined}
        data-aired={aired || undefined}
        data-clickable={!item.isLocal || undefined}
        onClick={!item.isLocal ? () => onToggleSelect(item) : undefined}
      >
        <div className="ab-poster-cover">
          {poster && <img src={poster} alt="" loading="lazy" />}
          {showTime && <span className="ab-poster-badge" data-side="left" data-tone="ink">{item.airTime || '--:--'}</span>}
          {!item.isLocal && item.origin && <span className="ab-poster-badge" data-side="right">{item.origin}</span>}
        </div>
        <div className="ab-poster-body">
          <div className="ab-poster-title" title={title}>{title}</div>
          <div className="ab-poster-sub">{subTitle(item, title)}</div>
          <div className="ab-poster-foot">
            <div className="ab-poster-meta">
              {ep && <span>{ep}</span>}
              {ep && rating && <span className="ab-poster-sep" />}
              {rating && <span className="inline-flex items-center gap-0.5"><StarIcon />{rating}</span>}
              {!ep && !rating && item.animeType !== 'movie' && item.season
                ? <span>{t('libraryGroup.seasonTag', { season: item.season })}</span> : null}
            </div>
            <span className="ab-status pr-8" data-tone={status.tone}>{status.text}</span>
          </div>
        </div>
        <button type="button"
          className="ab-poster-action"
          data-on={subscribed || undefined}
          disabled={!subscribed && isSubscribing(item)}
          title={subscribed ? t('calendar.scheduleSubscribed', '已订阅') : t('calendar.subscribeAction')}
          aria-label={subscribed ? t('calendar.scheduleSubscribed', '已订阅') : t('calendar.subscribeAction')}
          onClick={e => { e.stopPropagation(); subscribed ? onUnsubscribe(item) : onSubscribe(item) }}
        >
          <BellIcon on={subscribed} />
        </button>
      </div>
    )
  }

  const weekdayLabel = (d) => t(DAYS_SHORT[d.weekday - 1])
  const nextSubscribed = next && (next.isLocal || next.isSubscribed)

  return (
    <div className="space-y-4">
      {/* 页头：今日放送 · 日期 · 部数 + 类型筛选 */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="inline-flex size-10 items-center justify-center rounded-xl bg-ab-card text-ab-primary shadow-[0_0_0_1px_var(--ab-border),var(--anibt-shadow-sticker)]">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="4" width="18" height="18" rx="3" /><path d="M16 2v4M8 2v4M3 10h18" />
            </svg>
          </span>
          <div>
            <div className="text-xl leading-tight font-extrabold text-ab-foreground">
              {isToday ? t('calendar.scheduleTitleToday', '今日放送') : t('calendar.scheduleTitleDay', '当日放送')}
            </div>
            <div className="text-[13px] text-anibt-ink2 tabular-nums">
              {`${current.date.getMonth() + 1}/${current.date.getDate()} ${weekdayLabel(current)} · `}
              {t('calendar.scheduleCount', { n: list.length, defaultValue: `${list.length} 部` })}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={offset === 0} aria-label="prev" onClick={() => setOffset(o => Math.max(0, o - 1))}>‹</Button>
          <Button size="sm" variant="outline" disabled={offset === 6} aria-label="next" onClick={() => setOffset(o => Math.min(6, o + 1))}>›</Button>
          <SegmentedControl
            size={isMobile ? 'default' : 'compact'}
            value={type}
            onValueChange={setType}
            options={TYPE_FILTERS.map(v => ({ value: v, label: t(`calendar.scheduleType_${v}`, { all: '全部', tv: 'TV', movie: '剧场版', other: '其他' }[v]) }))}
          />
        </div>
      </div>

      {/* 7 天日期卡：移动端横向滑动 */}
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pt-2.5 pb-1 scrollbar-thin" role="tablist">
        {days.map(d => (
          <button key={d.offset} type="button" role="tab" aria-selected={d.offset === offset}
            className="ab-day" onClick={() => setOffset(d.offset)}>
            {d.offset === 0 && <span className="ab-day-today">{t('calendar.scheduleToday', '今天')}</span>}
            <span className="ab-day-num">{d.date.getDate()}</span>
            <span className="ab-day-week">{weekdayLabel(d)}</span>
            <span className="ab-day-count">{t('calendar.scheduleCount', { n: d.items.length, defaultValue: `${d.items.length} 部` })}</span>
          </button>
        ))}
      </div>

      {/* 「现在」+ 时间轴 + 下一部 */}
      <Card className="gap-0 p-4">
        <div className="flex flex-col gap-4 md:flex-row md:items-center">
          <div className="shrink-0">
            <div className="text-[12px] font-semibold text-anibt-ink2">{isToday ? t('calendar.scheduleNow', '现在') : weekdayLabel(current)}</div>
            <div className="text-[28px] leading-none font-extrabold tabular-nums text-anibt-time-ink">
              {isToday ? fmtMinutes(nowMinutes) : `${current.date.getMonth() + 1}/${current.date.getDate()}`}
            </div>
            <div className="mt-1 text-[12px] text-anibt-ink2 tabular-nums">
              {isToday && <>{t('calendar.scheduleAired', '已播')} {summary.aired} · </>}
              {t('calendar.scheduleUpcoming', '待播')} {summary.upcoming}
              {summary.unknown > 0 && <> · {t('calendar.scheduleUnknown', '时间未定')} {summary.unknown}</>}
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex justify-between text-[11px] text-ab-muted-foreground tabular-nums">
              <span>{isToday ? t('calendar.scheduleToday', '今天') : weekdayLabel(current)}</span>
              <span>{fmtMinutes(timeline.start)} - {timeline.end >= 1440 ? '24:00' : fmtMinutes(timeline.end)}</span>
            </div>
            <div className="ab-timeline">
              <div className="ab-timeline-track" />
              {isToday && <div className="ab-timeline-past" style={{ width: timeline.pos(Math.min(nowMinutes, timeline.end)) }} />}
              {timeline.ticks.map(m => (
                <span key={m}>
                  <span className="ab-timeline-tick" data-aired={(isToday && m <= nowMinutes) || undefined} style={{ left: timeline.pos(m) }} />
                  <span className="ab-timeline-label" style={{ left: timeline.pos(m) }}>{fmtMinutes(m)}</span>
                </span>
              ))}
              {isToday && <span className="ab-timeline-now" style={{ left: timeline.pos(nowMinutes) }} />}
            </div>
          </div>

          {next && (
            <div className="flex shrink-0 items-center gap-3 rounded-xl bg-anibt-time-wash p-2.5 shadow-[0_0_0_1.5px_var(--anibt-time-line)] md:max-w-[340px]">
              <div className="h-14 w-10 shrink-0 overflow-hidden rounded-md bg-ab-muted">
                {getPoster(next) && <img src={getPoster(next)} alt="" className="h-full w-full object-cover" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-semibold text-anibt-time-ink tabular-nums">
                  ▶ {t('calendar.scheduleNext', '下一部')} · {next.airTime} · {fmtUntil(minutesUntil)}
                </div>
                <div className="truncate text-[14px] font-bold text-ab-foreground">{getTitle(next)}</div>
                <div className="truncate text-[12px] text-anibt-ink2">{[epText(next), next.origin].filter(Boolean).join(' · ')}</div>
              </div>
              {nextSubscribed
                ? <Button size="sm" variant="outline" onClick={() => onUnsubscribe(next)}>{t('calendar.scheduleSubscribed', '已订阅')}</Button>
                : <Button size="sm" disabled={isSubscribing(next)} onClick={() => onSubscribe(next)}>{t('calendar.subscribeAction')}</Button>}
            </div>
          )}
        </div>
      </Card>

      {/* 海报网格 */}
      {list.length === 0
        ? (
          <Card className="py-0">
            <Empty className="py-10">
              <EmptyTitle>{t('calendar.noUpdate')}</EmptyTitle>
              <EmptyDescription>{t('calendar.scheduleEmptyHint', '换一天或切换类型看看')}</EmptyDescription>
            </Empty>
          </Card>
        )
        : <div className="ab-poster-grid">{list.map((i, idx) => renderPoster(i, idx))}</div>}

      {unschedFiltered.length > 0 && (
        <div className="space-y-2">
          <div className={cn('flex items-center gap-2 text-[13px] font-bold text-anibt-ink2')}>
            {t('calendar.unscheduledTitle', '其他作品')}
            <span className="rounded-md bg-ab-muted px-1.5 text-[11px] tabular-nums">{unschedFiltered.length}</span>
          </div>
          <div className="ab-poster-grid">{unschedFiltered.map((i, idx) => renderPoster(i, idx, { showTime: false }))}</div>
        </div>
      )}
    </div>
  )
}

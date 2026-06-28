import React, { useEffect, useState, useRef, useMemo } from 'react'
import { Input, Spin, Empty, message, Popover, Modal, Tooltip, Checkbox, Dropdown, Button } from 'antd'
import { SyncOutlined, DeleteOutlined, SearchOutlined, FilterOutlined, DownOutlined } from '@ant-design/icons'
import {
  subscribeCalendarItem,
  batchSubscribeCalendarItems,
  unsubscribeCalendarItem,
} from '../../apis'

// ---- Calendar View ----
// 说明：本组件从原 batch-manage.jsx 迁移而来（订阅分页拆分，阶段 A）。
// 仅负责「日历订阅」视图与订阅/取消订阅 Modal，不含本地源批量管理逻辑。

// 数据源显示名：未知源回退首字母大写（新增源零改动，自动适配）
const getProviderLabel = (key) =>
  key ? key.charAt(0).toUpperCase() + key.slice(1) : ''

// 由 provider 名哈希出稳定色相（0~359）→ 同一源每次渲染颜色固定，新增源自动分配，无需写死
const getProviderHue = (key) => {
  const s = key || 'unknown'
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return h
}

// 基于哈希色相生成各场景的 inline 样式（角标/chip/统计数字/圆点/卡片边框）
const getProviderColor = (key) => {
  const h = getProviderHue(key)
  return {
    badge: { backgroundColor: `hsl(${h} 70% 50% / 0.85)`, color: '#fff' },
    chip: { backgroundColor: `hsl(${h} 70% 50% / 0.15)`, color: `hsl(${h} 70% 60%)` },
    value: { color: `hsl(${h} 70% 60%)` },
    dot: { backgroundColor: `hsl(${h} 70% 50% / 0.85)` },
    edge: { borderColor: `hsl(${h} 70% 55% / 0.3)`, backgroundColor: `hsl(${h} 70% 50% / 0.03)` },
  }
}

// 纯函数：稳定的 item 唯一 key
// 设计：必须保证不同 entry 不撞 key（否则 isSelected 命中错条目，点 A 连选 B）。
// 优先用本地 sourceId（最稳）→ provider+externalId → provider+bangumiId/traktId →
// 最后兜底 origin+title+airWeekday+airTime（不同时间段同名也能区分）。
const getItemKey = (item) => {
  if (item.sourceId) return `local-${item.sourceId}`
  const ns = item.provider || item.origin || 'ext'
  if (item.externalId) return `${ns}-ext-${item.externalId}`
  if (item.bangumiId) return `${ns}-bgm-${item.bangumiId}`
  if (item.traktId) return `${ns}-trakt-${item.traktId}`
  // 兜底：用 origin + title + 播出星期 + airTime 拼 key，避免同名不同条目撞 key
  return `${ns}-t-${item.animeTitle || ''}-${item.airWeekday || 0}-${item.airTime || ''}`
}

const DAYS_KEYS = ['calendar.mon', 'calendar.tue', 'calendar.wed', 'calendar.thu', 'calendar.fri', 'calendar.sat', 'calendar.sun']

// 媒体类型 → 展示文案：movie/tv 走 i18n，ova/ona 直显缩写，其它兜底大写（新增类型零改动）
const getTypeLabel = (type, t) => {
  if (!type) return ''
  const v = String(type).toLowerCase()
  if (v === 'movie') return t('calendar.movie')
  if (v === 'tv' || v === 'tv_series') return t('calendar.tvSeries')
  if (v === 'ova') return 'OVA'
  if (v === 'ona') return 'ONA'
  return v.toUpperCase()
}

// ============ CalCard：海报卡片（顶层组件 + React.memo，防止父组件重渲染时 <img> 重新挂载导致海报重复请求 307） ============
const CalCard = React.memo(function CalCard({
  item, isToday, horizontal, day, isMobile, selected, t,
  posterSrc, displayTitle, displayYear, countdown,
  onToggleSelect, onSubscribe, onUnsubscribe, isSubscribing,
}) {
  const isExternal = !item.isLocal
  const selectedStyle = selected ? {
    backgroundColor: 'color-mix(in srgb, var(--color-primary) 15%, transparent)',
    borderColor: 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
    boxShadow: '0 0 0 1px color-mix(in srgb, var(--color-primary) 30%, transparent)',
  } : undefined
  const baseStyle = selected
    ? 'ring-2 ring-inset'
    : isExternal
      ? 'border-dashed opacity-80'
      : (isToday ? 'border-indigo-200 dark:border-indigo-500/20 bg-indigo-50 dark:bg-indigo-500/4' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/2 hover:bg-gray-50 dark:hover:bg-white/4')
  // 外部卡边框/底色用 provider 哈希色（inline style，未知源也能自动着色）
  const edgeStyle = (!selected && isExternal) ? getProviderColor(item.origin).edge : undefined

  // 竖版海报卡（行内横滑，仿小幻影视）
  if (horizontal) {
    const cardWidth = isMobile ? 'w-44' : 'w-36'
    return (
      <div
        className={`group/card ${cardWidth} flex-shrink-0 rounded-xl overflow-visible border transition relative ${baseStyle} cursor-pointer hover:border-indigo-400/50 dark:hover:border-indigo-500/40`}
        style={{
          ...(edgeStyle || {}),
          ...(selected ? { ...selectedStyle, '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)' } : {}),
        }}
        onClick={() => onToggleSelect(item)}
      >
        {/* 选中 ✓ 角标：负偏移突出到卡片右上角边框上（外层 overflow-visible 才能露出） */}
        {selected && <div className="absolute -top-2 -right-2 w-5 h-5 rounded-full flex items-center justify-center shadow-md ring-2 ring-white dark:ring-gray-900 z-20" style={{ backgroundColor: 'var(--color-primary)' }}><span className="text-white text-[10px] font-bold">✓</span></div>}
        <div className="relative w-full aspect-[2/3] bg-gray-200 dark:bg-white/6 rounded-t-xl overflow-hidden">
          {posterSrc
            ? <img src={posterSrc} loading="lazy" alt="" onError={e => { e.currentTarget.style.visibility = 'hidden' }} className="w-full h-full object-cover" />
            : <div className="w-full h-full flex items-center justify-center text-gray-400 text-2xl">🎬</div>}
          {/* 左上角评分 */}
          {item.rating && <span className="absolute top-1 left-1 text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-black/55 text-yellow-400">★{item.rating}</span>}
          {/* 右上角来源角标：本地卡时按「本地 → 外部源」自上而下竖排 */}
          {item.isLocal && (
            <div className="absolute top-1 right-1 flex flex-col gap-0.5 items-end">
              <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-md bg-emerald-500/85 text-white">{t('calendar.local') || '本地'}</span>
              {item.externalSources?.map((es, i) => {
                return (
                  <span key={i} className="text-[9px] font-bold px-1.5 py-0.5 rounded-md flex items-center gap-0.5" style={getProviderColor(es.origin).badge}
                        title={[es.animeTitle || es.titleZh, es.platformWatchStatus === 'watching' ? t('calendar.platformWatching') : (es.platformWatchStatus === 'wish' ? t('calendar.platformWishlist') : '')].filter(Boolean).join(' · ')}>
                    {getProviderLabel(es.origin)}
                    {es.platformWatchStatus === 'watching' ? '⭐' : (es.platformWatchStatus === 'wish' ? '📌' : '')}
                  </span>
                )
              })}
            </div>
          )}
          {/* 移动端：左下角倒计时徽章 */}
          {countdown && (
            <span className={`absolute bottom-9 left-1 text-[9px] font-extrabold px-1.5 py-0.5 rounded-md backdrop-blur-sm ${countdown.isNow ? 'bg-emerald-500/85 text-white' : 'bg-indigo-500/85 text-white'}`}>
              {countdown.isNow ? countdown.text : `${countdown.text}${countdown.unit}`}
            </span>
          )}
          {/* 来源角标（外部条目，颜色由 provider 哈希生成，新源自动着色） */}
          {!item.isLocal && (
            <span className="absolute top-1 right-1 text-[9px] font-bold px-1.5 py-0.5 rounded-md" style={getProviderColor(item.origin).badge}>{getProviderLabel(item.origin)}</span>
          )}
          {/* 平台「我在追/想看」徽章（OAuth 账号下的私人状态，与本地订阅独立）
              位置：来源角标下方一点，避开右上角选中勾 */}
          {!item.isLocal && (item.platformWatchStatus === 'watching' || item.platformWatchStatus === 'wish') && (
            <span className={`absolute top-7 right-1 text-[9px] font-bold px-1.5 py-0.5 rounded-md ${item.platformWatchStatus === 'watching' ? 'bg-amber-500/90 text-white' : 'bg-sky-500/85 text-white'} flex items-center gap-0.5`}
                  title={item.platformWatchStatus === 'watching' ? t('calendar.platformWatching') : t('calendar.platformWishlist')}>
              {item.platformWatchStatus === 'watching' ? '⭐' : '📌'}
              {item.platformWatchedEpisodes ? ` EP${String(item.platformWatchedEpisodes).padStart(2, '0')}` : ''}
            </span>
          )}
          {/* 底部悬浮：左侧进度条+集数（自适应），右下角订阅/已订阅 */}
          <div className="absolute bottom-0 inset-x-0 px-1.5 pb-1.5 pt-4 bg-gradient-to-t from-black/70 to-transparent flex items-center gap-1.5">
            {(() => {
              const cur = item.latestEpisodeIndex ?? 0
              const total = item.episodeCount || null
              const pct = total ? Math.min(100, Math.round((cur / total) * 100)) : 8
              if (item.latestEpisodeIndex == null && !total) return <div className="flex-1" />
              return (
                <div className="flex-1 min-w-0 flex items-center gap-1">
                  <div className="flex-1 h-1 rounded-full bg-white/25 overflow-hidden">
                    <div className="h-full rounded-full bg-indigo-400" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-[8px] font-semibold text-white/90 whitespace-nowrap">{cur}/{total ?? '∞'}</span>
                </div>
              )
            })()}
            {/* 本地订阅状态：isLocal（已在追更）或 isSubscribed（外部条目对应本地有匹配）→ 都视为「已订阅」 */}
            {(item.isLocal || item.isSubscribed)
              ? <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/90 text-white whitespace-nowrap cursor-pointer hover:bg-red-500/90 transition"
                  onClick={(e) => { e.stopPropagation(); onUnsubscribe(item) }}
                  title={t('calendar.unsubscribeAction')}
                >{t('calendar.subscribed')}</span>
              : <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/90 text-white cursor-pointer hover:bg-indigo-500 transition whitespace-nowrap"
                  onClick={(e) => { e.stopPropagation(); onSubscribe(item) }} title={t('calendar.subscribeAction')}>
                  {isSubscribing ? '⏳' : '➕'} {t('calendar.subscribeAction')}
                </span>}
          </div>
        </div>
        {/* 底部番名 + 小标签 */}
        <div className="p-1.5">
          <Tooltip title={displayTitle} placement="topLeft"><div className="font-bold text-[11px] leading-tight line-clamp-2 h-[28px]">{displayTitle}</div></Tooltip>
          {item.isLocal && item.externalTitles?.length > 0 && (
            <Tooltip title={item.externalTitles.join(' / ')} placement="topLeft">
              <div className="text-[10px] text-gray-400 truncate mt-0.5">↔ {item.externalTitles.slice(0, 2).join(' / ')}</div>
            </Tooltip>
          )}

          {/* 信息标签区：统一圆角胶囊（年份 / 类型 / 季度 / 播出星期 / 开播时间），有则展示，缺省自动省略 */}
          <div className="flex items-center gap-1 mt-1 flex-wrap">
            {/* 年份 */}
            {displayYear && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-gray-500/10 text-gray-500 dark:text-gray-400">{displayYear}</span>}
            {/* 媒体类型（movie/tv/ova...） */}
            {(item.animeType || item.type) && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-purple-500/10 text-purple-500 dark:text-purple-400">{getTypeLabel(item.animeType || item.type, t)}</span>}
            {/* 季度（外部条目 + 本地非电影条目） */}
            {!item.isLocal && item.season && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
            {item.isLocal && item.animeType !== 'movie' && item.season && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
            {/* 播出星期（airWeekday: 1=周一...7=周日，有则展示） */}
            {item.airWeekday >= 1 && item.airWeekday <= 7 && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-500 dark:text-sky-400">{t(DAYS_KEYS[item.airWeekday - 1])}</span>}
            {/* 开播时间 */}
            {item.airTime && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-500 dark:text-amber-400">🕐 {item.airTime}</span>}
          </div>
        </div>
      </div>
    )
  }

  // 横版小卡（unscheduled PC 端使用）
  return (
    <div
      className={`flex gap-2.5 p-2 rounded-xl transition border relative ${baseStyle} ${isExternal ? 'cursor-pointer hover:border-indigo-400/50 dark:hover:border-indigo-500/40' : 'cursor-default'}`}
      style={selected ? { ...selectedStyle, '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)' } : edgeStyle}
      onClick={isExternal ? () => onToggleSelect(item) : undefined}
    >
      {selected && (
        <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10"
          style={{ backgroundColor: 'var(--color-primary)' }}>
          <span className="text-white text-[10px] font-bold">✓</span>
        </div>
      )}
      {posterSrc ? <img src={posterSrc} loading="lazy" alt="" onError={e => { e.currentTarget.style.visibility = 'hidden' }} className="w-10 h-14 rounded-lg object-cover flex-shrink-0 bg-gray-200 dark:bg-white/6" /> : <div className="w-10 h-14 rounded-lg bg-gray-200 dark:bg-white/6 flex-shrink-0" />}
      <div className="min-w-0 flex-1">
        <Tooltip title={item.animeTitle} placement="topLeft"><div className="font-bold text-xs truncate">{item.animeTitle}</div></Tooltip>
        {item.isLocal && item.externalTitles?.length > 0 && (
          <Tooltip title={item.externalTitles.join(' / ')} placement="topLeft">
            <div className="text-[10px] text-gray-400 truncate mt-0.5">↔ {item.externalTitles.slice(0, 2).join(' / ')}</div>
          </Tooltip>
        )}

        <div className="flex gap-1 mt-1 flex-wrap">
          {/* 外部条目来源 chip（颜色由 provider 哈希生成，新源自动着色） */}
          {!item.isLocal && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={getProviderColor(item.origin).chip}>{getProviderLabel(item.origin)}</span>
          )}
          {/* 本地条目：自身绑定的 ID + externalSources 都按哈希色渲染 */}
          {item.isLocal && item.bangumiId && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={getProviderColor('bangumi').chip}>{getProviderLabel('bangumi')}</span>}
          {item.isLocal && item.traktId && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={getProviderColor('trakt').chip}>{getProviderLabel('trakt')}</span>}
          {item.isLocal && item.externalSources?.map((es, i) => {
            return <span key={`${es.origin}-${i}`} className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={getProviderColor(es.origin).chip} title={es.animeTitle || es.titleZh}>{getProviderLabel(es.origin)}</span>
          })}

          {/* 平台「我在追/想看」徽章（OAuth 账号下的私人状态，与本地订阅独立） */}
          {!item.isLocal && item.platformWatchStatus === 'watching' && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500" title={t('calendar.platformWatching')}>
              ⭐{item.platformWatchedEpisodes ? ` EP${String(item.platformWatchedEpisodes).padStart(2, '0')}` : ''}
            </span>
          )}
          {!item.isLocal && item.platformWatchStatus === 'wish' && (
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-500" title={t('calendar.platformWishlist')}>📌</span>
          )}
          {(item.isLocal || item.isSubscribed)
            ? <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 cursor-pointer hover:text-red-400 hover:bg-red-500/10 transition"
                onClick={(e) => { e.stopPropagation(); onUnsubscribe(item) }}
                title={t('calendar.unsubscribeAction')}
              >{t('calendar.subscribed')}</span>
            : <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 cursor-pointer hover:bg-indigo-500/20 transition"
                onClick={(e) => { e.stopPropagation(); onSubscribe(item) }} title={t('calendar.subscribeAction')}>
                {isSubscribing ? '⏳' : '➕'} {t('calendar.subscribeAction')}
              </span>
          }
          {item.isLocal && item.animeType !== 'movie' && item.season && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400">{t('libraryGroup.seasonTag', { season: item.season })}</span>}
          {item.latestEpisodeIndex != null && <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400">EP{String(item.latestEpisodeIndex).padStart(2, '0')}</span>}
          {item.providerName && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-gray-500/8 text-gray-500 dark:text-gray-400">{item.providerName}</span>}
          {item.rating && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-yellow-500/10 text-yellow-500">★{item.rating}</span>}
          {/* 年份 / 类型 / 播出星期 / 开播时间：统一圆角胶囊，有则展示 */}
          {(item.year || (item.origin === 'trakt' && item.traktTmdbId)) && getDisplayYear(item) && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-gray-500/10 text-gray-500 dark:text-gray-400">{getDisplayYear(item)}</span>}
          {(item.animeType || item.type) && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-purple-500/10 text-purple-500 dark:text-purple-400">{getTypeLabel(item.animeType || item.type, t)}</span>}
          {item.airWeekday >= 1 && item.airWeekday <= 7 && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-500 dark:text-sky-400">{t(DAYS_KEYS[item.airWeekday - 1])}</span>}
          {item.airTime && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-500 dark:text-amber-400">🕐 {item.airTime}</span>}
        </div>
      </div>
    </div>
  )
})

export const CalendarView = ({ data, loading, isMobile, t, filter = 'local', onFilterChange, syncing, onSync, onClearCache, selectedExtItems, setSelectedExtItems, setCalendarData }) => {
  const todayWeekday = new Date().getDay() === 0 ? 7 : new Date().getDay()
  const [searchKeyword, setSearchKeyword] = useState('')
  // 订阅确认 Modal 状态
  const [subscribeModalOpen, setSubscribeModalOpen] = useState(false)
  const [subscribingItem, setSubscribingItem] = useState(null)
  // 多源订阅：用户在确认框选中的源（availableSources 中的一项的 provider）；单源时为 null
  const [selectedSubSource, setSelectedSubSource] = useState(null)
  // 多源退订：本地卡有多个外部源时，弹框让用户选退订哪个源
  const [unsubModalOpen, setUnsubModalOpen] = useState(false)
  const [unsubItem, setUnsubItem] = useState(null)
  const [batchSubscribeModalOpen, setBatchSubscribeModalOpen] = useState(false)
  const [runNowChecked, setRunNowChecked] = useState(true)
  const [localizedTitles, setLocalizedTitles] = useState({}) // Trakt 中文标题/年份缓存 {tmdbId: {title, year}}
  // 7 天的横向滚动容器 ref（按 day 索引管理，避免在 renderDayRow 中用 useRef 触发组件类型问题）
  const dayScrollRefs = useRef({}) // { 1: HTMLDivElement, 2: ..., 7: ... }
  // 7 天的「内容是否可滑动」状态（决定是否显示 ‹ › 按钮）
  const [dayCanScroll, setDayCanScroll] = useState({}) // { 1: true, 2: false, ... }

  // 对 Trakt 条目按需拉取 TMDB 中文标题与年份（覆盖英文原标题）
  // 性能优化：并发池（8 路）+ localStorage 持久缓存，避免 350+ 个串行请求拖慢首屏
  useEffect(() => {
    if (!data?.weekly) return
    const ids = new Set()
    for (let d = 1; d <= 7; d++) {
      (data.weekly[d] || []).forEach(i => {
        if (i.origin === 'trakt' && i.traktTmdbId && localizedTitles[i.traktTmdbId] === undefined) ids.add(i.traktTmdbId)
      })
    }
    if (ids.size === 0) return
    let cancelled = false
    // 先尝试从 localStorage 读取已缓存的标题（24h 有效）
    const LS_KEY = 'calendar_tmdb_titles_v1'
    const LS_TTL = 24 * 3600 * 1000
    let lsCache = {}
    try {
      const raw = localStorage.getItem(LS_KEY)
      if (raw) {
        const parsed = JSON.parse(raw)
        if (parsed && parsed.ts && Date.now() - parsed.ts < LS_TTL) lsCache = parsed.data || {}
      }
    } catch {}
    // 已在 localStorage 中的直接命中
    const idsToFetch = []
    const fromCache = {}
    for (const id of ids) {
      if (lsCache[id]) fromCache[id] = lsCache[id]
      else idsToFetch.push(id)
    }
    if (Object.keys(fromCache).length > 0) {
      setLocalizedTitles(prev => ({ ...prev, ...fromCache }))
    }
    if (idsToFetch.length === 0) return
    // 并发池：同时跑 8 个请求，比串行快 ~8 倍
    ;(async () => {
      const POOL = 8
      const results = {}
      let cursor = 0
      const worker = async () => {
        while (cursor < idsToFetch.length && !cancelled) {
          const id = idsToFetch[cursor++]
          try {
            const resp = await fetch(`/api/ui/calendar/tmdb-title/${id}`)
            const json = await resp.json()
            results[id] = json || null
          } catch {
            results[id] = null
          }
        }
      }
      await Promise.all(Array.from({ length: POOL }, () => worker()))
      if (cancelled) return
      setLocalizedTitles(prev => ({ ...prev, ...results }))
      // 持久化到 localStorage
      try {
        const merged = { ...lsCache, ...results }
        localStorage.setItem(LS_KEY, JSON.stringify({ ts: Date.now(), data: merged }))
      } catch {}
    })()
    return () => { cancelled = true }
  }, [data])

  // 监听 7 天容器尺寸变化，按需更新 dayCanScroll（控制 ‹ › 按钮显隐）
  // 关键：依赖 [data, filter, searchKeyword]，items 变化时重建 ResizeObserver
  useEffect(() => {
    const observers = []
    const update = (day, el) => {
      if (!el) return
      const can = el.scrollWidth > el.clientWidth + 1
      setDayCanScroll(prev => prev[day] === can ? prev : { ...prev, [day]: can })
    }
    for (let d = 1; d <= 7; d++) {
      const el = dayScrollRefs.current[d]
      if (!el) continue
      update(d, el)
      const ro = new ResizeObserver(() => update(d, el))
      ro.observe(el)
      observers.push(ro)
    }
    return () => observers.forEach(ro => ro.disconnect())
  }, [data, filter, searchKeyword])

  // 取展示用标题/年份（Trakt 优先用 TMDB 中文标题）
  const getDisplayTitle = (item) => {
    if (item.origin === 'trakt' && item.traktTmdbId) {
      const loc = localizedTitles[item.traktTmdbId]
      if (loc?.title) return loc.title
    }
    return item.animeTitle
  }
  const getDisplayYear = (item) => {
    if (item.origin === 'trakt' && item.traktTmdbId) {
      const loc = localizedTitles[item.traktTmdbId]
      if (loc?.year) return loc.year
    }
    return item.year || null
  }

  const getCalPoster = (item) => {
    let src = item.localImagePath || item.imageUrl
    if (src?.startsWith('/images/')) src = src.replace('/images/', '/data/images/')
    // Trakt 番无现成海报时，走按需懒加载端点（浏览器仅加载视口内图片，单个失败不影响整体）
    if (!src && item.traktTmdbId) {
      src = `/api/ui/calendar/tmdb-poster/${item.traktTmdbId}`
    }
    return src
  }

  // 按 filter 过滤数据（filter='all'|'local'| <provider name>）
  // 通用规则：选中某 provider 时 → 显示该 provider 的外部条目 + 命中该 provider 的本地条目
  // 命中条件：isLocal && (item[`${provider}Id`] 存在 或 externalSources 中有 origin=provider)
  const filterItems = (items) => {
    if (!items) return []
    let filtered = items
    if (filter === 'local') {
      filtered = items.filter(i => i.isLocal)
    } else if (filter !== 'all') {
      const idField = `${filter}Id`  // bangumiId / traktId / ... 本地条目命名约定
      filtered = items.filter(i =>
        i.origin === filter
        || (i.isLocal && (i[idField] || (i.externalSources || []).some(es => es.origin === filter)))
      )
    }
    // 搜索关键词过滤
    if (searchKeyword.trim()) {
      const kw = searchKeyword.trim().toLowerCase()
      filtered = filtered.filter(i => {
        const titles = [
          i.animeTitle,
          i.titleZh,
          ...(i.externalTitles || []),
          ...((i.externalSources || []).flatMap(s => [s.animeTitle, s.titleZh])),
        ]
        return titles.some(title => (title || '').toLowerCase().includes(kw))
      })
    }
    return filtered
  }

  // 批量选择辅助（getItemKey 已提到组件外作为纯函数）
  const isSelected = (item) => selectedExtItems.some(s => getItemKey(s) === getItemKey(item))
  const toggleSelect = (item) => {
    const key = getItemKey(item)
    if (selectedExtItems.some(s => getItemKey(s) === key)) {
      setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== key))
    } else {
      setSelectedExtItems(prev => [...prev, item])
    }
  }
  const getFilteredExternalItems = () => {
    const allExt = []
    for (let day = 1; day <= 7; day++) {
      filterItems(data.weekly[day]).filter(i => !i.isLocal).forEach(i => {
        if (!allExt.some(e => getItemKey(e) === getItemKey(i))) allExt.push(i)
      })
    }
    return allExt
  }
  const filteredExternalItems = getFilteredExternalItems()
  const allFilteredExternalSelected = filteredExternalItems.length > 0
    && filteredExternalItems.every(i => selectedExtItems.some(s => getItemKey(s) === getItemKey(i)))
  // 全选/取消全选当前过滤后的外部番
  const toggleSelectAllExternal = () => {
    if (allFilteredExternalSelected) {
      const currentKeys = new Set(filteredExternalItems.map(i => getItemKey(i)))
      setSelectedExtItems(prev => prev.filter(s => !currentKeys.has(getItemKey(s))))
      return
    }
    setSelectedExtItems(prev => {
      const existing = new Set(prev.map(s => getItemKey(s)))
      const toAdd = filteredExternalItems.filter(i => !existing.has(getItemKey(i)))
      return [...prev, ...toAdd]
    })
  }

  // 订阅外部番（自动搜索并导入）
  const [subscribingKeys, setSubscribingKeys] = useState([])
  // 显示订阅确认 Modal
  const handleSubscribe = (item) => {
    setSubscribingItem(item)
    setRunNowChecked(true)
    // 多源时默认选中第一个源；单源/无 availableSources 时为 null（走 item 自身字段）
    const sources = item.availableSources || []
    setSelectedSubSource(sources.length > 1 ? (sources[0].provider || sources[0].origin) : null)
    setSubscribeModalOpen(true)
  }

  const patchCalendarItem = (target, patch) => {
    const isSame = (i) => getItemKey(i) === getItemKey(target)
      || (target.bangumiId && i.bangumiId && String(i.bangumiId) === String(target.bangumiId))
      || (target.traktId && i.traktId && String(i.traktId) === String(target.traktId))
      || (target.traktTmdbId && i.traktTmdbId && String(i.traktTmdbId) === String(target.traktTmdbId))
    setCalendarData(prev => {
      const patchList = (items = []) => items.map(i => isSame(i) ? { ...i, ...patch } : i)
      const weekly = {}
      for (const [day, items] of Object.entries(prev.weekly || {})) weekly[day] = patchList(items)
      return { ...prev, weekly, unscheduled: patchList(prev.unscheduled || []) }
    })
  }

  const patchCalendarItems = (targets, patch) => {
    targets.forEach(target => patchCalendarItem(target, patch))
  }

  // 确认订阅
  const handleConfirmSubscribe = async () => {
    if (!subscribingItem) return
    const item = subscribingItem
    const key = getItemKey(item)

    if (subscribingKeys.includes(key)) return
    setSubscribingKeys(prev => [...prev, key])
    setSubscribeModalOpen(false)
    setSubscribingItem(null)

    // 多源时取用户选中的源；否则回退 item 自身字段。源描述字段对齐后端 _build_source_descriptor
    const sources = item.availableSources || []
    const chosen = sources.length > 1
      ? (sources.find(s => (s.provider || s.origin) === selectedSubSource) || sources[0])
      : (sources[0] || null)
    const src = chosen || item

    try {
      await subscribeCalendarItem({
        animeTitle: src.animeTitle || item.animeTitle,
        mediaType: (src.mediaType || item.animeType) === 'movie' ? 'movie' : 'tv_series',
        season: src.season ?? item.season ?? null,
        traktTmdbId: (src.traktTmdbId || src.tmdbId) ? String(src.traktTmdbId || src.tmdbId) : null,
        traktId: src.traktId ? String(src.traktId) : null,
        bangumiId: src.bangumiId ? String(src.bangumiId) : null,
        provider: src.provider || src.origin || item.provider || item.origin || null,
        externalId: src.externalId || src.bangumiId || src.traktId || src.traktTmdbId || src.tmdbId || null,
        runNow: runNowChecked,
      })
      patchCalendarItem(item, { isSubscribed: true, subscriptionStatus: runNowChecked ? 'importing' : 'pending' })
      setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== getItemKey(item)))
      message.success(t('calendar.subscribeSubmitted', { title: item.animeTitle }))
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.subscribeFailed'))
    } finally {
      setSelectedSubSource(null)
      setSubscribingKeys(prev => prev.filter(k => k !== key))
    }
  }

  // 取消订阅/取消追更（统一调一次接口，后端内部处理本地+外部）
  const handleUnsubscribe = async (item) => {
    // 本地卡聚合了多个外部源时，先弹框让用户选退订哪个源
    if (item.isLocal && (item.externalSources?.length > 1)) {
      setUnsubItem(item)
      setUnsubModalOpen(true)
      return
    }
    // 单源/无外部源：取首个外部源（或纯本地）直接退订
    const externalTarget = item.isLocal && item.externalSources?.length > 0 ? item.externalSources[0] : null
    await doUnsubscribe(item, externalTarget)
  }

  // 实际退订：externalTarget 为 null 时退订本地源本身，否则退订指定外部源
  const doUnsubscribe = async (item, externalTarget) => {
    try {
      const payload = {
        provider: externalTarget?.provider || externalTarget?.origin || item.provider || item.origin || null,
        externalId: externalTarget?.externalId || item.externalId || null,
        bangumiId: (externalTarget?.bangumiId || item.bangumiId) ? String(externalTarget?.bangumiId || item.bangumiId) : null,
        traktId: (externalTarget?.traktId || item.traktId) ? String(externalTarget?.traktId || item.traktId) : null,
        traktTmdbId: (externalTarget?.tmdbId || item.traktTmdbId) ? String(externalTarget?.tmdbId || item.traktTmdbId) : null,
      }
      if (item.isLocal && item.sourceId && !externalTarget) {
        payload.sourceId = item.sourceId
      }
      if (!payload.externalId) {
        if (payload.provider === 'trakt') payload.externalId = payload.traktId || payload.traktTmdbId
        else if (payload.provider === 'bangumi') payload.externalId = payload.bangumiId
      }
      if (!payload.sourceId && !payload.provider && !payload.bangumiId && !payload.traktId && !payload.traktTmdbId) {
        message.warning(t('calendar.unsubscribeFailed'))
        return
      }

      await unsubscribeCalendarItem(payload)

      if (item.isLocal && !externalTarget) {
        setCalendarData(prev => {
          const newWeekly = {}
          for (const [day, items] of Object.entries(prev.weekly || {})) {
            newWeekly[day] = items.filter(i => i.sourceId !== item.sourceId)
          }
          const newUnscheduled = (prev.unscheduled || []).filter(i => i.sourceId !== item.sourceId)
          return { ...prev, weekly: newWeekly, unscheduled: newUnscheduled }
        })
      } else {
        patchCalendarItem(item, { isSubscribed: false, subscriptionStatus: null })
        setSelectedExtItems(prev => prev.filter(s => getItemKey(s) !== getItemKey(item)))
      }

      message.success(t('calendar.unsubscribeSuccess', { title: item.animeTitle }))
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.unsubscribeFailed'))
    }
  }

  const handleBatchSubscribe = () => {
    if (selectedExtItems.length === 0) return
    setRunNowChecked(true)
    setBatchSubscribeModalOpen(true)
  }

  const handleConfirmBatchSubscribe = async () => {
    const list = [...selectedExtItems]
    if (list.length === 0) return

    // 按父目标分组：同一合集/UP主的集聚合到一起
    const grouped = {}
    for (const it of list) {
      const parentKey = it.parentExternalId || it.externalId
      if (!grouped[parentKey]) {
        grouped[parentKey] = { parent: it, episodes: [] }
      }
      grouped[parentKey].episodes.push(it.externalId)
    }

    try {
      const requests = Object.values(grouped).map(g => ({
        animeTitle: g.parent.animeTitle,
        mediaType: g.parent.animeType === 'movie' ? 'movie' : 'tv_series',
        season: g.parent.season || null,
        traktTmdbId: g.parent.traktTmdbId ? String(g.parent.traktTmdbId) : null,
        traktId: g.parent.traktId ? String(g.parent.traktId) : null,
        bangumiId: g.parent.bangumiId ? String(g.parent.bangumiId) : null,
        provider: g.parent.origin || g.parent.provider || null,
        externalId: g.parent.parentExternalId || g.parent.externalId || g.parent.bangumiId || g.parent.traktId || g.parent.traktTmdbId || null,
        // 若有 parentExternalId，说明是合集下的集，传 selectedEpisodes 仅导入选中集
        selectedEpisodes: g.parent.parentExternalId ? g.episodes : null,
        runNow: runNowChecked,
      }))

      const res = await batchSubscribeCalendarItems({ runNow: runNowChecked, items: requests })
      const ok = res?.data?.successCount ?? requests.length
      message.success(t('calendar.batchSubscribeDone', { count: ok }))
      patchCalendarItems(list, { isSubscribed: true, subscriptionStatus: runNowChecked ? 'importing' : 'pending' })
      setSelectedExtItems([])
      setBatchSubscribeModalOpen(false)
    } catch (e) {
      message.error(e?.response?.data?.detail || t('calendar.subscribeFailed'))
    }
  }

  // 动态：从 stats 中提取所有有数据的外部源（local 单独处理）。新增源时前端零改动。
  // ⚠️ 必须放在所有 early return 之前，否则违反 React Hooks 调用顺序规则
  const availableProviders = useMemo(() => {
    const RESERVED = new Set(['total', 'local', 'scheduled', 'unscheduled'])
    return Object.keys(data.stats || {})
      .filter(k => !RESERVED.has(k) && (data.stats[k] || 0) > 0)
      .sort()
  }, [data.stats])

  // 过滤器下拉菜单项：[全部, 本地, ...各 provider]
  const filterMenu = useMemo(() => ({
    items: [
      { key: 'all', label: t('calendar.filterAll') },
      { key: 'local', label: t('calendar.filterLocal') },
      ...(availableProviders.length ? [{ type: 'divider' }] : []),
      ...availableProviders.map(p => ({
        key: p,
        label: (
          <span className="flex items-center gap-2">
            <span className="inline-block w-2 h-2 rounded-full" style={getProviderColor(p).dot} />
            {getProviderLabel(p)}
            <span className="text-[10px] text-gray-400 ml-auto">{data.stats[p] || 0}</span>
          </span>
        ),
      })),
    ],
    onClick: ({ key }) => onFilterChange(key),
  }), [availableProviders, data.stats, t, onFilterChange])

  if (loading) return <div className="flex items-center justify-center h-[40vh]"><Spin size="large" /></div>
  if (!data.stats?.total && !data.stats?.local) return <Empty className="py-16" description={t('calendar.noData')} />

  // CalCard 已提到组件外（顶层 + React.memo）。这里仅作辅助渲染：传入预计算好的 props
  const renderCalCard = (item, idx, { isToday = false, horizontal = false, day = null, keyPrefix = '' } = {}) => (
    <CalCard
      key={item.sourceId || `${keyPrefix}${item.origin}-${item.bangumiId || item.traktId}-${idx}`}
      item={item}
      isToday={isToday}
      horizontal={horizontal}
      day={day}
      isMobile={isMobile}
      selected={isSelected(item)}
      t={t}
      posterSrc={getCalPoster(item)}
      displayTitle={getDisplayTitle(item)}
      displayYear={getDisplayYear(item)}
      countdown={isMobile && day ? getCountdown(day) : null}
      onToggleSelect={toggleSelect}
      onSubscribe={handleSubscribe}
      onUnsubscribe={handleUnsubscribe}
      isSubscribing={subscribingKeys.includes(getItemKey(item))}
    />
  )

  const orderedDays = Array.from({ length: 7 }, (_, i) => ((todayWeekday - 1 + i) % 7) + 1)

  // 渲染一天的横向滚动行（普通函数 ✅ 不是 React 组件，所以不会因 CalendarView 重渲染产生新组件类型，
  // 内部 <CalCard>（顶层 + memo）的 DOM 节点会被 React 复用，<img> 不会重新挂载，海报也就不会重发请求）
  const renderDayRow = (day) => {
    const items = filterItems(data.weekly[day])
    const isToday = day === todayWeekday
    const canScroll = !!dayCanScroll[day]
    const scrollBy = (dir) => dayScrollRefs.current[day]?.scrollBy({ left: dir * 320, behavior: 'smooth' })
    // 全选/取消全选当天的外部番（filter ≠ local 才有意义）
    const externalItems = items.filter(i => !i.isLocal)
    const allDaySelected = externalItems.length > 0 && externalItems.every(i => isSelected(i))
    const toggleSelectDay = () => {
      if (allDaySelected) {
        const dayKeys = new Set(externalItems.map(i => getItemKey(i)))
        setSelectedExtItems(prev => prev.filter(s => !dayKeys.has(getItemKey(s))))
      } else {
        setSelectedExtItems(prev => {
          const existing = new Set(prev.map(s => getItemKey(s)))
          const toAdd = externalItems.filter(i => !existing.has(getItemKey(i)))
          return [...prev, ...toAdd]
        })
      }
    }
    return (
      <div key={day} className={`group rounded-2xl border overflow-hidden ${isToday ? 'border-indigo-200 dark:border-indigo-500/30 bg-indigo-50 dark:bg-indigo-500/[0.03]' : 'border-gray-200 dark:border-white/6 bg-white dark:bg-white/[0.02]'}`}>
        <div className={`px-3 py-2 border-b ${isToday ? 'border-indigo-200 dark:border-indigo-500/20' : 'border-gray-100 dark:border-white/4'} flex items-center gap-1.5`}>
          <span className={`text-xs font-bold ${isToday ? 'text-indigo-500 dark:text-indigo-400' : 'text-gray-600 dark:text-gray-400'}`}>{t(DAYS_KEYS[day - 1])}</span>
          {isToday && <span className="text-[8px] bg-indigo-500 text-white px-1.5 py-0.5 rounded-full font-bold">TODAY</span>}
          <span className="text-[10px] text-gray-400 bg-gray-100 dark:bg-white/4 px-1.5 py-0.5 rounded-md">{items.length}</span>
          {/* 当天全选按钮：filter ≠ local 且有外部番时显示。移动端 ml-auto 推到右侧 */}
          {filter !== 'local' && externalItems.length > 0 && (
            <button onClick={toggleSelectDay}
              className={`${isMobile ? 'ml-auto' : ''} text-[10px] font-medium px-2 py-0.5 rounded-md border transition ${allDaySelected ? 'bg-indigo-500/15 text-indigo-400 border-indigo-500/40' : 'border-gray-300 dark:border-white/10 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:border-indigo-400/40'}`}>
              {allDaySelected ? `☑ ${t('calendar.deselectDay')}` : `☐ ${t('calendar.selectDay')}`}
            </button>
          )}
          {/* 右侧滑动按钮（PC 端 + 内容可滑动时常驻显示；不可滑动隐藏） */}
          {!isMobile && canScroll && (
            <div className="ml-auto flex items-center gap-1">
              <button onClick={() => scrollBy(-1)} className="w-6 h-6 rounded-md flex items-center justify-center bg-white dark:bg-white/8 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/12 text-base leading-none">‹</button>
              <button onClick={() => scrollBy(1)} className="w-6 h-6 rounded-md flex items-center justify-center bg-white dark:bg-white/8 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/12 text-base leading-none">›</button>
            </div>
          )}
        </div>
        {items.length === 0
          ? <div className="flex items-center justify-center h-20 text-gray-400 text-xs">{t('calendar.noUpdate')}</div>
          : <div className="relative">
              <div ref={(el) => { dayScrollRefs.current[day] = el }} className="flex gap-2.5 p-2.5 overflow-x-auto scrollbar-thin">
                {items.map((item, idx) => renderCalCard(item, idx, { isToday, horizontal: true, day }))}
              </div>
            </div>}
      </div>
    )
  }

  // 倒计时计算
  const getCountdown = (day) => {
    const diff = day >= todayWeekday ? day - todayWeekday : 7 - todayWeekday + day
    if (diff === 0) return { text: t('calendar.justNow'), isNow: true }
    return { text: String(diff), unit: t('calendar.dayUnit'), isNow: false }
  }

  // 当前过滤器按钮的展示文案
  const filterBtnText = filter === 'all'
    ? t('calendar.filterAll')
    : filter === 'local'
      ? t('calendar.filterLocal')
      : getProviderLabel(filter)

  return (
    <div className="space-y-4">
      {/* 工具栏：左边统计 chips + 搜索；右边过滤下拉 + 操作按钮 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-2 flex-wrap items-center">
          {/* 本地统计（始终显示） */}
          <div className="px-3 py-1.5 rounded-xl border border-gray-200 dark:border-white/6 bg-gray-50 dark:bg-white/[0.03] text-xs">
            <span className="text-gray-500 dark:text-gray-400">{t('calendar.statLocal')}</span>
            <strong className="text-indigo-400 ml-1">{data.stats.local || 0}</strong>
          </div>
          {/* 各外部源统计（动态：data.stats 里有就显示，无则不显示） */}
          {availableProviders.map(p => {
            return (
              <div key={p} className="px-3 py-1.5 rounded-xl border border-gray-200 dark:border-white/6 bg-gray-50 dark:bg-white/[0.03] text-xs">
                <span className="text-gray-500 dark:text-gray-400">{getProviderLabel(p)}</span>
                <strong className="ml-1" style={getProviderColor(p).value}>{data.stats[p]}</strong>
              </div>
            )
          })}
          {/* 搜索框 */}
          <Popover content={<div style={{ width: 220 }}><Input placeholder={t('calendar.searchPlaceholder')} allowClear value={searchKeyword} onChange={e => setSearchKeyword(e.target.value)} autoFocus /></div>} trigger="click" placement="bottom">
            <Button size="small" icon={<SearchOutlined />} type={searchKeyword ? 'primary' : 'default'} ghost={!!searchKeyword}>
              {searchKeyword || t('calendar.search')}
            </Button>
          </Popover>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* 全选当前过滤后的外部条目 */}
          {filter !== 'local' && filteredExternalItems.length > 0 && (
            <Button size="small" type={allFilteredExternalSelected ? 'primary' : 'default'} ghost={allFilteredExternalSelected} onClick={toggleSelectAllExternal}>
              {allFilteredExternalSelected ? `☑ ${t('calendar.deselectAll')}` : `☐ ${t('calendar.selectAll')}`}
            </Button>
          )}
          {/* 过滤源：单个 Dropdown 按钮，菜单项由 stats 动态生成 → 后续新增源前端零改动 */}
          <Dropdown menu={filterMenu} trigger={['click']}>
            <Button size="small" icon={<FilterOutlined />}>
              {filterBtnText} <DownOutlined style={{ fontSize: 10 }} />
            </Button>
          </Dropdown>
          <Button size="small" icon={<SyncOutlined spin={syncing} />} onClick={onSync} disabled={syncing}>
            {t('calendar.syncSchedule')}
          </Button>
          <Button size="small" icon={<DeleteOutlined />} onClick={onClearCache} danger>
            {t('calendar.clearCache')}
          </Button>
        </div>
      </div>


      {/* 已选操作条 - 贴底悬浮工具栏（PC 端居中、移动端右下角） */}
      {selectedExtItems.length > 0 && (
        <div className={`fixed ${isMobile ? 'right-4 bottom-[calc(6rem+env(safe-area-inset-bottom))] z-[1000]' : 'left-1/2 -translate-x-1/2 bottom-3 z-40'} flex items-center gap-2 px-3 py-2 rounded-2xl shadow-xl border border-indigo-500/30 bg-white/95 dark:bg-[#1a1e2e]/95 backdrop-blur supports-[backdrop-filter]:bg-white/80 dark:supports-[backdrop-filter]:bg-[#1a1e2e]/80 max-w-[calc(100vw-1.5rem)]`}>
          <span className="text-xs font-semibold text-indigo-500 dark:text-indigo-400 whitespace-nowrap">{t('calendar.selected', { count: selectedExtItems.length })}</span>
          <button onClick={() => setSelectedExtItems([])} className="text-xs text-gray-400 hover:text-gray-600 transition whitespace-nowrap">{t('calendar.clearSelection')}</button>
          <button onClick={handleBatchSubscribe} className="px-3 py-1.5 rounded-xl text-xs font-semibold bg-indigo-500 text-white hover:bg-indigo-600 transition whitespace-nowrap shadow-sm">
            ➕ {t('calendar.batchSubscribe')}
          </button>
        </div>
      )}

      {/* Weekly grid - 统一使用 renderDayRow（PC + 移动端横滑布局）。
          renderDayRow 是普通函数（非组件）→ React 看到的是稳定的 div 结构，不会重建子树 → <img> 不会重新挂载 */}
      <div className="space-y-3">
        {orderedDays.map(day => renderDayRow(day))}
      </div>

      {/* 未知播出日：当前 filter 下 unscheduled 命中条目（全 filter 可见，B站等无星期条目落入此处）。
          样式对齐星期分组 renderDayRow：圆角边框外壳 + 头部条 + 横向滚动卡片行 */}
      {(() => {
        const unschedFiltered = filterItems(data.unscheduled || [])
        if (unschedFiltered.length === 0) return null
        return (
          <div className="group rounded-2xl border overflow-hidden border-gray-200 dark:border-white/6 bg-white dark:bg-white/[0.02]">
            <div className="px-3 py-2 border-b border-gray-100 dark:border-white/4 flex items-center gap-1.5">
              <span className="text-xs font-bold text-gray-600 dark:text-gray-400">{t('calendar.unscheduledTitle', '其他作品')}</span>
              <span className="text-[10px] text-gray-400 bg-gray-100 dark:bg-white/4 px-1.5 py-0.5 rounded-md">{unschedFiltered.length}</span>
            </div>
            <div className="relative">
              <div className="flex gap-2.5 p-2.5 overflow-x-auto scrollbar-thin">
                {unschedFiltered.map((item, idx) => (
                  renderCalCard(item, idx, { isToday: false, horizontal: true, keyPrefix: 'unsched-' })
                ))}
              </div>
            </div>
          </div>
        )
      })()}

      {/* 底部留白：避免悬浮工具栏遮挡最后一个区块（其他作品/周历）。
          放在所有内容区块之后，选中条目时才撑开，不会把「其他作品」往下挤 */}
      {selectedExtItems.length > 0 && <div className={isMobile ? 'h-36' : 'h-20'} aria-hidden />}

      {/* 订阅确认 Modal */}
      <Modal
        title={t('calendar.subscribeConfirm')}
        open={subscribeModalOpen}
        onOk={handleConfirmSubscribe}
        onCancel={() => {
          setSubscribeModalOpen(false)
          setSubscribingItem(null)
          setSelectedSubSource(null)
        }}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        width={400}
      >
        {subscribingItem && (
          <div className="space-y-3">
            <div className="text-sm">
              <div className="font-medium mb-1">{subscribingItem.animeTitle}</div>
              <div className="text-gray-500 dark:text-gray-400 text-xs space-y-0.5">
                <div>{t('calendar.type')}: {subscribingItem.animeType === 'movie' ? t('calendar.movie') : t('calendar.tvSeries')}</div>
                {subscribingItem.season && <div>{t('calendar.season')}: {subscribingItem.season}</div>}
              </div>
            </div>
            {/* 多源选择：同名番命中多个外部源时，让用户选定从哪个源订阅导入 */}
            {(subscribingItem.availableSources?.length > 1) && (
              <div className="pt-2 border-t border-gray-200 dark:border-white/10">
                <div className="text-sm font-medium mb-2">{t('calendar.chooseSource')}</div>
                <div className="space-y-1.5">
                  {subscribingItem.availableSources.map((s) => {
                    const sKey = s.provider || s.origin
                    const checked = selectedSubSource === sKey
                    return (
                      <label key={sKey} className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg cursor-pointer border transition ${checked ? 'border-indigo-400 bg-indigo-500/10' : 'border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/5'}`}>
                        <input type="radio" name="subSource" className="accent-indigo-500" checked={checked} onChange={() => setSelectedSubSource(sKey)} />
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md" style={getProviderColor(sKey).badge}>{getProviderLabel(sKey)}</span>
                        <span className="text-xs text-gray-600 dark:text-gray-300 truncate flex-1">{s.animeTitle || s.titleZh || getProviderLabel(sKey)}</span>
                        {s.rating && <span className="text-[10px] text-yellow-500 shrink-0">★{s.rating}</span>}
                      </label>
                    )
                  })}
                </div>
              </div>
            )}
            <div className="pt-2 border-t border-gray-200 dark:border-white/10">
              <Checkbox
                checked={runNowChecked}
                onChange={(e) => setRunNowChecked(e.target.checked)}
              >
                <span className="text-sm">{t('calendar.runNowLabel')}</span>
              </Checkbox>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1 ml-6">
                {t('calendar.runNowDesc')}
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* 批量订阅确认 Modal */}
      <Modal
        title={t('calendar.batchSubscribeConfirm')}
        open={batchSubscribeModalOpen}
        onOk={handleConfirmBatchSubscribe}
        onCancel={() => setBatchSubscribeModalOpen(false)}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
        width={420}
      >
        <div className="space-y-3">
          <div className="text-sm">
            {t('calendar.batchSubscribeConfirmDesc', { count: selectedExtItems.length })}
          </div>
          {selectedExtItems.length > 0 && (
            <div className="max-h-40 overflow-y-auto rounded-xl bg-gray-50 dark:bg-white/[0.03] p-2 space-y-1">
              {selectedExtItems.slice(0, 8).map((item, idx) => (
                <div key={getItemKey(item) || idx} className="flex items-center justify-between gap-2 text-xs px-2 py-1 rounded-lg bg-white dark:bg-white/[0.03]">
                  <span className="truncate font-medium text-gray-600 dark:text-gray-300">{item.animeTitle}</span>
                  <span className="shrink-0 text-[10px] text-gray-400">{getProviderLabel(item.origin)}</span>
                </div>
              ))}
              {selectedExtItems.length > 8 && (
                <div className="text-xs text-gray-400 px-2 py-1">+{selectedExtItems.length - 8}</div>
              )}
            </div>
          )}

          <div className="pt-2 border-t border-gray-200 dark:border-white/10">
            <Checkbox
              checked={runNowChecked}
              onChange={(e) => setRunNowChecked(e.target.checked)}
            >
              <span className="text-sm">{t('calendar.runNowLabel')}</span>
            </Checkbox>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-1 ml-6">
              {t('calendar.runNowDesc')}
            </div>
          </div>
        </div>
      </Modal>

      {/* 退订选源 Modal：本地卡聚合多个外部源时，让用户选退订哪个源 */}
      <Modal
        title={t('calendar.chooseUnsubSource')}
        open={unsubModalOpen}
        footer={null}
        onCancel={() => { setUnsubModalOpen(false); setUnsubItem(null) }}
        width={400}
      >
        {unsubItem && (
          <div className="space-y-2">
            <div className="text-sm font-medium mb-1">{unsubItem.animeTitle}</div>
            <div className="text-xs text-gray-400 mb-2">{t('calendar.chooseUnsubSourceDesc')}</div>
            {(unsubItem.externalSources || []).map((es) => {
              const sKey = es.provider || es.origin
              return (
                <button
                  key={sKey}
                  onClick={async () => { setUnsubModalOpen(false); setUnsubItem(null); await doUnsubscribe(unsubItem, es) }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-gray-200 dark:border-white/10 hover:bg-gray-50 dark:hover:bg-white/5 transition text-left"
                >
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md" style={getProviderColor(sKey).badge}>{getProviderLabel(sKey)}</span>
                  <span className="text-xs text-gray-600 dark:text-gray-300 truncate flex-1">{es.animeTitle || es.titleZh || getProviderLabel(sKey)}</span>
                </button>
              )
            })}
            {/* 取消本地追更（删除本地源本身） */}
            {unsubItem.sourceId && (
              <button
                onClick={async () => { setUnsubModalOpen(false); setUnsubItem(null); await doUnsubscribe(unsubItem, null) }}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-red-300 dark:border-red-500/30 text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition text-left"
              >
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-red-500/85 text-white">{t('calendar.local')}</span>
                <span className="text-xs truncate flex-1">{t('calendar.unsubLocalSource')}</span>
              </button>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}





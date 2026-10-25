import { useEffect, useMemo, useRef, useState } from 'react'
import { Input, Select, Empty, InputNumber, Tag } from 'antd'
import { SearchOutlined, HistoryOutlined } from '@ant-design/icons'
import { useAtom } from 'jotai'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MyIcon } from './MyIcon'
import { getScrapers } from '../apis'
import { searchHistoryAtom } from '../../store'
import {
  useFeatureMatches,
  featureUrl,
  danmuSearchUrl,
  navLabelOf,
} from './FeatureSearch'

// 下拉里最多展示的功能条数，避免面板过长
const MAX_FEATURES = 8

/**
 * 桌面端顶栏全局搜索栏（参考 MoviePilot 顶栏搜索：输入即出下拉，第一项搜内容，其余跳功能）。
 * - 第一项：搜索弹幕，可在右侧选择“全部源 / 指定源”，回车跳到搜索页并自动发起搜索。
 * - 其余项：功能索引命中，回车跳转到对应页面 Tab 并高亮锚点。
 * 输入框/下拉框复用 antd 组件，因此 AniBT 等页面样式的输入框外观会自动生效。
 * @param {{ focusSignal: number }} props focusSignal 变化时聚焦输入框（Ctrl/⌘+K）
 */
export const GlobalSearchBar = ({ focusSignal }) => {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [provider, setProvider] = useState('all')
  const [providers, setProviders] = useState(null)
  // 精确搜索：填写季/集后在关键词末尾追加 SxxEyy（原搜索页“精确搜索 + 插入”合并到这里）
  const [season, setSeason] = useState(null)
  const [episode, setEpisode] = useState(null)
  const [searchHistory, setSearchHistory] = useAtom(searchHistoryAtom)
  const inputRef = useRef(null)
  const wrapRef = useRef(null)

  const matches = useFeatureMatches(keyword)
  const kw = keyword.trim()
  // 有输入时第一项固定为“搜索弹幕”
  const items = useMemo(() => {
    const feats = matches.slice(0, MAX_FEATURES)
    return kw ? [{ id: '__danmu__', _danmu: true }, ...feats] : feats
  }, [kw, matches])

  useEffect(() => {
    setActiveIndex(0)
  }, [keyword])

  // Ctrl/⌘+K：聚焦搜索栏（首次挂载不触发）
  useEffect(() => {
    if (!focusSignal) return
    inputRef.current?.focus()
    setOpen(true)
  }, [focusSignal])

  // 点击搜索栏外部时收起下拉
  useEffect(() => {
    if (!open) return
    const onDown = e => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // 首次展开时再拉取启用的搜索源（按显示顺序），失败时只保留“全部源”
  useEffect(() => {
    if (!open || providers) return
    getScrapers()
      .then(res => {
        const list = (res?.data || [])
          .filter(s => s.isEnabled)
          .sort((a, b) => a.displayOrder - b.displayOrder)
          .map(s => s.providerName)
        setProviders(list)
      })
      .catch(() => setProviders([]))
  }, [open, providers])

  const providerOptions = useMemo(
    () => [
      { value: 'all', label: t('featureSearch.allSources') },
      ...(providers || []).map(name => ({ value: name, label: name })),
    ],
    [providers, t]
  )

  // 实际提交的弹幕搜索关键词：季必填才追加，集可选
  const exactKeyword = useMemo(() => {
    if (!kw || !season) return kw
    let suffix = ` S${String(season).padStart(2, '0')}`
    if (episode) suffix += `E${String(episode).padStart(2, '0')}`
    return `${kw}${suffix}`
  }, [kw, season, episode])

  const close = () => {
    setOpen(false)
    setKeyword('')
    setSeason(null)
    setEpisode(null)
    inputRef.current?.blur()
  }

  // 直接按关键词搜索弹幕（历史记录点击也走这里）
  const searchDanmu = (text, src = provider) => {
    if (!text?.trim()) return
    navigate(danmuSearchUrl(text, src))
    close()
  }

  const go = item => {
    if (!item) return
    if (item._danmu) {
      searchDanmu(exactKeyword)
      return
    }
    navigate(featureUrl(item))
    close()
  }

  // 有输入时历史记录按包含关系过滤，没输入时全部展示
  const historyItems = useMemo(
    () => (kw ? searchHistory.filter(h => h !== kw && h.includes(kw)) : searchHistory).slice(0, 10),
    [kw, searchHistory]
  )

  const onKeyDown = e => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIndex(i => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      go(items[activeIndex])
    } else if (e.key === 'Escape') {
      setOpen(false)
      inputRef.current?.blur()
    }
  }

  return (
    <div ref={wrapRef} className="relative w-[360px] max-w-[40vw]">
      <Input
        ref={inputRef}
        prefix={<SearchOutlined className="text-gray-400" />}
        suffix={<span className="text-xs text-gray-400">Ctrl/⌘+K</span>}
        placeholder={t('featureSearch.barPlaceholder')}
        value={keyword}
        onChange={e => {
          setKeyword(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        allowClear
      />
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg shadow-lg bg-base-bg border border-base-hover max-h-[440px] overflow-y-auto py-1">
          {/* 搜索历史：参考 MP，点击搜索框即可看到，点一下直接重新搜索 */}
          {historyItems.length > 0 && (
            <div className="px-4 pt-1 pb-2">
              <div className="flex items-center gap-1 pb-1 text-xs text-gray-400">
                <HistoryOutlined />
                {t('featureSearch.historyTitle')}
                <span
                  className="ml-auto cursor-pointer hover:text-primary"
                  onClick={() => setSearchHistory([])}
                >
                  {t('featureSearch.clearHistory')}
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {historyItems.map(h => (
                  <Tag
                    key={h}
                    closable
                    className="cursor-pointer m-0"
                    onClick={() => searchDanmu(h)}
                    onClose={e => {
                      e.preventDefault()
                      e.stopPropagation()
                      setSearchHistory(list => list.filter(o => o !== h))
                    }}
                  >
                    {h}
                  </Tag>
                ))}
              </div>
            </div>
          )}
          {items.length === 0 ? (
            historyItems.length > 0 ? null : (
            <Empty
              className="py-6"
              description={t('featureSearch.empty')}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
            )
          ) : (
            <>
              {items.map((item, idx) => (
                <div key={item.id}>
                {/* 分组标题：内容搜索 / 功能搜索 */}
                {item._danmu && (
                  <div className="px-4 pt-1 pb-1 text-xs text-gray-400">{t('featureSearch.contentTitle')}</div>
                )}
                {!item._danmu && idx === (kw ? 1 : 0) && (
                  <div className="px-4 pt-1 pb-1 text-xs text-gray-400">{t('featureSearch.menuTitle')}</div>
                )}
                {item._danmu ? (
                  <div
                    key={item.id}
                    onClick={() => go(item)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`flex items-center gap-3 px-4 py-2 cursor-pointer transition-colors ${
                      idx === activeIndex ? 'bg-base-hover' : ''
                    }`}
                  >
                    <MyIcon icon="danmu" size={20} className="text-primary flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-base-text truncate">
                        {t('featureSearch.searchDanmu', { keyword: exactKeyword })}
                      </div>
                      {/* 精确搜索：季/集输入，阻止冒泡避免点输入框就跳转 */}
                      <div
                        className="flex items-center gap-1 mt-1 text-xs text-gray-500"
                        onClick={e => e.stopPropagation()}
                        onKeyDown={e => {
                          // 在季/集框里回车也直接提交搜索
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            searchDanmu(exactKeyword)
                          }
                        }}
                      >
                        <span>{t('featureSearch.exact')}</span>
                        <InputNumber
                          size="small"
                          min={0}
                          value={season}
                          onChange={setSeason}
                          placeholder={t('home.season')}
                          style={{ width: 60 }}
                        />
                        <InputNumber
                          size="small"
                          min={1}
                          value={episode}
                          onChange={setEpisode}
                          disabled={!season}
                          placeholder={t('home.episode')}
                          style={{ width: 60 }}
                        />
                      </div>
                    </div>
                    {/* 阻止冒泡：选源时不触发跳转 */}
                    <div onClick={e => e.stopPropagation()}>
                      <Select
                        size="small"
                        value={provider}
                        onChange={setProvider}
                        options={providerOptions}
                        loading={providers === null}
                        popupMatchSelectWidth={false}
                        getPopupContainer={() => wrapRef.current}
                        style={{ minWidth: 110 }}
                      />
                    </div>
                  </div>
                ) : (
                  <div
                    key={item.id}
                    onClick={() => go(item)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`flex items-center gap-3 px-4 py-2 cursor-pointer transition-colors ${
                      idx === activeIndex ? 'bg-base-hover' : ''
                    }`}
                  >
                    {item.icon && (
                      <MyIcon icon={item.icon} size={20} className="text-primary flex-shrink-0" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-base-text truncate">
                        {item._title}
                        <span className="text-xs text-gray-400 ml-2">{t(navLabelOf(item))}</span>
                      </div>
                      {item._desc && (
                        <div className="text-xs text-gray-500 truncate">{item._desc}</div>
                      )}
                    </div>
                  </div>
                )}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default GlobalSearchBar

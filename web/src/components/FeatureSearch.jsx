import { useState, useMemo, useEffect, useRef } from 'react'
import { Modal, Input, Empty } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MyIcon } from './MyIcon'
import { FEATURES } from '../configs/features'

/**
 * 功能索引模糊匹配（功能名 + 简介 + 别名，三语由 t() 解析），顶栏搜索栏与移动端面板共用。
 * @param {string} keyword 用户输入
 * @returns {Array} 命中的功能项（附 _title/_desc）
 */
export const useFeatureMatches = keyword => {
  const { t } = useTranslation()
  const indexed = useMemo(
    () =>
      FEATURES.map(f => {
        const title = t(f.titleKey)
        const desc = f.descKey ? t(f.descKey) : ''
        const haystack = [title, desc, ...(f.keywords || [])]
          .join(' ')
          .toLowerCase()
        return { ...f, _title: title, _desc: desc, _haystack: haystack }
      }),
    [t]
  )
  return useMemo(() => {
    const kw = (keyword || '').trim().toLowerCase()
    if (!kw) return indexed
    // 支持空格分词，所有词都命中才算匹配
    const terms = kw.split(/\s+/).filter(Boolean)
    return indexed.filter(f => terms.every(term => f._haystack.includes(term)))
  }, [keyword, indexed])
}

/** 功能项 → 跳转 URL（?key=Tab#锚点） */
export const featureUrl = item => {
  let url = item.path
  if (item.tabKey) url += `?key=${item.tabKey}`
  if (item.anchor) url += `#${item.anchor}`
  return url
}

/** 弹幕搜索跳转 URL：进入搜索页并自动发起搜索，可指定单个源或源数组（多个源逗号拼接） */
export const danmuSearchUrl = (keyword, provider) => {
  const params = new URLSearchParams({ keyword: keyword.trim(), auto: '1' })
  const list = (Array.isArray(provider) ? provider : [provider]).filter(p => p && p !== 'all')
  if (list.length) params.set('provider', list.join(','))
  return `/search?${params.toString()}`
}

/**
 * 全功能搜索面板（移动端弹窗）：输入关键词模糊匹配功能（功能名+简介+别名），点击跳转并高亮定位。
 */
export const FeatureSearch = ({ open, onClose }) => {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef(null)

  // 打开时聚焦并重置
  useEffect(() => {
    if (open) {
      setKeyword('')
      setActiveIndex(0)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [open])

  const results = useFeatureMatches(keyword)

  useEffect(() => {
    setActiveIndex(0)
  }, [keyword])

  // 有输入时第一项是“搜索弹幕”，其余为功能命中
  const kw = keyword.trim()
  const items = useMemo(
    () => (kw ? [{ id: '__danmu__', _danmu: true }, ...results] : results),
    [kw, results]
  )

  const go = (item) => {
    if (!item) return
    navigate(item._danmu ? danmuSearchUrl(kw) : featureUrl(item))
    onClose?.()
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex(i => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      go(items[activeIndex])
    }
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      closable={false}
      width={560}
      styles={{ body: { padding: 0 } }}
      destroyOnClose
    >
      <div className="p-3 border-b border-base-hover">
        <Input
          ref={inputRef}
          size="large"
          variant="borderless"
          prefix={<SearchOutlined className="text-gray-400" />}
          placeholder={t('featureSearch.placeholder')}
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onKeyDown={onKeyDown}
          allowClear
        />
      </div>
      <div className="max-h-[420px] overflow-y-auto py-2">
        {items.length === 0 ? (
          <Empty
            className="py-8"
            description={t('featureSearch.empty')}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          items.map((item, idx) => item._danmu ? (
            <div
              key={item.id}
              onClick={() => go(item)}
              onMouseEnter={() => setActiveIndex(idx)}
              className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors ${
                idx === activeIndex ? 'bg-base-hover' : ''
              }`}
            >
              <MyIcon icon="danmu" size={20} className="text-primary flex-shrink-0" />
              <div className="text-sm font-medium text-base-text truncate">
                {t('featureSearch.searchDanmu', { keyword: kw })}
              </div>
            </div>
          ) : (
            <div
              key={item.id}
              onClick={() => go(item)}
              onMouseEnter={() => setActiveIndex(idx)}
              className={`flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors ${
                idx === activeIndex ? 'bg-base-hover' : ''
              }`}
            >
              {item.icon && (
                <MyIcon icon={item.icon} size={20} className="text-primary flex-shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-base-text truncate">
                  {item._title}
                  <span className="text-xs text-gray-400 ml-2">
                    {t(navLabelOf(item))}
                  </span>
                </div>
                {item._desc && (
                  <div className="text-xs text-gray-500 truncate">{item._desc}</div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </Modal>
  )
}

// 根据 path 推导所属一级页面的导航名（用于结果右侧的面包屑提示）
const PATH_NAV_LABEL = {
  '/': 'nav.home',
  '/search': 'nav.search',
  '/library': 'nav.library',
  '/library/batch-manage': 'nav.library',
  '/library/subscriptions': 'nav.library',
  '/task': 'nav.task',
  '/bullet': 'nav.bullet',
  '/media-fetch': 'nav.mediaFetch',
  '/source': 'nav.source',
  '/control': 'nav.control',
  '/setting': 'nav.setting',
}
export const navLabelOf = (item) => PATH_NAV_LABEL[item.path] || ''

export default FeatureSearch

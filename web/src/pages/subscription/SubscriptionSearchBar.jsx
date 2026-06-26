import { useEffect, useState, useCallback, useMemo } from 'react'
import { Select, Input, Button, message, Popover, List, Avatar, Empty, Spin, Tag, Divider } from 'antd'
import { SearchOutlined, LinkOutlined } from '@ant-design/icons'
import {
  getAvailableSubscriptionSources,
  discoverSubscriptionTargets,
  createSubscriptionTarget,
} from '../../apis'
import { SubscriptionUrlModal } from './SubscriptionUrlModal'

// 是否为 URL：仅看协议头。具体能否被订阅源识别，交后端 /resolve-url 决定。
// 这样前端无需感知任何源的域名（新增/移除源时前端零改动）。
const looksLikeUrl = (q) => !!q && /^https?:\/\//i.test(q.trim())

// 统一搜索区：常驻订阅页顶部，两视图通用。
// 支持：选订阅源（全部/单个）+ 关键词或 URL → discover 候选 → 点选订阅。
// 设计依据：订阅页双视图方案（追番日历 / 探索发现）。
export const SubscriptionSearchBar = ({ t, onSubscribed }) => {
  const [sources, setSources] = useState([])         // 可用订阅源（available=true）
  const [provider, setProvider] = useState('all')    // 选中的源；all=全部
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [candidates, setCandidates] = useState([])
  const [errors, setErrors] = useState([])
  const [popoverOpen, setPopoverOpen] = useState(false)
  const [submittingKey, setSubmittingKey] = useState(null)
  const [urlModalOpen, setUrlModalOpen] = useState(false)  // URL 解析独立弹框开关

  const loadSources = useCallback(async () => {
    try {
      const res = await getAvailableSubscriptionSources()
      const d = res.data || res
      const all = [...(d.danmakuSources || []), ...(d.calendarSources || [])]
      setSources(all)
    } catch { /* 静默 */ }
  }, [])

  useEffect(() => { loadSources() }, [loadSources])

  const availableSources = useMemo(() => sources.filter(s => s.available), [sources])

  // 把任意类型的错误信息规范化为字符串（FastAPI 422 时 detail 是数组）
  const _stringifyReason = (raw) => {
    if (raw == null) return '未知错误'
    if (typeof raw === 'string') return raw
    if (Array.isArray(raw)) {
      // FastAPI 422 detail: [{type, loc, msg, input, url}, ...]
      return raw.map(it => {
        if (typeof it === 'string') return it
        if (it && typeof it === 'object') {
          const loc = Array.isArray(it.loc) ? it.loc.join('.') : (it.loc || '')
          return `${loc ? loc + ': ' : ''}${it.msg || JSON.stringify(it)}`
        }
        return String(it)
      }).join('; ')
    }
    if (typeof raw === 'object') {
      // 单个错误对象
      if (raw.msg) return raw.msg
      try { return JSON.stringify(raw) } catch { return String(raw) }
    }
    return String(raw)
  }

  const _discoverOne = async (p, q) => {
    try {
      const r = await discoverSubscriptionTargets({ provider: p, query: q })
      const list = ((r.data || r).list || []).map(it => ({ ...it, _provider: p }))
      return { provider: p, list, error: null }
    } catch (e) {
      // detail 可能是字符串 / 422 的对象数组 / 其它，需统一规范化
      const reason = _stringifyReason(e?.response?.data?.detail) || e?.message || '未知错误'
      return { provider: p, list: [], error: reason }
    }
  }

  const _runDiscover = async (targets, q) => {
    setSearching(true)
    setPopoverOpen(true)
    setCandidates([])
    setErrors([])
    try {
      const results = await Promise.all(targets.map(p => _discoverOne(p, q)))
      const merged = results.flatMap(r => r.list)
      const errs = results.filter(r => r.error).map(r => ({ provider: r.provider, reason: r.error }))
      setCandidates(merged)
      setErrors(errs)
      if (merged.length === 0 && errs.length === 0) {
        message.info(t('subscription.noCandidates', '未找到候选项'))
      } else if (merged.length === 0 && errs.length > 0) {
        message.warning(t('subscription.allSourcesFailed', '所有源搜索失败，请查看结果面板'))
      }
    } finally {
      setSearching(false)
    }
  }

  const handleSearch = async () => {
    const q = (query || '').trim()
    if (!q) { message.info(t('subscription.enterQuery', '请输入关键词或 URL')); return }

    // URL：统一打开独立的 URL 解析弹框（结构化展示 + 多选批量订阅）
    if (looksLikeUrl(q)) {
      setUrlModalOpen(true)
      return
    }

    let targets = []
    if (provider === 'all') {
      targets = availableSources.map(s => s.provider)
    } else {
      const chosen = sources.find(s => s.provider === provider)
      if (chosen && !chosen.available) {
        message.warning(t('subscription.sourceNotReady', `${chosen.displayName} 待配置，无法搜索`))
        return
      }
      targets = [provider]
    }
    if (targets.length === 0) {
      message.info(t('subscription.noAvailableSource', '暂无可用订阅源，请先在源管理里配置/授权'))
      return
    }
    await _runDiscover(targets, q)
  }

  // URL 解析按钮：打开独立弹框（不再内嵌 discover）
  const handleUrlImport = () => {
    setUrlModalOpen(true)
  }

  const handlePick = async (item, idx) => {
    setSubmittingKey(idx)
    try {
      await createSubscriptionTarget({ provider: item._provider, type: item.type, payload: item.payload || {} })
      message.success(t('subscription.createSuccess', '订阅目标已创建'))
      onSubscribed?.()
      setPopoverOpen(false)
    } catch (e) {
      const reason = _stringifyReason(e?.response?.data?.detail) || e?.message || t('subscription.createFailed', '创建订阅失败')
      message.error(reason)
    } finally { setSubmittingKey(null) }
  }

  const groupedCandidates = useMemo(() => {
    const map = new Map()
    candidates.forEach((it, flatIdx) => {
      const p = it._provider || 'unknown'
      const arr = map.get(p) || []
      arr.push({ item: it, flatIdx })
      map.set(p, arr)
    })
    return Array.from(map.entries()).map(([p, items]) => {
      const meta = sources.find(s => s.provider === p)
      return { provider: p, displayName: meta?.displayName || p, items }
    })
  }, [candidates, sources])

  const resultPanel = (
    <div style={{ width: 460, maxHeight: 460, overflowY: 'auto' }}>
      <Spin spinning={searching}>
        {candidates.length === 0 && errors.length === 0 ? (
          <Empty description={searching ? t('subscription.searching', '搜索中...') : t('subscription.searchHint', '输入关键词或 URL 后搜索')} />
        ) : (
          <>
            {groupedCandidates.map(({ provider: p, displayName, items }, gi) => (
              <div key={p}>
                {gi > 0 && <Divider style={{ margin: '8px 0' }} />}
                <div className="px-2 py-1 flex items-center gap-2">
                  <Tag color="blue">{displayName}</Tag>
                  <span className="text-xs text-gray-500">{items.length} {t('subscription.candidatesCount', '条候选')}</span>
                </div>
                <List
                  size="small"
                  dataSource={items}
                  renderItem={(entry) => {
                    const { item, flatIdx } = entry
                    return (
                      <List.Item
                        key={`${p}-${flatIdx}`}
                        actions={[
                          <Button key="s" type="primary" size="small" loading={submittingKey === flatIdx} onClick={() => handlePick(item, flatIdx)}>
                            {t('subscription.subscribeThis', '订阅')}
                          </Button>,
                        ]}
                      >
                        <List.Item.Meta
                          avatar={item.cover ? <Avatar shape="square" size={40} src={item.cover} /> : <Avatar shape="square" size={40}>{((item.title || '?') + '').slice(0, 1)}</Avatar>}
                          title={<span className="text-sm font-medium">{item.title || '(无标题)'}</span>}
                          description={<span className="text-xs text-gray-500">{item.description || ''}</span>}
                        />
                      </List.Item>
                    )
                  }}
                />
              </div>
            ))}
            {errors.length > 0 && (
              <>
                <Divider style={{ margin: '8px 0' }} />
                <div className="px-2 py-1 text-xs text-amber-500">
                  {t('subscription.partialFailed', '部分源未返回结果：')}
                </div>
                <div className="px-2 pb-2 space-y-1">
                  {errors.map((er, i) => (
                    <div key={`${er.provider}-${i}`} className="text-xs text-gray-500">
                      <Tag color="warning">{sources.find(s => s.provider === er.provider)?.displayName || er.provider}</Tag>
                      <span className="ml-1">{er.reason}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </Spin>
    </div>
  )

  const selectOptions = [
    { label: t('subscription.allSources', '全部源'), value: 'all' },
    ...sources.map(s => ({
      label: s.available
        ? s.displayName
        : `⚠️ ${s.displayName} (${t('subscription.notReady', '待配置')})`,
      value: s.provider,
    })),
  ]

  return (
    <div className="flex flex-wrap gap-2 items-center">
      <Select
        value={provider} onChange={setProvider} style={{ width: 160 }}
        options={selectOptions}
      />
      <Popover open={popoverOpen} onOpenChange={setPopoverOpen} trigger="click" placement="bottomLeft" content={resultPanel}>
        <Input
          value={query} onChange={(e) => setQuery(e.target.value)} onPressEnter={handleSearch}
          placeholder={t('subscription.searchOrUrl', '关键词搜索，或粘贴视频/合集 URL')}
          style={{ width: 340 }} prefix={<SearchOutlined />}
        />
      </Popover>
      <Button type="primary" loading={searching} onClick={handleSearch} icon={<SearchOutlined />}>
        {t('subscription.search', '搜索')}
      </Button>
      <Button onClick={handleUrlImport} icon={<LinkOutlined />}>
        {t('subscription.parseUrl', 'URL 解析')}
      </Button>
      <SubscriptionUrlModal
        open={urlModalOpen}
        onClose={() => setUrlModalOpen(false)}
        initialUrl={looksLikeUrl(query) ? query.trim() : ''}
        t={t}
        onSubscribed={onSubscribed}
      />
    </div>
  )
}

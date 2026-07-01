import { useState, useCallback } from 'react'
import { Modal, Button, message, Spin, Empty, Tag, Divider, Card, Input } from 'antd'
import { resolveSubscriptionUrl, createSubscriptionTarget } from '../../apis'

// 把后端返回的错误规范化为字符串（FastAPI 422 时 detail 是对象数组）
const stringifyReason = (raw) => {
  if (raw == null) return '未知错误'
  if (typeof raw === 'string') return raw
  if (Array.isArray(raw)) {
    return raw.map(it => {
      if (typeof it === 'string') return it
      if (it && typeof it === 'object') {
        const loc = Array.isArray(it.loc) ? it.loc.join('.') : (it.loc || '')
        return `${loc ? loc + ': ' : ''}${it.msg || JSON.stringify(it)}`
      }
      return String(it)
    }).join('; ')
  }
  if (typeof raw === 'object') return raw.msg || (() => { try { return JSON.stringify(raw) } catch { return String(raw) } })()
  return String(raw)
}

// 数值格式化：1.2万 / 3456
const fmtNum = (n) => {
  const v = Number(n || 0)
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return String(v)
}
// 时长（秒）→ mm:ss / hh:mm:ss
const fmtDuration = (sec) => {
  const s = Number(sec || 0)
  if (!s) return ''
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60
  const pad = (x) => String(x).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(ss)}` : `${m}:${pad(ss)}`
}
// 时间戳 → YYYY-MM-DD
const fmtDate = (ts) => {
  const t = Number(ts || 0)
  if (!t) return ''
  const d = new Date(t * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 视频卡片：主标题=视频名（过长自动换行），副标题=BV/AV号 + 作者，下方展示可获取的参数
const VideoCard = ({ video, t }) => {
  const stat = video.stat || {}
  // 优先展示 BV 号，无则回退 AV 号（aid）
  const vidCode = video.bvid || (video.aid ? `av${video.aid}` : '')
  return (
    <div className="flex gap-3">
      {video.cover && <img src={video.cover} alt="" style={{ width: 120, height: 75, objectFit: 'cover', borderRadius: 4, flexShrink: 0 }} />}
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium break-words" title={video.title}>{video.title || t('subscription.noTitle', '(无标题)')}</div>
        <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap items-center gap-x-2">
          {vidCode && <span className="text-gray-400 font-mono">{vidCode}</span>}
          {video.author && <span>{t('subscription.author', '作者')}：{video.author}</span>}
        </div>
        <div className="text-xs text-gray-400 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
          {stat.view != null && <span>▶ {fmtNum(stat.view)}</span>}
          {stat.danmaku != null && <span>弹 {fmtNum(stat.danmaku)}</span>}
          {stat.like != null && <span>赞 {fmtNum(stat.like)}</span>}
          {stat.coin != null && <span>币 {fmtNum(stat.coin)}</span>}
          {stat.favorite != null && <span>藏 {fmtNum(stat.favorite)}</span>}
          {stat.reply != null && <span>评 {fmtNum(stat.reply)}</span>}
          {video.duration ? <span>⏱ {fmtDuration(video.duration)}</span> : null}
          {video.pubdate ? <span>{fmtDate(video.pubdate)}</span> : null}
        </div>
      </div>
    </div>
  )
}

// 独立的 URL 解析弹框：粘贴 URL → 解析 → 展示「当前视频/所属合集/合集全部视频(多选)」→ 批量订阅
export const SubscriptionUrlModal = ({ open, onClose, initialUrl = '', t, onSubscribed }) => {
  const [url, setUrl] = useState(initialUrl)
  const [loading, setLoading] = useState(false)
  const [matched, setMatched] = useState(null)   // {currentVideo, collection, collectionVideos}
  const [provider, setProvider] = useState('')
  const [selectedBvids, setSelectedBvids] = useState([]) // 合集内勾选的视频 bvid
  const [submitting, setSubmitting] = useState(false)

  const doResolve = useCallback(async (u) => {
    const target = (u ?? url ?? '').trim()
    if (!target) { message.info(t('subscription.enterUrl', '请输入 URL')); return }
    setLoading(true); setMatched(null); setSelectedBvids([])
    try {
      const r = await resolveSubscriptionUrl(target)
      const d = r.data || r
      setProvider(d.provider || '')
      if (d.matched) {
        setMatched(d.matched)
        // 默认全选合集内视频，方便一键批量订阅
        setSelectedBvids((d.matched.collectionVideos || []).map(v => v.bvid).filter(Boolean))
      } else {
        // 降级：后端未结构化解析，仅给了 list（兜底提示）
        setMatched({ currentVideo: null, collection: null, collectionVideos: [], _fallbackList: d.list || [] })
      }
    } catch (e) {
      message.error(stringifyReason(e?.response?.data?.detail) || e?.message || t('subscription.urlParseFailed', 'URL 解析失败'))
    } finally { setLoading(false) }
  }, [url, t])

  // 订阅一个目标（type + payload 由后端结构化返回直接透传，前端零硬编码）
  const subscribeOne = async (type, payload) => {
    await createSubscriptionTarget({ provider, type, payload: payload || {} })
  }

  const handleSubscribeCurrent = async () => {
    const cv = matched?.currentVideo
    if (!cv) return
    setSubmitting(true)
    try {
      await subscribeOne(cv.subscribeType, cv.subscribePayload)
      message.success(t('subscription.createSuccess', '订阅成功'))
      onSubscribed?.()
    } catch (e) {
      message.error(stringifyReason(e?.response?.data?.detail) || e?.message || t('subscription.createFailed', '订阅失败'))
    } finally { setSubmitting(false) }
  }

  const handleSubscribeCollection = async () => {
    const col = matched?.collection
    if (!col) return
    setSubmitting(true)
    try {
      await subscribeOne(col.subscribeType, col.subscribePayload)
      message.success(t('subscription.createSuccess', '订阅成功'))
      onSubscribed?.()
    } catch (e) {
      message.error(stringifyReason(e?.response?.data?.detail) || e?.message || t('subscription.createFailed', '订阅失败'))
    } finally { setSubmitting(false) }
  }

  const handleSubscribeSelected = async () => {
    const videos = (matched?.collectionVideos || []).filter(v => selectedBvids.includes(v.bvid))
    if (videos.length === 0) { message.info(t('subscription.selectAtLeastOne', '请至少勾选一个视频')); return }

    const col = matched?.collection
    if (!col) {
      message.error(t('subscription.noCollection', '未找到合集信息'))
      return
    }

    setSubmitting(true)
    try {
      // 修复：订阅合集时传入 selectedEpisodes，而非循环订阅单集
      const selectedEpisodes = videos.map(v => {
        // 从 subscribePayload 中提取 externalId（视频ID）
        const videoId = v.subscribePayload?.externalId || v.bvid
        return `video:${videoId}`  // 格式：video:BV1xxx 或 video:avid
      })

      // 合并合集的 subscribePayload + selectedEpisodes
      const payload = {
        ...(col.subscribePayload || {}),
        selectedEpisodes,  // 新增字段：选中的集ID列表
      }

      await subscribeOne(col.subscribeType, payload)
      message.success(t('subscription.createSuccess', `已订阅合集的 ${videos.length} 个视频`))
      onSubscribed?.()
    } catch (e) {
      message.error(stringifyReason(e?.response?.data?.detail) || e?.message || t('subscription.createFailed', '订阅失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <SubscriptionUrlModalView
      open={open} onClose={onClose} t={t}
      url={url} setUrl={setUrl} loading={loading} doResolve={doResolve}
      matched={matched} provider={provider} submitting={submitting}
      selectedBvids={selectedBvids} setSelectedBvids={setSelectedBvids}
      handleSubscribeCurrent={handleSubscribeCurrent}
      handleSubscribeCollection={handleSubscribeCollection}
      handleSubscribeSelected={handleSubscribeSelected}
      VideoCard={VideoCard}
    />
  )
}

// 视图层：纯展示，逻辑全在容器组件
const SubscriptionUrlModalView = ({
  open, onClose, t, url, setUrl, loading, doResolve, matched, provider, submitting,
  selectedBvids, setSelectedBvids, handleSubscribeCurrent, handleSubscribeCollection,
  handleSubscribeSelected, VideoCard,
}) => {
  const videos = matched?.collectionVideos || []
  const allChecked = videos.length > 0 && selectedBvids.length === videos.length
  const indeterminate = selectedBvids.length > 0 && selectedBvids.length < videos.length
  // 点击卡片切换单个视频的选中状态（与主页一致的交互）
  const toggleVideo = (bvid) => {
    if (!bvid) return
    setSelectedBvids(prev => prev.includes(bvid) ? prev.filter(b => b !== bvid) : [...prev, bvid])
  }

  return (
    <Modal
      open={open} onCancel={onClose} title={t('subscription.urlParseTitle', 'URL 解析订阅')}
      width={720} footer={null} destroyOnClose
    >
      <div className="flex gap-2 mb-3">
        <Input
          value={url} onChange={(e) => setUrl(e.target.value)} onPressEnter={() => doResolve()}
          placeholder={t('subscription.pasteUrl', '粘贴视频 / 合集 URL')} allowClear
        />
        <Button type="primary" loading={loading} onClick={() => doResolve()}>
          {t('subscription.parse', '解析')}
        </Button>
      </div>

      <Spin spinning={loading}>
        {!matched ? (
          <Empty description={t('subscription.urlParseHint', '粘贴 URL 后点解析')} />
        ) : (
          <div style={{ maxHeight: 480, overflowY: 'auto' }}>
            {provider && <Tag color="blue" className="mb-2">{provider}</Tag>}

            {/* 当前视频 */}
            {matched.currentVideo && (
              <Card size="small" className="mb-3" title={t('subscription.currentVideo', '当前视频')}
                extra={<Button type="primary" size="small" loading={submitting} onClick={handleSubscribeCurrent}>
                  {t('subscription.subscribeThis', '订阅此视频')}
                </Button>}>
                <VideoCard video={matched.currentVideo} t={t} />
              </Card>
            )}

            {/* 所属合集 */}
            {matched.collection && (
              <Card size="small" className="mb-3"
                title={`${t('subscription.collection', '所属合集')}：${matched.collection.title || ''}`}
                extra={<Button size="small" loading={submitting} onClick={handleSubscribeCollection}>
                  {t('subscription.subscribeCollection', '订阅整个合集')}
                </Button>}>
                <div className="text-xs text-gray-500">
                  {t('subscription.collectionTotal', '合集共')} {matched.collection.total || videos.length} {t('subscription.videosUnit', '个视频')}
                </div>
              </Card>
            )}

            {/* 合集内全部视频（点击卡片切换选中，与主页一致，无复选框） */}
            {videos.length > 0 && (
              <Card size="small"
                title={
                  <span className="cursor-pointer select-none" onClick={() => setSelectedBvids(allChecked ? [] : videos.map(v => v.bvid).filter(Boolean))}>
                    {allChecked ? '☑' : (indeterminate ? '☐' : '☐')} {t('subscription.selectVideos', '选择视频')}（{selectedBvids.length}/{videos.length}）
                  </span>
                }
                extra={<Button type="primary" size="small" loading={submitting} onClick={handleSubscribeSelected}>
                  {t('subscription.subscribeSelected', '订阅选中视频')}
                </Button>}>
                <div className="flex flex-col gap-2">
                  {videos.map((v, i) => {
                    const isSel = selectedBvids.includes(v.bvid)
                    return (
                      <div key={v.bvid || i}
                        onClick={() => toggleVideo(v.bvid)}
                        className={`relative rounded-xl border p-2 transition cursor-pointer ${isSel ? 'ring-2 ring-inset' : 'border-gray-200 dark:border-white/10 hover:border-indigo-400/50'}`}
                        style={isSel ? {
                          backgroundColor: 'color-mix(in srgb, var(--color-primary) 15%, transparent)',
                          borderColor: 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
                          boxShadow: '0 0 0 1px color-mix(in srgb, var(--color-primary) 30%, transparent)',
                          '--tw-ring-color': 'color-mix(in srgb, var(--color-primary) 50%, transparent)',
                        } : undefined}>
                        {isSel && (
                          <div className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center shadow-sm z-10"
                            style={{ backgroundColor: 'var(--color-primary)' }}>
                            <span className="text-white text-[10px] font-bold">✓</span>
                          </div>
                        )}
                        <VideoCard video={v} t={t} />
                      </div>
                    )
                  })}
                </div>
              </Card>
            )}

            {matched._fallbackList && matched._fallbackList.length > 0 && (
              <>
                <Divider style={{ margin: '8px 0' }} />
                <div className="text-xs text-gray-500">{t('subscription.fallbackHint', '该源未提供结构化解析，仅返回候选列表')}</div>
              </>
            )}
          </div>
        )}
      </Spin>
    </Modal>
  )
}

import {
  getMatchTest,
  searchEpisodesTest,
  searchAnimeTest,
  getBangumiDetailTest,
  getCommentTest,
  pollTaskCommentTest,
  parseFilenameTest,
  getTokenList,
} from '../../../apis'
import { useState, useEffect, useMemo } from 'react'
import {
  Button,
  Card,
  Form,
  Input,
  Tabs,
  InputNumber,
  Select,
  Tag,
  Alert,
  Pagination,
  Switch,
} from 'antd'
import { SearchOutlined } from '@ant-design/icons'

export const Test = () => {
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()
  const [result, setResult] = useState(null)
  const [activeTab, setActiveTab] = useState('match')
  const [tokens, setTokens] = useState([])
  const [tokensLoading, setTokensLoading] = useState(false)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [showRaw, setShowRaw] = useState(false)

  // 加载 token 列表
  useEffect(() => {
    fetchTokens()
  }, [])

  const STORAGE_KEY = 'test_selected_token'

  const fetchTokens = async () => {
    try {
      setTokensLoading(true)
      const res = await getTokenList()
      // 只显示已启用且未过期的 token
      const now = new Date()
      const validTokens = (res?.data || []).filter(token => {
        if (!token.isEnabled) return false
        if (token.expiresAt && new Date(token.expiresAt) < now) return false
        return true
      })
      setTokens(validTokens)
    } catch (error) {
      console.error('获取 Token 列表失败:', error)
    } finally {
      setTokensLoading(false)
    }
  }

  // tokens 加载完成后自动选中：优先恢复缓存，否则选第一个
  useEffect(() => {
    if (tokens.length === 0) return
    const cached = localStorage.getItem(STORAGE_KEY)
    const current = form.getFieldValue('apiToken')
    // 已有值（且仍在有效列表中）就不覆盖
    if (current && tokens.find(t => t.token === current)) return
    const target = (cached && tokens.find(t => t.token === cached))
      ? cached
      : tokens[0].token
    form.setFieldValue('apiToken', target)
  }, [tokens])

  // 测试配置：每个测试类型的配置
  const testConfigs = {
    match: {
      label: '文件名匹配',
      apiPath: '/api/v1/{token}/match',
      method: 'POST',
      handler: getMatchTest,
      fields: [
        {
          name: 'fileName',
          label: '文件名',
          apiParam: 'fileName',
          placeholder: '请输入要测试匹配的文件名',
          required: true,
          component: Input,
        },
      ],
      getListData: data => data?.matches || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.animeTitle || '').toLowerCase().includes(kw) ||
          (item.episodeTitle || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        const hasMatches = data?.matches && data.matches.length > 0
        if (!hasMatches) return <div className="text-red-600">[匹配失败] 未匹配到任何结果</div>
        const statusColor = data.isMatched ? 'text-green-600' : 'text-orange-600'
        const statusText = data.isMatched
          ? '[精确匹配]'
          : `[多个匹配] 找到 ${data.matches.length} 个可能的匹配`
        return <div className={`font-bold ${statusColor}`}>{statusText}</div>
      },
      renderItem: (it, index, data) => (
        <div
          key={index}
          className={`p-3 rounded border ${
            data?.isMatched
              ? 'bg-green-50 dark:bg-green-950 border-green-200 dark:border-green-800'
              : 'bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800'
          }`}
        >
          <div className="flex items-start gap-3">
            {it.imageUrl && (
              <img src={it.imageUrl} alt={it.animeTitle} className="w-16 h-24 object-cover rounded" />
            )}
            <div className="flex-1">
              <div className="font-semibold text-gray-800 dark:text-gray-200">
                {it.animeTitle}
                <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">(作品ID: {it.animeId})</span>
              </div>
              <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                {it.episodeTitle}
                <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">(分集ID: {it.episodeId})</span>
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                <Tag color={it.type === 'tvseries' ? 'blue' : 'purple'}>{it.typeDescription}</Tag>
                {it.shift !== 0 && (
                  <Tag color="orange" className="ml-1">
                    偏移: {it.shift > 0 ? `+${it.shift}` : it.shift}
                  </Tag>
                )}
              </div>
            </div>
          </div>
        </div>
      ),
    },
    searchEpisodes: {
      label: '搜索分集',
      apiPath: '/api/v1/{token}/search/episodes',
      method: 'GET',
      handler: searchEpisodesTest,
      fields: [
        {
          name: 'anime',
          label: '节目名称',
          apiParam: 'anime (query)',
          placeholder: '请输入节目名称',
          required: true,
          component: Input,
        },
        {
          name: 'episode',
          label: '分集标题',
          apiParam: 'episode (query)',
          placeholder: '请输入分集标题（可选）',
          required: false,
          component: Input,
        },
      ],
      getListData: data => data?.animes || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.animeTitle || '').toLowerCase().includes(kw) ||
          (item.typeDescription || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        if (data?.animes && data.animes.length > 0) {
          return <div className="font-bold text-green-600">[搜索成功] 找到 {data.animes.length} 个结果</div>
        }
        return <div className="text-red-600">[搜索失败] 未找到结果</div>
      },
      renderItem: (anime, index) => (
        <div key={index} className="p-2 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-800">
          <div className="text-gray-800 dark:text-gray-200">番剧: {anime.animeTitle} (ID: {anime.animeId})</div>
          <div className="text-gray-600 dark:text-gray-400">类型: {anime.typeDescription}</div>
          {anime.episodes && <div className="text-gray-600 dark:text-gray-400">分集数: {anime.episodes.length}</div>}
        </div>
      ),
    },
    searchAnime: {
      label: '搜索作品',
      apiPath: '/api/v1/{token}/search/anime',
      method: 'GET',
      handler: searchAnimeTest,
      fields: [
        {
          name: 'keyword',
          label: '关键词',
          apiParam: 'keyword (query)',
          placeholder: '请输入搜索关键词',
          required: true,
          component: Input,
        },
      ],
      getListData: data => data?.animes || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.animeTitle || '').toLowerCase().includes(kw) ||
          (item.typeDescription || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        if (data?.animes && data.animes.length > 0) {
          return <div className="font-bold text-green-600">[搜索成功] 找到 {data.animes.length} 个结果</div>
        }
        return <div className="text-red-600">[搜索失败] 未找到结果</div>
      },
      renderItem: (anime, index) => (
        <div key={index} className="p-3 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-800">
          <div className="flex items-start gap-3">
            {anime.imageUrl && (
              <img src={anime.imageUrl} alt={anime.animeTitle} className="w-16 h-24 object-cover rounded" />
            )}
            <div className="flex-1">
              <div className="font-semibold text-gray-800 dark:text-gray-200">
                {anime.animeTitle}
                <span className="ml-2 text-xs text-gray-500 dark:text-gray-400">(ID: {anime.animeId})</span>
              </div>
              <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">类型: {anime.typeDescription}</div>
              <div className="text-sm text-gray-600 dark:text-gray-400">分集数: {anime.episodeCount || 0}</div>
              {anime.year && <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">{anime.year}年</div>}
            </div>
          </div>
        </div>
      ),
    },
    bangumiDetail: {
      label: '番剧详情',
      apiPath: '/api/v1/{token}/bangumi/{id}',
      method: 'GET',
      handler: getBangumiDetailTest,
      fields: [
        {
          name: 'bangumiId',
          label: '番剧ID',
          apiParam: 'id (path)',
          placeholder: '支持纯数字ID、A开头的备用ID（如 A900002）、或Bangumi ID',
          required: true,
          component: Input,
        },
      ],
      getListData: data => data?.bangumi?.episodes || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.episodeTitle || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        if (data?.bangumi) {
          const b = data.bangumi
          const epCount = b.episodes?.length || 0
          return (
            <div className="space-y-2">
              <div className="font-bold text-green-600">[查询成功]</div>
              <div className="flex items-start gap-3 p-3 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200 dark:border-blue-800">
                {b.imageUrl && (
                  <img src={b.imageUrl} alt={b.animeTitle} className="w-20 h-28 object-cover rounded" />
                )}
                <div className="flex-1 space-y-1">
                  <div className="font-semibold text-gray-800 dark:text-gray-200 text-base">{b.animeTitle}</div>
                  <div className="flex flex-wrap gap-1.5 text-xs">
                    <Tag color="blue">{b.typeDescription || b.type}</Tag>
                    {b.rating > 0 && <Tag color="gold">评分: {b.rating}</Tag>}
                    {b.isFavorited && <Tag color="red">已追番</Tag>}
                  </div>
                  <div className="text-sm text-gray-600 dark:text-gray-400">
                    作品ID: <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">{b.animeId}</code>
                    {b.bangumiId && <span className="ml-2">Bangumi: <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">{b.bangumiId}</code></span>}
                  </div>
                  <div className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    共 <span className="text-blue-600">{epCount}</span> 个分集
                  </div>
                </div>
              </div>
            </div>
          )
        }
        return (
          <div className="text-red-600">
            [查询失败] {data?.errorMessage || '未找到番剧详情'}
          </div>
        )
      },
      renderItem: (ep, index) => (
        <div key={index} className="flex items-center gap-3 py-2 px-3 rounded hover:bg-gray-100 dark:hover:bg-gray-800 border-b border-gray-100 dark:border-gray-800">
          <span className="text-gray-400 dark:text-gray-500 font-mono w-8 text-right shrink-0">
            {ep.episodeNumber != null ? ep.episodeNumber : index + 1}
          </span>
          <span className="flex-1 truncate" title={ep.episodeTitle}>{ep.episodeTitle || '未知'}</span>
          <code className="text-xs text-gray-400 dark:text-gray-500 shrink-0">ID: {ep.episodeId}</code>
        </div>
      ),
    },
    comment: {
      label: '弹幕获取',
      apiPath: '/api/v1/{token}/comment/{episodeId}',
      method: 'GET',
      handler: getCommentTest,
      fields: [
        {
          name: 'episodeId',
          label: '分集ID',
          apiParam: 'episodeId (path)',
          placeholder: '请输入分集ID',
          required: true,
          component: InputNumber,
          componentProps: { className: 'w-full', style: { width: '100%' } },
        },
        {
          name: 'chConvert',
          label: '简繁转换',
          apiParam: 'chConvert (query)',
          tooltip: '0-不转换，1-转为简体，2-转为繁体，默认 0',
          required: false,
          component: Select,
          componentProps: {
            placeholder: '默认不转换',
            allowClear: true,
            options: [
              { label: '0 - 不转换', value: 0 },
              { label: '1 - 转为简体', value: 1 },
              { label: '2 - 转为繁体', value: 2 },
            ],
          },
        },
        {
          name: 'withRelated',
          label: '包含关联弹幕',
          apiParam: 'withRelated (query)',
          tooltip: '是否包含关联番剧的弹幕，默认开启',
          required: false,
          component: Switch,
          componentProps: { checkedChildren: '是', unCheckedChildren: '否', defaultChecked: true },
          valuePropName: 'checked',
        },
        {
          name: 'asyncMode',
          label: '异步模式',
          apiParam: 'async (query)',
          tooltip: '开启后立即返回 taskId，不等待弹幕下载完成，适合弹幕尚未入库的场景',
          required: false,
          component: Switch,
          componentProps: { checkedChildren: '开', unCheckedChildren: '关' },
          valuePropName: 'checked',
        },
      ],
      getListData: data => data?.comments || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.m || '').toLowerCase().includes(kw) ||
          (item.p || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        if (!data?.comments || data.comments.length === 0) {
          return <div className="text-red-600">[获取失败] 未找到弹幕</div>
        }
        const comments = data.comments
        const total = data.count || comments.length

        // 解析所有弹幕的时间和模式
        const times = []
        const modeCount = {}
        const colorCount = {}
        const modeLabels = { 1: '滚动', 4: '底部', 5: '顶部', 6: '逆向', 7: '精准', 8: '高级' }
        for (const c of comments) {
          const parts = (c.p || '').split(',')
          const t = parseFloat(parts[0] || 0)
          const mode = parseInt(parts[1] || 1)
          const color = parseInt(parts[2] || 16777215)
          times.push(t)
          const ml = modeLabels[mode] || `模式${mode}`
          modeCount[ml] = (modeCount[ml] || 0) + 1
          const hex = '#' + color.toString(16).padStart(6, '0')
          colorCount[hex] = (colorCount[hex] || 0) + 1
        }

        // 热力图：动态分桶，确保桶数在 40~80 之间（手机上也能看清）
        const maxTime = Math.max(...times, 1)
        const targetBuckets = 60  // 目标桶数
        const bucketSize = Math.max(Math.ceil(maxTime / targetBuckets), 10)  // 最小10秒/桶
        const bucketCount = Math.ceil(maxTime / bucketSize)
        const buckets = new Array(bucketCount).fill(0)
        for (const t of times) {
          const idx = Math.min(Math.floor(t / bucketSize), bucketCount - 1)
          buckets[idx]++
        }
        const maxBucket = Math.max(...buckets, 1)
        const bucketLabel = bucketSize >= 60 ? `${Math.round(bucketSize / 60)}分钟` : `${bucketSize}秒`

        // 统计
        const durationMin = Math.floor(maxTime / 60)
        const durationSec = Math.floor(maxTime % 60)
        const topColors = Object.entries(colorCount).sort((a, b) => b[1] - a[1]).slice(0, 8)
        const modeEntries = Object.entries(modeCount).sort((a, b) => b[1] - a[1])

        return (
          <div className="space-y-3">
            <div className="font-bold text-green-600">[获取成功] 共 {total} 条弹幕</div>

            {/* 弹幕热力图 */}
            <div>
              <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">弹幕密度分布 (每{bucketLabel})</div>
              <div className="flex items-end gap-px h-16 bg-gray-100 dark:bg-gray-800 rounded p-1 overflow-hidden">
                {buckets.map((count, i) => {
                  const h = Math.max((count / maxBucket) * 100, count > 0 ? 4 : 0)
                  const intensity = count / maxBucket
                  const bg = intensity > 0.8 ? 'bg-red-500' : intensity > 0.5 ? 'bg-orange-400' : intensity > 0.2 ? 'bg-blue-400' : 'bg-blue-200 dark:bg-blue-700'
                  const startSec = i * bucketSize
                  const endSec = Math.min((i + 1) * bucketSize, maxTime)
                  const fmtTime = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
                  return (
                    <div key={i} className="flex-1 flex flex-col justify-end h-full" title={`${fmtTime(startSec)} ~ ${fmtTime(endSec)} — ${count} 条`}>
                      <div className={`${bg} rounded-t-sm transition-all`} style={{ height: `${h}%`, minWidth: 2 }} />
                    </div>
                  )
                })}
              </div>
              <div className="flex justify-between text-xs text-gray-400 dark:text-gray-500 mt-0.5 px-1">
                <span>0:00</span>
                <span>{durationMin}:{String(durationSec).padStart(2, '0')}</span>
              </div>
            </div>

            {/* 统计信息 */}
            <div className="flex flex-wrap gap-4 text-xs">
              {/* 弹幕模式分布 */}
              <div>
                <div className="text-gray-500 dark:text-gray-400 mb-1">模式分布</div>
                <div className="flex flex-wrap gap-1">
                  {modeEntries.map(([label, count]) => (
                    <Tag key={label} className="!text-xs !m-0">{label}: {count}</Tag>
                  ))}
                </div>
              </div>
              {/* 热门颜色 */}
              <div>
                <div className="text-gray-500 dark:text-gray-400 mb-1">热门颜色</div>
                <div className="flex gap-1">
                  {topColors.map(([hex, count]) => (
                    <div key={hex} className="flex items-center gap-0.5" title={`${hex} (${count}条)`}>
                      <span className="w-3 h-3 rounded-sm border border-gray-200 dark:border-gray-700" style={{ backgroundColor: hex }} />
                      <span className="text-gray-400 dark:text-gray-500">{count}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )
      },
      renderItem: (comment, index) => {
        // dandanplay 弹幕 p 格式: "时间,模式,颜色,uid"
        const parts = (comment.p || '').split(',')
        const time = parseFloat(parts[0] || 0)
        const mode = parseInt(parts[1] || 1)
        const color = parseInt(parts[2] || 16777215)
        const modeLabels = { 1: '滚动', 4: '底部', 5: '顶部', 6: '逆向', 7: '精准', 8: '高级' }
        const modeLabel = modeLabels[mode] || `模式${mode}`
        // 将十进制颜色转为 #RRGGBB
        const hexColor = '#' + color.toString(16).padStart(6, '0')
        const mins = Math.floor(time / 60)
        const secs = Math.floor(time % 60)
        const timeStr = `${mins}:${String(secs).padStart(2, '0')}`
        return (
          <div key={index} className="flex items-center gap-2 text-sm py-1.5 px-3 rounded hover:bg-gray-100 dark:hover:bg-gray-800 border-b border-gray-100 dark:border-gray-800">
            <span className="text-gray-400 font-mono w-12 shrink-0 text-right">{timeStr}</span>
            <span className="w-4 h-4 rounded-sm shrink-0 border border-gray-200" style={{ backgroundColor: hexColor }} title={hexColor} />
            <Tag className="shrink-0 !text-xs !px-1 !leading-4" color={mode === 5 ? 'red' : mode === 4 ? 'blue' : 'default'}>{modeLabel}</Tag>
            <span className="truncate flex-1" title={comment.m}>{comment.m}</span>
          </div>
        )
      },
    },
    taskcomment: {
      label: '弹幕任务轮询',
      apiPath: '/api/v1/{token}/taskcomment/{taskId}',
      method: 'GET',
      handler: pollTaskCommentTest,
      fields: [
        {
          name: 'taskId',
          label: '任务 ID',
          apiParam: 'taskId (path)',
          placeholder: '粘贴 async=1 接口返回的 taskId',
          required: true,
          component: Input,
        },
      ],
      renderResult: data => {
        const statusColor = {
          completed: 'text-green-600',
          pending: 'text-blue-500',
          failed: 'text-red-600',
        }[data?.status] || 'text-gray-600 dark:text-gray-400'
        const statusLabel = {
          completed: '[已完成]',
          pending: '[进行中]',
          failed: '[失败]',
        }[data?.status] || '[未知]'
        return (
          <div className="space-y-2">
            <div className={`font-bold ${statusColor}`}>
              {statusLabel}
              {data?.taskId && <span className="ml-2 text-xs font-mono text-gray-400 dark:text-gray-500">{data.taskId}</span>}
            </div>
            {data?.description && (
              <div className="text-sm text-gray-500 dark:text-gray-400">{data.description}</div>
            )}
            {data?.progress != null && data.status === 'pending' && (
              <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
                <div className="bg-blue-500 h-1.5 rounded-full transition-all" style={{ width: `${data.progress}%` }} />
              </div>
            )}
            {data?.episodeId != null && (
              <div className="text-sm text-gray-600 dark:text-gray-400">
                episodeId: <code className="bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded font-mono">{data.episodeId}</code>
                {data.status === 'completed' && (
                  <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">（使用此 ID 调用 /comment/{'{episodeId}'} 获取弹幕）</span>
                )}
              </div>
            )}
          </div>
        )
      },
    },
    fileRecognition: {
      label: '文件识别',
      apiPath: '/api/ui/tools/parse-filename',
      method: 'POST',
      noToken: true,
      handler: parseFilenameTest,
      fields: [
        {
          name: 'fileName',
          label: '文件名',
          apiParam: 'fileName (body)',
          placeholder: '例: [SubGroup] Anime Name S01E02 [1080p].mkv',
          required: true,
          component: Input,
        },
      ],
      renderResult: data => {
        if (data?.success && data.result) {
          const r = data.result
          const fields = [
            { label: '标题', value: r.title },
            { label: '原始标题', value: r.original_title },
            { label: '英文名', value: r.en_name },
            { label: '季', value: r.season },
            { label: '集', value: r.episode },
            { label: '类型', value: r.is_movie ? '电影' : '剧集' },
            { label: '年份', value: r.year },
            { label: '分辨率', value: r.resolution },
            { label: '视频编码', value: r.video_codec },
            { label: '音频编码', value: r.audio_codec },
            { label: '来源', value: r.source },
            { label: '字幕组', value: r.team },
            { label: '动态范围', value: r.dynamic_range },
            { label: '平台', value: r.platform },
            { label: '特效', value: r.effect },
          ].filter(f => f.value != null && f.value !== '')
          return (
            <div>
              <div className="font-bold text-green-600 mb-2">[识别成功]</div>
              <div className="space-y-1">
                {fields.map(f => (
                  <div key={f.label} className="flex gap-2 text-sm">
                    <span className="text-gray-500 dark:text-gray-400 w-20 shrink-0">{f.label}:</span>
                    <span className="font-mono">{String(f.value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )
        }
        return <div className="text-red-600">[识别失败] {data?.message || '无法识别该文件名'}</div>
      },
    },
  }

  const handleTest = async values => {
    try {
      setLoading(true)
      setResult(null)
      setSearchKeyword('')
      setCurrentPage(1)

      const config = testConfigs[activeTab]
      const res = await config.handler({ apiToken: values.apiToken, ...values })
      console.log('测试结果:', res)

      setResult(res?.data || res)
    } catch (error) {
      console.error('测试错误:', error)
      setResult({
        error: true,
        message: error?.detail || error?.message || JSON.stringify(error),
      })
    } finally {
      setLoading(false)
    }
  }

  const currentConfig = testConfigs[activeTab]

  // 是否为列表型结果（有 getListData 的 tab）
  const isListResult = !!currentConfig.getListData

  // 过滤 + 分页计算
  const { pagedList, totalFiltered } = useMemo(() => {
    if (!isListResult || !result || result.error) {
      return { pagedList: [], totalFiltered: 0 }
    }
    const allItems = currentConfig.getListData(result) || []
    // 搜索过滤
    const filtered = searchKeyword && currentConfig.searchFilter
      ? allItems.filter(item => currentConfig.searchFilter(item, searchKeyword))
      : allItems
    // 分页
    const start = (currentPage - 1) * pageSize
    const paged = filtered.slice(start, start + pageSize)
    return { pagedList: paged, totalFiltered: filtered.length }
  }, [result, activeTab, searchKeyword, currentPage, pageSize])

  return (
    <div className="my-4">
      <Card title="API 接口测试" extra={
        <Select
          value={activeTab}
          onChange={key => {
            setActiveTab(key)
            const fieldNames = testConfigs[key].fields.map(f => f.name)
            form.resetFields(fieldNames)
            setResult(null)
            setSearchKeyword('')
            setCurrentPage(1)
          }}
          style={{ width: 180 }}
          options={Object.entries(testConfigs).map(([key, config]) => ({
            value: key,
            label: config.label,
          }))}
        />
      }>
        <Tabs
          activeKey={activeTab}
          onChange={key => {
            setActiveTab(key)
            // 只重置动态字段，保留 apiToken 选中状态
            const fieldNames = testConfigs[key].fields.map(f => f.name)
            form.resetFields(fieldNames)
            setResult(null)
            setSearchKeyword('')
            setCurrentPage(1)
          }}
          items={Object.entries(testConfigs).map(([key, config]) => ({
            key,
            label: config.label,
          }))}
        />

        <div className="mt-4">
          <div className="max-w-2xl mx-auto">
            {/* 接口信息展示（内部工具类接口不显示） */}
            {!currentConfig.noToken && (
            <Alert
              message={
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Tag color="blue">{currentConfig.method}</Tag>
                    <code className="text-sm bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded">
                      {currentConfig.apiPath}
                    </code>
                  </div>
                  {currentConfig.fields.length > 0 && (
                    <div className="text-xs text-gray-600 dark:text-gray-400">
                      <div className="font-semibold mb-1">参数说明:</div>
                      <div className="pl-2">
                        {currentConfig.fields.map(field => (
                          <div key={field.name} className="mb-1">
                            <span className="font-mono text-blue-600 dark:text-blue-400">
                              {field.label}
                            </span>
                            {' → '}
                            <span className="text-gray-500 dark:text-gray-400">
                              {field.apiParam || field.name}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              }
              type="info"
              className="mb-4"
            />
            )}

            <Form
              form={form}
              layout="vertical"
              onFinish={handleTest}
              className="px-2"
            >
              {/* Token选择（不需要token的测试类型隐藏） */}
              {!currentConfig.noToken && (
              <Form.Item
                name="apiToken"
                label={
                  <div className="flex items-center justify-between w-full">
                    <div className="flex items-center gap-2">
                      <span>弹幕 Token</span>
                      <span className="text-xs text-gray-400 font-normal">
                        (token path)
                      </span>
                    </div>
                    <Button
                      type="link"
                      size="small"
                      onClick={fetchTokens}
                      loading={tokensLoading}
                      className="p-0 h-auto"
                    >
                      刷新
                    </Button>
                  </div>
                }
                rules={[{ required: true, message: '请选择弹幕token' }]}
              >
                <Select
                  placeholder={
                    tokens.length === 0
                      ? '暂无可用 Token，请先创建'
                      : '请选择弹幕token'
                  }
                  loading={tokensLoading}
                  showSearch
                  optionFilterProp="searchLabel"
                  onChange={val => localStorage.setItem(STORAGE_KEY, val)}
                  disabled={tokens.length === 0}
                  notFoundContent={
                    <div className="text-center p-4 text-gray-400">
                      暂无可用 Token
                      <br />
                      <span className="text-xs">
                        请在 Token 管理页面创建
                      </span>
                    </div>
                  }
                  options={tokens.map(token => ({
                    value: token.token,
                    searchLabel: token.name,
                    label: (
                      <div className="flex items-center justify-between">
                        <div className="flex flex-col">
                          <span>{token.name}</span>
                          {token.expiresAt && (
                            <span className="text-xs text-gray-400">
                              到期: {new Date(token.expiresAt).toLocaleDateString()}
                            </span>
                          )}
                        </div>
                        <Tag color="blue" className="ml-2 text-xs">
                          {token.dailyCallCount || 0}/
                          {token.dailyCallLimit === -1
                            ? '∞'
                            : token.dailyCallLimit}
                        </Tag>
                      </div>
                    ),
                  }))}
                />
              </Form.Item>
              )}

              {/* 动态字段 */}
              {currentConfig.fields.map(field => {
                const Component = field.component
                return (
                  <Form.Item
                    key={field.name}
                    name={field.name}
                    label={
                      <div className="flex items-center gap-2">
                        <span>{field.label}</span>
                        {field.apiParam && (
                          <span className="text-xs text-gray-400 font-normal">
                            ({field.apiParam})
                          </span>
                        )}
                      </div>
                    }
                    tooltip={field.tooltip}
                    valuePropName={field.valuePropName || 'value'}
                    rules={[
                      {
                        required: field.required,
                        message: `请输入${field.label}`,
                      },
                    ]}
                  >
                    <Component
                      placeholder={field.placeholder}
                      {...(field.componentProps || {})}
                    />
                  </Form.Item>
                )
              })}

              {/* 测试按钮 */}
              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={loading}
                  className="w-full h-11 text-base font-medium rounded-lg bg-primary hover:bg-primary/90 transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98]"
                >
                  测试
                </Button>
              </Form.Item>
            </Form>
          </div>

          {/* 结果区域（上下布局，全宽展示） */}
          {result && (
            <div className="mt-4 px-2">
              {/* 结果标题栏 + 视图切换开关 */}
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-gray-500 dark:text-gray-400">测试结果:</div>
                {/* 药丸形视图切换开关 */}
                <button
                  type="button"
                  onClick={() => setShowRaw(v => !v)}
                  className={`
                    flex items-center gap-1.5 px-1.5 py-1 rounded-full text-xs font-medium
                    border transition-all duration-200 select-none cursor-pointer
                    ${showRaw
                      ? 'bg-blue-50 border-blue-200 text-blue-600 dark:bg-blue-950 dark:border-blue-700 dark:text-blue-400'
                      : 'bg-gray-100 border-gray-200 text-gray-500 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-400'
                    }
                  `}
                >
                  <span className={`transition-all duration-200 px-1.5 py-0.5 rounded-full text-xs ${!showRaw ? 'bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 shadow-sm' : ''}`}>
                    格式化
                  </span>
                  {/* 滑块轨道 */}
                  <div className={`relative w-8 h-4 rounded-full transition-colors duration-200 ${showRaw ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'}`}>
                    <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white shadow transition-all duration-200 ${showRaw ? 'left-[18px]' : 'left-0.5'}`} />
                  </div>
                  <span className={`transition-all duration-200 px-1.5 py-0.5 rounded-full text-xs ${showRaw ? 'bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 shadow-sm' : ''}`}>
                    原始
                  </span>
                </button>
              </div>

              <div className="p-4 bg-gray-50 dark:bg-gray-900 rounded">
                {showRaw ? (
                  /* 原始 JSON 展示 */
                  <pre className="text-xs text-gray-700 dark:text-gray-300 overflow-auto max-h-[500px] whitespace-pre-wrap break-all leading-relaxed">
                    {JSON.stringify(result, null, 2)}
                  </pre>
                ) : result.error ? (
                  <div className="text-red-600">
                    <div className="font-bold">[错误]</div>
                    <div className="mt-2">{result.message}</div>
                  </div>
                ) : isListResult ? (
                  <>
                    {/* 头部信息 */}
                    {currentConfig.renderHeader(result)}

                    {/* 搜索栏 */}
                    {(currentConfig.getListData(result) || []).length > 0 && (
                      <div className="mt-2 mb-2 flex items-center gap-2">
                        <Input
                          placeholder="搜索结果..."
                          prefix={<SearchOutlined className="text-gray-400" />}
                          allowClear
                          value={searchKeyword}
                          onChange={e => {
                            setSearchKeyword(e.target.value)
                            setCurrentPage(1)
                          }}
                          size="small"
                        />
                        {searchKeyword && (
                          <span className="text-xs text-gray-400 whitespace-nowrap">
                            {totalFiltered} 条
                          </span>
                        )}
                      </div>
                    )}

                    {/* 滚动列表 */}
                    <div className="max-h-[500px] overflow-y-auto space-y-1">
                      {pagedList.length > 0 ? (
                        pagedList.map((item, index) =>
                          currentConfig.renderItem(item, (currentPage - 1) * pageSize + index, result)
                        )
                      ) : (
                        <div className="text-gray-400 text-center py-4">
                          {searchKeyword ? '没有匹配的结果' : '暂无数据'}
                        </div>
                      )}
                    </div>

                    {/* 分页 */}
                    {totalFiltered > pageSize && (
                      <div className="mt-3 flex justify-center">
                        <Pagination
                          current={currentPage}
                          pageSize={pageSize}
                          total={totalFiltered}
                          size="small"
                          showSizeChanger
                          pageSizeOptions={[10, 20, 50, 100]}
                          onChange={(page, size) => {
                            setCurrentPage(page)
                            setPageSize(size)
                          }}
                        />
                      </div>
                    )}
                  </>
                ) : (
                  currentConfig.renderResult(result)
                )}
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}


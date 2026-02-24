import {
  getMatchTest,
  searchEpisodesTest,
  searchAnimeTest,
  getBangumiDetailTest,
  getCommentTest,
  parseFilenameTest,
  getTokenList,
} from '../../../apis'
import { useState, useEffect, useMemo } from 'react'
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Tabs,
  InputNumber,
  Select,
  Tag,
  Alert,
  Pagination,
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

  // 加载 token 列表
  useEffect(() => {
    fetchTokens()
  }, [])

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
              ? 'bg-green-50 border-green-200'
              : 'bg-blue-50 border-blue-200'
          }`}
        >
          <div className="flex items-start gap-3">
            {it.imageUrl && (
              <img src={it.imageUrl} alt={it.animeTitle} className="w-16 h-24 object-cover rounded" />
            )}
            <div className="flex-1">
              <div className="font-semibold text-gray-800">
                {it.animeTitle}
                <span className="ml-2 text-xs text-gray-500">(作品ID: {it.animeId})</span>
              </div>
              <div className="text-sm text-gray-600 mt-1">
                {it.episodeTitle}
                <span className="ml-2 text-xs text-gray-400">(分集ID: {it.episodeId})</span>
              </div>
              <div className="text-xs text-gray-500 mt-1">
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
        <div key={index} className="p-2 bg-blue-50 rounded">
          <div>番剧: {anime.animeTitle} (ID: {anime.animeId})</div>
          <div>类型: {anime.typeDescription}</div>
          {anime.episodes && <div>分集数: {anime.episodes.length}</div>}
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
        <div key={index} className="p-2 bg-blue-50 rounded">
          <div>番剧: {anime.animeTitle} (ID: {anime.animeId})</div>
          <div>类型: {anime.typeDescription}</div>
          <div>分集数: {anime.episodeCount || 0}</div>
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
          placeholder: '请输入番剧ID',
          required: true,
          component: InputNumber,
          componentProps: { className: 'w-full', style: { width: '100%' } },
        },
      ],
      // 番剧详情是单条数据，不需要分页
      renderResult: data => {
        if (data?.bangumi) {
          const bangumi = data.bangumi
          return (
            <div className="font-bold text-green-600">
              <div>[查询成功]</div>
              <div className="mt-2 p-2 bg-blue-50 rounded font-normal">
                <div>标题: {bangumi.animeTitle}</div>
                <div>类型: {bangumi.typeDescription}</div>
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
      ],
      getListData: data => data?.comments || [],
      searchFilter: (item, keyword) => {
        const kw = keyword.toLowerCase()
        return (item.m || '').toLowerCase().includes(kw) ||
          (item.p || '').toLowerCase().includes(kw)
      },
      renderHeader: data => {
        if (data?.comments && data.comments.length > 0) {
          return <div className="font-bold text-green-600">[获取成功] 共 {data.count || data.comments.length} 条弹幕</div>
        }
        return <div className="text-red-600">[获取失败] 未找到弹幕</div>
      },
      renderItem: (comment, index) => (
        <div key={index} className="text-sm py-1 px-2 bg-purple-50 rounded">
          <span className="text-gray-400">[{comment.p}]</span> {comment.m}
        </div>
      ),
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
                    <span className="text-gray-500 w-20 shrink-0">{f.label}:</span>
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
      <Card title="API 接口测试">
        <Tabs
          activeKey={activeTab}
          onChange={key => {
            setActiveTab(key)
            form.resetFields()
            setResult(null)
            setSearchKeyword('')
            setCurrentPage(1)
          }}
          items={Object.entries(testConfigs).map(([key, config]) => ({
            key,
            label: config.label,
          }))}
        />

        <Row gutter={24} className="mt-4">
          <Col md={12} sm={24}>
            {/* 接口信息展示（内部工具类接口不显示） */}
            {!currentConfig.noToken && (
            <Alert
              message={
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Tag color="blue">{currentConfig.method}</Tag>
                    <code className="text-sm bg-gray-100 px-2 py-1 rounded">
                      {currentConfig.apiPath}
                    </code>
                  </div>
                  {currentConfig.fields.length > 0 && (
                    <div className="text-xs text-gray-600">
                      <div className="font-semibold mb-1">参数说明:</div>
                      <div className="pl-2">
                        {currentConfig.fields.map(field => (
                          <div key={field.name} className="mb-1">
                            <span className="font-mono text-blue-600">
                              {field.label}
                            </span>
                            {' → '}
                            <span className="text-gray-500">
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
              className="px-6 pb-6"
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
          </Col>

          <Col md={12} sm={24}>
            <div className="px-6">
              <div className="text-sm text-gray-500 mb-2">测试结果:</div>
              <div className="p-4 bg-gray-50 rounded">
                {result ? (
                  result.error ? (
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
                      <div className="max-h-[400px] overflow-y-auto space-y-2">
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
                  )
                ) : (
                  <div className="text-gray-400">
                    测试结果将显示在这里。
                  </div>
                )}
              </div>
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  )
}


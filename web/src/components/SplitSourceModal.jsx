import { useState, useEffect, useRef } from 'react'
import { Modal, Select, Checkbox, Form, Input, InputNumber, Radio, Spin, Empty, Button, List } from 'antd'
import { useTranslation } from 'react-i18next'
import { InfoCircleOutlined, ScissorOutlined } from '@ant-design/icons'
import { getSourceEpisodesForSplit, splitSource, getAnimeLibrary } from '../apis'
import { useMessage } from '../MessageContext'

export const SplitSourceModal = ({ open, animeId, animeTitle, sources, onCancel, onSuccess }) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const messageApi = useMessage()
  
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [selectedSourceId, setSelectedSourceId] = useState(null)
  const [episodes, setEpisodes] = useState([])
  const [selectedEpisodeIds, setSelectedEpisodeIds] = useState([])
  const lastClickedIndexRef = useRef(null) // Shift 多选：记录上次点击的索引
  const [rangeStart, setRangeStart] = useState(null) // 区间选择：起始集数
  const [rangeEnd, setRangeEnd] = useState(null) // 区间选择：结束集数
  const [targetType, setTargetType] = useState('new')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [libraryList, setLibraryList] = useState([])
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [selectedExistingId, setSelectedExistingId] = useState(null)

  // 重置状态
  useEffect(() => {
    if (open) {
      setSelectedSourceId(null)
      setEpisodes([])
      setSelectedEpisodeIds([])
      lastClickedIndexRef.current = null
      setRangeStart(null)
      setRangeEnd(null)
      setTargetType('new')
      setSearchKeyword('')
      setLibraryList([])
      setSelectedExistingId(null)
      form.resetFields()
      // 如果只有一个数据源，自动选中
      if (sources?.length === 1) {
        setSelectedSourceId(sources[0].sourceId)
      }
    }
  }, [open, sources, form])

  // 加载分集列表
  useEffect(() => {
    if (selectedSourceId) {
      loadEpisodes(selectedSourceId)
    }
  }, [selectedSourceId])

  const loadEpisodes = async (sourceId) => {
    setLoading(true)
    setSelectedEpisodeIds([]) // 切换数据源时清空已选分集
    try {
      const res = await getSourceEpisodesForSplit(sourceId)
      setEpisodes(res.data?.episodes || [])
    } catch (error) {
      console.error('加载分集列表失败:', error)
      messageApi.error(t('splitSource.loadEpisodesFailed'))
    } finally {
      setLoading(false)
    }
  }

  // 搜索已有条目
  const searchLibrary = async (keyword) => {
    if (!keyword?.trim()) {
      setLibraryList([])
      return
    }
    setLibraryLoading(true)
    try {
      const res = await getAnimeLibrary({ keyword: keyword.trim(), pageSize: 20 })
      // 过滤掉当前条目
      const filtered = (res.data?.list || []).filter(item => item.animeId !== animeId)
      setLibraryList(filtered)
    } catch (error) {
      messageApi.error(t('splitSource.searchFailed'))
    } finally {
      setLibraryLoading(false)
    }
  }

  const handleSubmit = async () => {
    if (!selectedSourceId) {
      messageApi.warning(t('splitSource.selectSource'))
      return
    }
    if (selectedEpisodeIds.length === 0) {
      messageApi.warning(t('splitSource.selectEpisodes'))
      return
    }

    let payload = {
      sourceId: selectedSourceId,
      episodeIds: selectedEpisodeIds,
      targetType,
      reindexEpisodes: true,
    }

    if (targetType === 'new') {
      try {
        const values = await form.validateFields()
        payload.newMediaInfo = {
          title: values.title,
          season: values.season || 1,
          year: values.year || null,
        }
      } catch {
        return
      }
    } else {
      if (!selectedExistingId) {
        messageApi.warning(t('splitSource.selectTarget'))
        return
      }
      payload.existingMediaId = selectedExistingId
    }

    setSubmitting(true)
    try {
      const res = await splitSource(animeId, payload)
      messageApi.success(res.data?.message || t('splitSource.splitSuccess'))
      onSuccess?.(res.data)
    } catch (error) {
      messageApi.error(error.detail || t('splitSource.splitFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleSelectAll = () => {
    if (selectedEpisodeIds.length === episodes.length) {
      setSelectedEpisodeIds([])
    } else {
      setSelectedEpisodeIds(episodes.map(ep => ep.episodeId))
    }
  }

  // 区间批量选择
  const handleRangeSelect = () => {
    if (rangeStart == null || rangeEnd == null) {
      messageApi.warning(t('splitSource.inputRange'))
      return
    }
    const min = Math.min(rangeStart, rangeEnd)
    const max = Math.max(rangeStart, rangeEnd)
    const rangeIds = episodes
      .filter(ep => ep.episodeIndex >= min && ep.episodeIndex <= max)
      .map(ep => ep.episodeId)
    if (rangeIds.length === 0) {
      messageApi.warning(t('splitSource.noMatchInRange'))
      return
    }
    setSelectedEpisodeIds(prev => {
      const newSet = new Set(prev)
      rangeIds.forEach(id => newSet.add(id))
      return [...newSet]
    })
  }

  // Shift 多选处理
  const handleEpisodeClick = (episodeId, index, e) => {
    e.preventDefault() // 阻止 Checkbox 默认行为，由我们手动控制
    const isSelected = selectedEpisodeIds.includes(episodeId)

    if (e.shiftKey && lastClickedIndexRef.current !== null) {
      // Shift + 点击：选中范围内的所有项
      const start = Math.min(lastClickedIndexRef.current, index)
      const end = Math.max(lastClickedIndexRef.current, index)
      const rangeIds = episodes.slice(start, end + 1).map(ep => ep.episodeId)
      // 将范围内的 id 合并到已选列表（去重）
      setSelectedEpisodeIds(prev => {
        const newSet = new Set(prev)
        rangeIds.forEach(id => newSet.add(id))
        return [...newSet]
      })
    } else {
      // 普通点击：切换单个选中状态
      if (isSelected) {
        setSelectedEpisodeIds(prev => prev.filter(id => id !== episodeId))
      } else {
        setSelectedEpisodeIds(prev => [...prev, episodeId])
      }
    }
    lastClickedIndexRef.current = index
  }

  return (
    <Modal
      title={<><ScissorOutlined className="mr-2" />{t('splitSource.title')}</>}
      open={open}
      onCancel={onCancel}
      onOk={handleSubmit}
      confirmLoading={submitting}
      width={600}
      destroyOnHidden
      okText={t('splitSource.okText')}
    >
      <div className="space-y-4 py-4">
        {/* 提示信息 */}
        <div className="p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded text-sm text-yellow-800 dark:text-yellow-200">
          <InfoCircleOutlined className="mr-2" />
          {t('splitSource.tip')}
        </div>

        {/* 第一步：选择数据源 */}
        <div>
          <div className="font-medium mb-2" style={{ color: 'var(--color-text)' }}>{t('splitSource.step1')}</div>
          <Select
            className="w-full"
            placeholder={t('splitSource.selectSourcePlaceholder')}
            value={selectedSourceId}
            onChange={(value) => {
              setSelectedSourceId(value)
            }}
            options={sources?.map(s => {
              return {
                value: s.sourceId,
                label: `${s.providerName} - ${s.mediaId} (${t('splitSource.episodeCountSuffix', { count: s.episodeCount || 0 })})`
              }
            })}
          />
        </div>

        {/* 第二步：选择分集 */}
        <div>
          <div className="font-medium mb-2 flex items-center justify-between" style={{ color: 'var(--color-text)' }}>
            <span>{t('splitSource.step2')}</span>
            {episodes.length > 0 && (
              <Button size="small" onClick={handleSelectAll}>
                {selectedEpisodeIds.length === episodes.length ? t('splitSource.unselectAll') : t('splitSource.selectAll')}
              </Button>
            )}
          </div>
          {/* 区间选择 */}
          {episodes.length > 0 && (
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-sm shrink-0" style={{ color: 'var(--color-text)' }}>{t('splitSource.rangeSelect')}</span>
              <span className="text-sm" style={{ color: 'var(--color-text)' }}>{t('splitSource.rangeFrom')}</span>
              <InputNumber
                size="small"
                min={1}
                placeholder={t('splitSource.rangeStart')}
                value={rangeStart}
                onChange={setRangeStart}
                style={{ width: 70 }}
              />
              <span className="text-sm" style={{ color: 'var(--color-text)' }}>~</span>
              <InputNumber
                size="small"
                min={1}
                placeholder={t('splitSource.rangeEnd')}
                value={rangeEnd}
                onChange={setRangeEnd}
                style={{ width: 70 }}
              />
              <span className="text-sm" style={{ color: 'var(--color-text)' }}>{t('splitSource.rangeUnit')}</span>
              <Button size="small" type="primary" onClick={handleRangeSelect}>
                {t('splitSource.select')}
              </Button>
            </div>
          )}
          {loading ? (
            <div className="text-center py-4"><Spin /></div>
          ) : episodes.length === 0 ? (
            <Empty description={selectedSourceId ? t('splitSource.noEpisodes') : t('splitSource.selectSourceFirst')} />
          ) : (
            <div className="max-h-48 overflow-y-auto border rounded p-2 dark:border-gray-600">
              <div className="flex flex-col gap-1">
                {episodes.map((ep, index) => (
                  <Checkbox
                    key={ep.episodeId}
                    checked={selectedEpisodeIds.includes(ep.episodeId)}
                    className="!ml-0"
                    onClick={(e) => handleEpisodeClick(ep.episodeId, index, e)}
                    onChange={() => {}} // 由 onClick 控制，这里阻止默认行为
                  >
                    <span style={{ color: 'var(--color-text)' }}>
                      {t('splitSource.episodeLine', { index: ep.episodeIndex, title: ep.title, count: ep.commentCount })}
                    </span>
                  </Checkbox>
                ))}
              </div>
            </div>
          )}
          {selectedEpisodeIds.length > 0 && (
            <div className="text-sm text-gray-500 mt-1">{t('splitSource.selectedCount', { count: selectedEpisodeIds.length })}</div>
          )}
        </div>

        {/* 第三步：目标设置 */}
        <div>
          <div className="font-medium mb-2" style={{ color: 'var(--color-text)' }}>{t('splitSource.step3')}</div>
          <div className="mb-3">
            <Radio.Group value={targetType} onChange={e => setTargetType(e.target.value)}>
              <Radio value="new"><span style={{ color: 'var(--color-text)' }}>{t('splitSource.createNew')}</span></Radio>
              <Radio value="existing"><span style={{ color: 'var(--color-text)' }}>{t('splitSource.mergeExisting')}</span></Radio>
            </Radio.Group>
          </div>

          {targetType === 'new' ? (
            <div className="p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
              <Form form={form} layout="vertical" requiredMark={false}>
                <Form.Item
                  name="title"
                  label={t('splitSource.titleLabel')}
                  rules={[{ required: true, message: t('splitSource.inputTitle') }]}
                  initialValue={animeTitle}
                >
                  <Input placeholder={t('splitSource.newTitlePlaceholder')} />
                </Form.Item>
                <div className="flex gap-4">
                  <Form.Item
                    name="season"
                    label={t('splitSource.season')}
                    initialValue={1}
                    className="flex-1 !mb-0"
                  >
                    <InputNumber min={1} className="w-full" />
                  </Form.Item>
                  <Form.Item
                    name="year"
                    label={t('splitSource.year')}
                    className="flex-1 !mb-0"
                  >
                    <InputNumber min={1900} max={2100} className="w-full" placeholder={t('splitSource.optional')} />
                  </Form.Item>
                </div>
              </Form>
            </div>
          ) : (
            <div className="p-3 rounded" style={{ backgroundColor: 'var(--color-hover)' }}>
              <Input.Search
                placeholder={t('splitSource.searchTargetPlaceholder')}
                value={searchKeyword}
                onChange={e => setSearchKeyword(e.target.value)}
                onSearch={searchLibrary}
                enterButton
                loading={libraryLoading}
              />
              {libraryList.length > 0 && (
                <List
                  className="mt-2 max-h-40 overflow-y-auto"
                  size="small"
                  dataSource={libraryList}
                  renderItem={item => (
                    <List.Item
                      className={`cursor-pointer rounded px-2 ${selectedExistingId === item.animeId ? 'bg-blue-100 dark:bg-blue-900' : 'hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                      onClick={() => setSelectedExistingId(item.animeId)}
                    >
                      <span className="text-gray-900 dark:text-gray-100">
                        {item.title} (S{String(item.season).padStart(2, '0')})
                      </span>
                    </List.Item>
                  )}
                />
              )}
              {selectedExistingId && (
                <div className="text-sm text-green-600 dark:text-green-400 mt-2">
                  {t('splitSource.selectedTargetId', { id: selectedExistingId })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}


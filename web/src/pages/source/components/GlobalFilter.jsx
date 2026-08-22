import { Button, Card, Form, Input, Modal, Select, Switch, Tabs, Tooltip } from 'antd'
import { Fragment, useEffect, useState } from 'react'
import {
  getGlobalFilter,
  setGlobalFilter,
  getGlobalFilterDefaults,
  getSingleEpisodeFilter,
  setSingleEpisodeFilter,
  getGlobalEpisodeTitleFilter,
  setGlobalEpisodeTitleFilter,
  getGlobalEpisodeTitleFilterDefaults,
  getAnimeLibrary,
  getAnimeSource,
  getScrapers,
  getSingleScraper,
  setSingleScraper,
  getScraperDefaultBlacklist,
  getCommonBlacklist,
  generateRegex,
  testRegexPatterns,
} from '../../../apis'
import { QuestionCircleOutlined, RobotOutlined } from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'
import { useHashTab } from '@/hooks/useHashTab'

// 功能搜索锚点 -> 内层 Tab key。定义在模块级保证引用稳定。
const FILTER_ANCHOR_TABS = {
  'feat-global-filter': 'global',
  'feat-episode-title-filter': 'episodeFilter',
  'feat-single-filter': 'single',
}

export const GlobalFilter = () => {
  const { t } = useTranslation()
  // 功能搜索深链：锚点哈希映射到内层 Tab key
  const [activeTab, setActiveTab] = useHashTab(FILTER_ANCHOR_TABS, 'global')
  const [loading, setLoading] = useState(true)
  const [form] = Form.useForm()
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [singleFilterContent, setSingleFilterContent] = useState('')
  const [singleDraft, setSingleDraft] = useState({ title: '', rules: '', provider: '', mediaId: '' })
  const [libraryOptions, setLibraryOptions] = useState([])
  const [sourceOptions, setSourceOptions] = useState([])
  const [providerOptions, setProviderOptions] = useState([])
  const [hoveredAnime, setHoveredAnime] = useState(null)
  const [animeSourcesMap, setAnimeSourcesMap] = useState({})
  const [titleDropdownOpen, setTitleDropdownOpen] = useState(false)
  const [aiOpen, setAiOpen] = useState(false)
  const [aiDesc, setAiDesc] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  // AI 弹窗目标：'single'=单剧过滤草稿 / 'source'=单源分集正则，决定生成结果写回哪里
  const [aiTarget, setAiTarget] = useState('single')
  // AI 生成结果（先预览再应用，避免直接覆盖文本域造成误操作）
  const [aiResult, setAiResult] = useState('')
  const [isSingleSaveLoading, setIsSingleSaveLoading] = useState(false)
  const [episodeFilterEnabled, setEpisodeFilterEnabled] = useState(false)
  const [isLoadingEpisodeDefaults, setIsLoadingEpisodeDefaults] = useState(false)
  const [episodeFilterRegex, setEpisodeFilterRegex] = useState('')
  const [isEpisodeFilterSaveLoading, setIsEpisodeFilterSaveLoading] = useState(false)
  // 单源分集标题过滤：当前选中的源、该源的黑名单正则、各类 loading
  const [selectedEpisodeSource, setSelectedEpisodeSource] = useState('')
  const [sourceEpisodeRegex, setSourceEpisodeRegex] = useState('')
  const [isLoadingSourceEpisode, setIsLoadingSourceEpisode] = useState(false)
  const [isSourceEpisodeSaveLoading, setIsSourceEpisodeSaveLoading] = useState(false)
  const [isLoadingSourceEpisodeDefaults, setIsLoadingSourceEpisodeDefaults] = useState(false)
  const [isLoadingSourceEpisodeCommon, setIsLoadingSourceEpisodeCommon] = useState(false)
  const [isLoadingDefaults, setIsLoadingDefaults] = useState({ cn: false, eng: false })
  const [regexTestOpen, setRegexTestOpen] = useState(false)
  const [regexTestTitle, setRegexTestTitle] = useState('')
  const [regexTestText, setRegexTestText] = useState('')
  const [regexTestPatterns, setRegexTestPatterns] = useState([])
  const [regexTestResult, setRegexTestResult] = useState(null)
  const [regexTestLoading, setRegexTestLoading] = useState(false)

  const messageApi = useMessage()

  useEffect(() => {
    Promise.all([getGlobalFilter(), getSingleEpisodeFilter(), getScrapers(), getGlobalEpisodeTitleFilter()])
      .then(([globalRes, singleRes, scraperRes, episodeFilterRes]) => {
        form.setFieldsValue(globalRes.data ?? { cn: '', eng: '' })
        setSingleFilterContent(singleRes.data?.content ?? '')
        setProviderOptions((scraperRes.data || [])
          .map(item => ({ label: item.providerName || item.name, value: item.providerName || item.name }))
          .filter(item => item.value))
        setEpisodeFilterEnabled(episodeFilterRes.data?.enabled ?? false)
        const savedRegex = episodeFilterRes.data?.regex ?? ''
        setEpisodeFilterRegex(savedRegex)
        // config 为空时自动填充默认正则
        if (!savedRegex) {
          getGlobalEpisodeTitleFilterDefaults().then(defRes => {
            if (defRes.data?.regex) setEpisodeFilterRegex(defRes.data.regex)
          }).catch(() => {})
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }, [form])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const values = await form.validateFields()
      await setGlobalFilter(values)
      messageApi.success(t('globalFilter.saveSuccess'))
    } catch (error) {
      messageApi.error(t('globalFilter.saveFailed'))
    } finally {
      setIsSaveLoading(false)
    }
  }

  // 填充默认规则
  const handleFillDefault = async (field) => {
    try {
      setIsLoadingDefaults(prev => ({ ...prev, [field]: true }))
      const res = await getGlobalFilterDefaults()
      if (res.data && res.data[field]) {
        form.setFieldValue(field, res.data[field])
        messageApi.success(t('globalFilter.filledDefaultRules'))
      } else {
        messageApi.warning(t('globalFilter.noDefaultRules'))
      }
    } catch (error) {
      messageApi.error(t('globalFilter.getDefaultRulesFailed'))
    } finally {
      setIsLoadingDefaults(prev => ({ ...prev, [field]: false }))
    }
  }

  const handleSearchLibrary = async (keyword) => {
    if (!keyword?.trim()) return
    setSingleDraft(prev => ({ ...prev, title: keyword }))
    const res = await getAnimeLibrary({ keyword, page: 1, pageSize: 10 })
    const animes = res.data?.list || []
    const sourcesMap = {}
    await Promise.all(animes.map(async item => {
      const sourceRes = await getAnimeSource({ animeId: item.animeId })
      sourcesMap[item.animeId] = (sourceRes.data || []).map(source => ({
        provider: source.providerName,
        mediaId: source.mediaId,
      }))
    }))
    setAnimeSourcesMap(sourcesMap)
    setLibraryOptions(animes.map(item => ({
      label: item.title,
      value: item.title,
      animeId: item.animeId,
    })))
  }

  const handleSelectLibrary = async (_, option) => {
    const sources = animeSourcesMap[option.animeId] || []
    setSourceOptions(sources.map(item => ({
      label: `${item.provider} / ${item.mediaId}`,
      value: item.mediaId,
      provider: item.provider,
      mediaId: item.mediaId,
    })))

    if (sources.length === 1) {
      setSingleDraft(prev => ({
        ...prev,
        title: option.value,
        provider: sources[0].provider,
        mediaId: sources[0].mediaId,
      }))
    } else {
      setSingleDraft(prev => ({
        ...prev,
        title: option.value,
        provider: '',
        mediaId: '',
      }))
    }
    setTitleDropdownOpen(false)
  }

  const handleSelectAnimeSource = (animeOption, source) => {
    setSingleDraft(prev => ({
      ...prev,
      title: animeOption.label,
      provider: source.provider,
      mediaId: source.mediaId,
    }))
    setHoveredAnime(null)
    setTitleDropdownOpen(false)
  }


  const handleSaveEpisodeFilter = async () => {
    try {
      setIsEpisodeFilterSaveLoading(true)
      await setGlobalEpisodeTitleFilter({ enabled: episodeFilterEnabled, regex: episodeFilterRegex })
      messageApi.success(t('globalEpisodeTitleFilter.saveSuccess'))
    } catch (error) {
      messageApi.error(t('globalEpisodeTitleFilter.saveFailed'))
    } finally {
      setIsEpisodeFilterSaveLoading(false)
    }
  }

  const handleFillEpisodeDefaults = async () => {
    try {
      setIsLoadingEpisodeDefaults(true)
      const res = await getGlobalEpisodeTitleFilterDefaults()
      if (res.data?.regex) {
        setEpisodeFilterRegex(res.data.regex)
        messageApi.success(t('globalFilter.filledDefaultRules'))
      } else {
        messageApi.warning(t('globalFilter.noDefaultRules'))
      }
    } catch (error) {
      messageApi.error(t('globalFilter.getDefaultRulesFailed'))
    } finally {
      setIsLoadingEpisodeDefaults(false)
    }
  }

  // 切换源：拉取该源的分集标题黑名单正则（切换即丢弃未保存修改）
  const handleSelectEpisodeSource = async (providerName) => {
    setSelectedEpisodeSource(providerName || '')
    if (!providerName) {
      setSourceEpisodeRegex('')
      return
    }
    try {
      setIsLoadingSourceEpisode(true)
      const res = await getSingleScraper({ name: providerName })
      // 后端返回驼峰键：{provider}EpisodeBlacklistRegex
      setSourceEpisodeRegex(res.data?.[`${providerName}EpisodeBlacklistRegex`] || '')
    } catch (error) {
      messageApi.error(t('sourceEpisodeFilter.loadFailed'))
      setSourceEpisodeRegex('')
    } finally {
      setIsLoadingSourceEpisode(false)
    }
  }

  // 填充该源默认分集黑名单正则
  const handleFillSourceEpisodeDefaults = async () => {
    if (!selectedEpisodeSource) {
      messageApi.warning(t('sourceEpisodeFilter.selectSourceFirst'))
      return
    }
    try {
      setIsLoadingSourceEpisodeDefaults(true)
      const res = await getScraperDefaultBlacklist(selectedEpisodeSource)
      if (res.data?.defaultBlacklist) {
        setSourceEpisodeRegex(res.data.defaultBlacklist)
        messageApi.success(t('globalFilter.filledDefaultRules'))
      } else {
        messageApi.warning(t('globalFilter.noDefaultRules'))
      }
    } catch (error) {
      messageApi.error(t('globalFilter.getDefaultRulesFailed'))
    } finally {
      setIsLoadingSourceEpisodeDefaults(false)
    }
  }

  // 填充通用默认分集黑名单正则（所有源共用的一份通用规则）
  const handleFillSourceEpisodeCommon = async () => {
    if (!selectedEpisodeSource) {
      messageApi.warning(t('sourceEpisodeFilter.selectSourceFirst'))
      return
    }
    try {
      setIsLoadingSourceEpisodeCommon(true)
      const res = await getCommonBlacklist()
      if (res.data?.commonBlacklist) {
        setSourceEpisodeRegex(res.data.commonBlacklist)
        messageApi.success(t('globalFilter.filledDefaultRules'))
      } else {
        messageApi.warning(t('globalFilter.noDefaultRules'))
      }
    } catch (error) {
      messageApi.error(t('globalFilter.getDefaultRulesFailed'))
    } finally {
      setIsLoadingSourceEpisodeCommon(false)
    }
  }

  // 保存该源黑名单：后端为部分更新，仅提交黑名单字段，不影响该源其他配置
  const handleSaveSourceEpisodeFilter = async () => {
    if (!selectedEpisodeSource) {
      messageApi.warning(t('sourceEpisodeFilter.selectSourceFirst'))
      return
    }
    try {
      setIsSourceEpisodeSaveLoading(true)
      await setSingleScraper({
        name: selectedEpisodeSource,
        [`${selectedEpisodeSource}EpisodeBlacklistRegex`]: sourceEpisodeRegex,
      })
      messageApi.success(t('sourceEpisodeFilter.saveSuccess'))
    } catch (error) {
      messageApi.error(t('sourceEpisodeFilter.saveFailed'))
    } finally {
      setIsSourceEpisodeSaveLoading(false)
    }
  }

  const renderAnimeDropdown = () => {
    const hoveredSources = hoveredAnime ? (animeSourcesMap[hoveredAnime] || []) : []
    return (
      <div className="flex">
        <div className="max-h-64 overflow-y-auto flex-1 min-w-0">
          {libraryOptions.map(option => {
            const sources = animeSourcesMap[option.animeId] || []
            const isMultiSource = sources.length >= 2
            const isHovered = hoveredAnime === option.animeId
            return (
              <div
                key={option.animeId}
                className={`px-3 py-2 cursor-pointer transition-colors truncate ${isHovered ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300' : 'hover:bg-gray-100 dark:hover:bg-white/10'}`}
                onMouseEnter={() => setHoveredAnime(isMultiSource ? option.animeId : null)}
                onClick={() => !isMultiSource && handleSelectLibrary(undefined, option)}
              >
                <span>{option.label}</span>
                {isMultiSource && <span className="ml-1 text-gray-400">›</span>}
              </div>
            )
          })}
        </div>
        {hoveredSources.length > 0 && (
          <div
            className="max-h-64 overflow-y-auto border-l border-gray-200 dark:border-white/10 min-w-[180px]"
            onMouseEnter={() => {}}
            onMouseLeave={() => setHoveredAnime(null)}
          >
            {hoveredSources.map((source, idx) => (
              <div
                key={idx}
                className="px-3 py-2 cursor-pointer hover:bg-gray-100 dark:hover:bg-white/10 transition-colors"
                onClick={() => {
                  const animeOption = libraryOptions.find(o => o.animeId === hoveredAnime)
                  if (animeOption) handleSelectAnimeSource(animeOption, source)
                }}
              >
                <div className="text-sm">{source.provider}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{source.mediaId}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  const handleSelectSource = (value, option) => {
    setSingleDraft(prev => ({
      ...prev,
      provider: option.provider ?? value ?? '',
      mediaId: option.mediaId ?? '',
    }))
  }

  // 打开 AI 弹窗：target 决定生成结果写回哪个文本域（single=单剧草稿 / source=单源正则）
  const openAiModal = (target) => {
    setAiTarget(target)
    setAiDesc('')
    setAiResult('')
    setAiOpen(true)
  }

  // 按当前目标取"现有正则"，作为 AI 生成的上下文参考
  // single=单剧草稿 / source=单源正则 / episode=兜底全局正则 / cn=标题中文规则 / eng=标题英文规则
  const getAiCurrentRegex = () => {
    if (aiTarget === 'source') return sourceEpisodeRegex || ''
    if (aiTarget === 'episode') return episodeFilterRegex || ''
    if (aiTarget === 'cn') return form.getFieldValue('cn') || ''
    if (aiTarget === 'eng') return form.getFieldValue('eng') || ''
    return singleDraft.rules || ''
  }

  // 生成正则：结果先存入 aiResult 供预览对比，不直接覆盖文本域
  const handleGenerateRulesByAI = async () => {
    if (!aiDesc.trim()) {
      messageApi.warning(t('aiRegexModal.emptyDescWarn'))
      return
    }
    try {
      setAiLoading(true)
      const res = await generateRegex(aiDesc, getAiCurrentRegex(), 'episode_blacklist')
      if (res.data?.regex) {
        setAiResult(res.data.regex)
      } else {
        messageApi.warning(t('aiRegexModal.noResult'))
      }
    } catch (error) {
      messageApi.error(t('aiRegexModal.failed'))
    } finally {
      setAiLoading(false)
    }
  }

  // 应用 AI 结果：mode='overwrite' 覆盖 / 'append' 追加，按 aiTarget 写回对应文本域
  const handleApplyAiResult = (mode) => {
    if (!aiResult.trim()) return
    const merge = (current) => {
      if (mode === 'append' && current && current.trim()) {
        // 追加时用 | 连接，避免与现有正则冲突
        return `${current.trim()}|${aiResult.trim()}`
      }
      return aiResult.trim()
    }
    if (aiTarget === 'source') {
      setSourceEpisodeRegex(prev => merge(prev))
    } else if (aiTarget === 'episode') {
      setEpisodeFilterRegex(prev => merge(prev))
    } else if (aiTarget === 'cn' || aiTarget === 'eng') {
      // 标题过滤的中文/英文规则由 antd Form 管理，通过 form 读写
      form.setFieldsValue({ [aiTarget]: merge(form.getFieldValue(aiTarget)) })
    } else {
      setSingleDraft(prev => ({ ...prev, rules: merge(prev.rules) }))
    }
    messageApi.success(mode === 'append' ? t('aiRegexModal.appended') : t('aiRegexModal.overwritten'))
    setAiOpen(false)
    setAiDesc('')
    setAiResult('')
  }

  const handleSaveSingleFilter = async () => {
    try {
      setIsSingleSaveLoading(true)
      await setSingleEpisodeFilter({ content: singleFilterContent })
      messageApi.success(t('singleEpisodeFilter.saveSuccess'))
    } catch (error) {
      messageApi.error(t('singleEpisodeFilter.saveFailed'))
    } finally {
      setIsSingleSaveLoading(false)
    }
  }

  const handleInsertSingleDraft = () => {
    const title = singleDraft.title.trim()
    const rules = singleDraft.rules.trim()
    if (!title || !rules) {
      messageApi.warning(t('singleEpisodeFilter.draftRequired'))
      return
    }

    const fields = [`rules=${rules}`]
    if (singleDraft.provider.trim()) fields.push(`provider=${singleDraft.provider.trim()}`)
    if (singleDraft.mediaId.trim()) fields.push(`mediaId=${singleDraft.mediaId.trim()}`)
    const line = `${title} => {[${fields.join(';')}]}`
    setSingleFilterContent(prev => prev ? `${prev.trim()}\n${line}` : line)
  }

  const handleInsertDefaultFormat = () => {
    const line = t('singleEpisodeFilter.defaultFormat')
    setSingleFilterContent(prev => prev ? `${prev.trim()}\n${line}` : line)
  }

  const extractRegexParts = (content, labelPrefix, splitAlternatives = false) => {
    const lines = String(content || '').split(/\r?\n/)
    const patterns = []
    lines.forEach((line, lineIndex) => {
      const clean = line.trim()
      if (!clean || clean.startsWith('#')) return
      const parts = splitAlternatives ? clean.split('|') : [clean]
      parts.forEach((part, partIndex) => {
        const pattern = part.trim()
        if (pattern) {
          patterns.push({
            label: `${labelPrefix} #${lineIndex + 1}${splitAlternatives ? `.${partIndex + 1}` : ''}`,
            pattern,
          })
        }
      })
    })
    return patterns
  }

  const extractSingleFilterPatterns = () => {
    const patterns = []
    String(singleFilterContent || '').split(/\r?\n/).forEach((line, lineIndex) => {
      const clean = line.trim()
      if (!clean || clean.startsWith('#')) return
      const match = clean.match(/^(.+?)\s*=>\s*\{\[(.*)\]\}\s*$/)
      if (!match) return
      const title = match[1].trim()
      const fields = match[2].split(';').map(item => item.trim())
      const rulesField = fields.find(item => item.startsWith('rules='))
      const rules = rulesField ? rulesField.slice('rules='.length) : ''
      if (rules) patterns.push({ label: `${title} #${lineIndex + 1}`, pattern: rules })
    })
    if (singleDraft.title.trim() && singleDraft.rules.trim()) {
      patterns.push({ label: `${singleDraft.title.trim()} (${t('singleEpisodeFilter.quickTitle')})`, pattern: singleDraft.rules.trim() })
    }
    return patterns
  }

  const openRegexTestModal = (title, patterns) => {
    setRegexTestTitle(title)
    setRegexTestPatterns((patterns || []).filter(item => item.pattern))
    setRegexTestText('')
    setRegexTestResult(null)
    setRegexTestOpen(true)
  }

  const handleRunRegexTest = async () => {
    if (!regexTestText.trim()) {
      messageApi.warning(t('regexTester.empty'))
      return
    }
    try {
      setRegexTestLoading(true)
      const res = await testRegexPatterns({ text: regexTestText, patterns: regexTestPatterns })
      setRegexTestResult(res.data)
    } catch (error) {
      messageApi.error(t('regexTester.failed'))
    } finally {
      setRegexTestLoading(false)
    }
  }


  return (
    <>
    {/* 最外层卡片：包裹整个过滤配置模块，提供清晰的视觉边界与层次感 */}
    <Card
      className="my-6"
      id="feat-global-filter"
      styles={{ body: { padding: 0 } }}
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        className="[&_.ant-tabs-nav]:px-5 [&_.ant-tabs-nav]:pt-2 [&_.ant-tabs-nav]:mb-0"
        items={[
          {
            key: 'global',
            label: t('globalFilter.title'),
            children: loading ? null : (
              // 每个 Tab 内容区统一 padding，与卡片边框保持间距
              <div className="px-5 py-5">
                <div className="mb-5">
                  {/* 过滤层级说明区块 */}
                  <div className="bg-blue-50 dark:bg-blue-900/30 px-4 py-3 rounded-lg border border-blue-100 dark:border-blue-800/40">
                    <p className="font-semibold text-blue-800 dark:text-blue-300 mb-2 text-sm">
                      {t('globalFilter.filterLevelTitle')}
                    </p>
                    <pre className="text-blue-700 dark:text-blue-400 text-xs mb-2 whitespace-pre-wrap font-mono bg-white/60 dark:bg-gray-800/50 px-3 py-2 rounded">
                      {t('globalFilter.filterLevelTree')}
                    </pre>
                    <div className="text-blue-600 dark:text-blue-400 text-xs space-y-0.5">
                      <p>{t('globalFilter.episodeFilterTip')}</p>
                      <p>{t('globalFilter.episodeFilterTip2')}</p>
                    </div>
                  </div>
                </div>
                <Form form={form} layout="vertical" onFinish={handleSave}>
                  <Form.Item
                    name="cn"
                    label={
                      <div className="flex items-center justify-between w-full">
                        <span>
                          {t('globalFilter.cnRules')}
                          <Tooltip title={t('globalFilter.cnRulesTip')}>
                            <QuestionCircleOutlined className="ml-2 cursor-pointer text-gray-400" />
                          </Tooltip>
                        </span>
                        <div className="flex items-center">
                          <Button type="link" size="small" loading={isLoadingDefaults.cn} onClick={() => handleFillDefault('cn')}>
                            {t('globalFilter.fillDefaultRules')}
                          </Button>
                          {/* AI 生成：复用统一 AI 弹窗，写回中文规则文本域 */}
                          <Button type="link" size="small" icon={<RobotOutlined />} onClick={() => openAiModal('cn')}>
                            {t('globalFilter.aiGenerate')}
                          </Button>
                        </div>
                      </div>
                    }
                    className="mb-5"
                  >
                    <Input.TextArea rows={4} placeholder={t('globalFilter.cnRulesPlaceholder')} />
                  </Form.Item>
                  <Form.Item
                    name="eng"
                    label={
                      <div className="flex items-center justify-between w-full">
                        <span>
                          {t('globalFilter.enRules')}
                          <Tooltip title={t('globalFilter.enRulesTip')}>
                            <QuestionCircleOutlined className="ml-2 cursor-pointer text-gray-400" />
                          </Tooltip>
                        </span>
                        <div className="flex items-center">
                          <Button type="link" size="small" loading={isLoadingDefaults.eng} onClick={() => handleFillDefault('eng')}>
                            {t('globalFilter.fillDefaultRules')}
                          </Button>
                          {/* AI 生成：复用统一 AI 弹窗，写回英文规则文本域 */}
                          <Button type="link" size="small" icon={<RobotOutlined />} onClick={() => openAiModal('eng')}>
                            {t('globalFilter.aiGenerate')}
                          </Button>
                        </div>
                      </div>
                    }
                    className="mb-5"
                  >
                    <Input.TextArea rows={4} placeholder={t('globalFilter.enRulesPlaceholder')} />
                  </Form.Item>
                  {/* 底部操作栏：用浅色分割线与上方内容区隔开 */}
                  <div className="flex justify-between gap-2 pt-4 border-t border-gray-100 dark:border-white/8">
                    <Button
                      onClick={() => openRegexTestModal(t('globalFilter.title'), [
                        ...extractRegexParts(form.getFieldValue('cn'), t('globalFilter.cnRules')),
                        ...extractRegexParts(form.getFieldValue('eng'), t('globalFilter.enRules')),
                      ])}
                    >
                      {t('regexTester.title')}
                    </Button>
                    <Button type="primary" htmlType="submit" loading={isSaveLoading}>{t('globalFilter.saveChanges')}</Button>
                  </div>
                </Form>
              </div>
            ),
          },

          {
            key: 'episodeFilter',
            label: t('globalEpisodeTitleFilter.tabTitle'),
            children: loading ? null : (
              // 左右两卡片：桌面端并排，窄屏（lg 以下）自动上下堆叠
              <div className="px-5 py-5" id="feat-episode-title-filter">
                {/* 分集过滤流程说明区块：横向三阶段流程（①单源黑名单 →②兜底过滤 →③单剧过滤），
                    桌面端从左往右并排、箭头串联，窄屏（md 以下）自动竖排堆叠 */}
                <div className="mb-5 bg-blue-50 dark:bg-blue-900/30 px-4 py-3 rounded-lg border border-blue-100 dark:border-blue-800/40">
                  <p className="font-semibold text-blue-800 dark:text-blue-300 mb-3 text-sm">
                    {t('episodeFilterFlow.title')}
                  </p>
                  {/* 起点 + 三阶段横向流程卡片 */}
                  <div className="flex flex-col md:flex-row md:items-stretch gap-2 mb-3">
                    {/* 流程起点：源站返回的原始分集标题列表（不参与过滤，灰色调区分于①②③） */}
                    <div className="flex-1 bg-gray-100/80 dark:bg-gray-700/40 rounded-lg px-3 py-2.5 border border-gray-200 dark:border-white/10">
                      <div className="flex items-center gap-2 mb-1">
                        {/* 起点图标标识（无数字序号） */}
                        <span className="shrink-0 w-5 h-5 rounded-full bg-gray-400 dark:bg-gray-500 text-white text-xs font-bold flex items-center justify-center">
                          ▶
                        </span>
                        <span className="font-semibold text-gray-700 dark:text-gray-200 text-xs">
                          {t('episodeFilterFlow.start.name')}
                        </span>
                      </div>
                      <div className="text-[11px] text-gray-500 dark:text-gray-400 mb-1 font-medium">
                        {t('episodeFilterFlow.start.where')}
                      </div>
                      <p className="text-[11px] text-gray-500/90 dark:text-gray-400/90 leading-snug">
                        {t('episodeFilterFlow.start.desc')}
                      </p>
                    </div>
                    {/* 起点 → ① 的箭头 */}
                    <div className="flex items-center justify-center text-blue-400 dark:text-blue-500 font-bold shrink-0">
                      <span className="hidden md:inline">→</span>
                      <span className="md:hidden">↓</span>
                    </div>
                    {[1, 2, 3].map((step, idx) => (
                      <Fragment key={step}>
                        <div className="flex-1 bg-white/70 dark:bg-gray-800/50 rounded-lg px-3 py-2.5 border border-blue-100/70 dark:border-blue-800/30">
                          <div className="flex items-center gap-2 mb-1">
                            {/* 序号圆圈 */}
                            <span className="shrink-0 w-5 h-5 rounded-full bg-blue-500 text-white text-xs font-bold flex items-center justify-center">
                              {step}
                            </span>
                            <span className="font-semibold text-blue-800 dark:text-blue-300 text-xs">
                              {t(`episodeFilterFlow.step${step}.name`)}
                            </span>
                          </div>
                          {/* 位置徽章 */}
                          <div className="text-[11px] text-blue-500 dark:text-blue-400 mb-1 font-medium">
                            {t(`episodeFilterFlow.step${step}.where`)}
                          </div>
                          <p className="text-[11px] text-blue-600/90 dark:text-blue-400/90 leading-snug">
                            {t(`episodeFilterFlow.step${step}.desc`)}
                          </p>
                        </div>
                        {/* 阶段间箭头：桌面端朝右、窄屏朝下 */}
                        {idx < 2 && (
                          <div className="flex items-center justify-center text-blue-400 dark:text-blue-500 font-bold shrink-0">
                            <span className="hidden md:inline">→</span>
                            <span className="md:hidden">↓</span>
                          </div>
                        )}
                      </Fragment>
                    ))}
                  </div>
                  <div className="text-blue-600 dark:text-blue-400 text-xs space-y-0.5">
                    <p>{t('episodeFilterFlow.note2')}</p>
                    <p>{t('episodeFilterFlow.note3')}</p>
                  </div>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">

                  {/* 卡片一：单源分集标题过滤配置（flex 列布局，让操作栏贴底，保证与右卡等高对齐） */}
                  <div className="rounded-lg border border-gray-200 dark:border-white/10 px-4 py-4 bg-gray-50/60 dark:bg-white/[0.03] space-y-4 flex flex-col">
                    <div>
                      <div className="text-sm font-semibold mb-1">{t('sourceEpisodeFilter.title')}</div>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('sourceEpisodeFilter.desc')}</p>
                    </div>
                    <Form layout="vertical" className="flex flex-col flex-1">
                      <Form.Item label={t('sourceEpisodeFilter.sourceLabel')} className="mb-4">
                        <Select
                          className="w-full"
                          showSearch
                          allowClear
                          value={selectedEpisodeSource || undefined}
                          // 排除 custom 虚拟上传源：它没有分集标题黑名单配置字段
                          options={providerOptions.filter(o => o.value !== 'custom')}
                          onChange={handleSelectEpisodeSource}
                          placeholder={t('sourceEpisodeFilter.sourcePlaceholder')}
                          notFoundContent={t('sourceEpisodeFilter.noSource')}
                          loading={isLoadingSourceEpisode}
                          optionFilterProp="label"
                        />
                      </Form.Item>
                      <Form.Item
                        label={
                          // 窄屏竖排（标签在上、按钮组在下且可换行），sm 起同行两端对齐，避免移动端标签被挤成竖排
                          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between w-full gap-1">
                            <span className="whitespace-nowrap">{t('sourceEpisodeFilter.regexLabel')}</span>
                            <div className="flex items-center flex-wrap gap-x-1 -ml-2 sm:ml-0">
                              <Button
                                type="link"
                                size="small"
                                disabled={!selectedEpisodeSource}
                                loading={isLoadingSourceEpisodeDefaults}
                                onClick={handleFillSourceEpisodeDefaults}
                              >
                                {t('sourceEpisodeFilter.fillDefault')}
                              </Button>
                              <Button
                                type="link"
                                size="small"
                                disabled={!selectedEpisodeSource}
                                loading={isLoadingSourceEpisodeCommon}
                                onClick={handleFillSourceEpisodeCommon}
                              >
                                {t('sourceEpisodeFilter.fillCommon')}
                              </Button>
                              {/* AI 生成：复用统一 AI 弹窗，写回单源正则文本域 */}
                              <Button
                                type="link"
                                size="small"
                                icon={<RobotOutlined />}
                                disabled={!selectedEpisodeSource}
                                onClick={() => openAiModal('source')}
                              >
                                {t('sourceEpisodeFilter.aiGenerate')}
                              </Button>
                            </div>
                          </div>
                        }
                        className="mb-4"
                      >
                        <Input.TextArea
                          rows={12}
                          value={sourceEpisodeRegex}
                          onChange={e => setSourceEpisodeRegex(e.target.value)}
                          placeholder={t('sourceEpisodeFilter.regexPlaceholder')}
                          disabled={!selectedEpisodeSource || isLoadingSourceEpisode}
                        />
                      </Form.Item>
                      <div className="flex justify-between gap-2 pt-4 border-t border-gray-100 dark:border-white/8 mt-auto">
                        <Button
                          disabled={!selectedEpisodeSource}
                          onClick={() => openRegexTestModal(
                            t('sourceEpisodeFilter.title'),
                            extractRegexParts(sourceEpisodeRegex, t('sourceEpisodeFilter.regexLabel'))
                          )}
                        >
                          {t('regexTester.title')}
                        </Button>
                        <Button
                          type="primary"
                          disabled={!selectedEpisodeSource}
                          loading={isSourceEpisodeSaveLoading}
                          onClick={handleSaveSourceEpisodeFilter}
                        >
                          {t('sourceEpisodeFilter.saveChanges')}
                        </Button>
                      </div>
                    </Form>
                  </div>

                  {/* 卡片二：兜底分集标题过滤（原内容，同样 flex 列布局与左卡对齐等高） */}
                  <div className="rounded-lg border border-gray-200 dark:border-white/10 px-4 py-4 bg-gray-50/60 dark:bg-white/[0.03] space-y-4 flex flex-col">
                    {/* 标题+开关单独一行，描述文字放下方占满整卡宽度，与左卡描述纵向对齐 */}
                    <div>
                      <div className="flex items-center justify-between gap-3 mb-1">
                        <div className="text-sm font-semibold">{t('globalEpisodeTitleFilter.title')}</div>
                        {/* 启用开关：与"弹幕搜索源"列表单源开关保持一致，通过 checkedChildren/unCheckedChildren 内嵌文案 */}
                        <Switch
                          className="shrink-0"
                          checked={episodeFilterEnabled}
                          checkedChildren={t('globalEpisodeTitleFilter.enabledText')}
                          unCheckedChildren={t('globalEpisodeTitleFilter.disabledText')}
                          onChange={setEpisodeFilterEnabled}
                        />
                      </div>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{t('globalEpisodeTitleFilter.desc')}</p>
                    </div>
                    <div className="text-xs text-gray-400 dark:text-gray-500 space-y-0.5">
                      <p>{t('globalEpisodeTitleFilter.hint1')}</p>
                      <p>{t('globalEpisodeTitleFilter.hint2')}</p>
                      <p>{t('globalEpisodeTitleFilter.hint3')}</p>
                      <p>{t('globalEpisodeTitleFilter.hint4')}</p>
                    </div>
                    <Form layout="vertical" className="flex flex-col flex-1">
                      <Form.Item
                        label={
                          // 窄屏竖排（标签在上、按钮组在下且可换行），sm 起同行两端对齐，避免移动端标签被挤成竖排
                          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between w-full gap-1">
                            <span className="whitespace-nowrap">{t('globalEpisodeTitleFilter.regexLabel')}</span>
                            <div className="flex items-center flex-wrap gap-x-1 -ml-2 sm:ml-0">
                              <Button type="link" size="small" loading={isLoadingEpisodeDefaults} onClick={handleFillEpisodeDefaults}>
                                {t('globalEpisodeTitleFilter.fillDefault')}
                              </Button>
                              {/* AI 生成：复用统一 AI 弹窗，写回兜底全局正则文本域 */}
                              <Button
                                type="link"
                                size="small"
                                icon={<RobotOutlined />}
                                onClick={() => openAiModal('episode')}
                              >
                                {t('globalEpisodeTitleFilter.aiGenerate')}
                              </Button>
                            </div>
                          </div>
                        }
                        className="mb-4"
                      >
                        <Input.TextArea
                          rows={12}
                          value={episodeFilterRegex}
                          onChange={e => setEpisodeFilterRegex(e.target.value)}
                          placeholder={t('globalEpisodeTitleFilter.regexPlaceholder')}
                          disabled={!episodeFilterEnabled}
                        />
                      </Form.Item>
                      <div className="flex justify-between gap-2 pt-4 border-t border-gray-100 dark:border-white/8 mt-auto">
                        <Button
                          disabled={!episodeFilterEnabled}
                          onClick={() => openRegexTestModal(
                            t('globalEpisodeTitleFilter.title'),
                            extractRegexParts(episodeFilterRegex, t('globalEpisodeTitleFilter.regexLabel'))
                          )}
                        >
                          {t('regexTester.title')}
                        </Button>
                        <Button type="primary" loading={isEpisodeFilterSaveLoading} onClick={handleSaveEpisodeFilter}>
                          {t('globalEpisodeTitleFilter.saveChanges')}
                        </Button>
                      </div>
                    </Form>
                  </div>

                </div>
              </div>
            ),
          },
          {
            key: 'single',
            label: t('singleEpisodeFilter.title'),
            children: loading ? null : (
              <div className="px-5 py-5 space-y-4" id="feat-single-filter">
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {t('singleEpisodeFilter.desc')}
                </p>

                {/* 快速填写面板：内嵌子卡片，与外层卡片形成层次 */}
                <div className="rounded-lg border border-gray-200 dark:border-white/10 px-4 py-3 bg-gray-50/60 dark:bg-white/[0.03]">
                  <div className="text-sm font-medium mb-3">{t('singleEpisodeFilter.quickTitle')}</div>
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-x-2 gap-y-3 md:gap-y-2 items-center">
                    <Select
                      className="md:col-span-4 w-full"
                      showSearch
                      allowClear
                      open={titleDropdownOpen}
                      onDropdownVisibleChange={setTitleDropdownOpen}
                      value={singleDraft.title || undefined}
                      filterOption={false}
                      onSearch={handleSearchLibrary}
                      onChange={value => {
                        if (!value) setSingleDraft(prev => ({ ...prev, title: '', provider: '', mediaId: '' }))
                      }}
                      placeholder={t('singleEpisodeFilter.titleKeywordPlaceholder')}
                      dropdownRender={renderAnimeDropdown}
                    />
                    <div className="md:col-span-3 flex w-full items-center gap-2">
                      <Input
                        className="flex-1 min-w-0"
                        value={singleDraft.rules}
                        onChange={e => setSingleDraft(prev => ({ ...prev, rules: e.target.value }))}
                        placeholder={t('singleEpisodeFilter.rulesPlaceholder')}
                      />
                      <Button
                        size="small"
                        className="shrink-0 !h-8 px-2"
                        onClick={() => openAiModal('single')}
                      >
                        {t('singleEpisodeFilter.aiRules')}
                      </Button>
                    </div>
                    <Select
                      className="md:col-span-2 w-full"
                      allowClear
                      value={sourceOptions.some(item => item.value === singleDraft.mediaId) ? singleDraft.mediaId : (singleDraft.provider || undefined)}
                      options={[{ label: t('singleEpisodeFilter.providerAll'), value: '' }, ...(sourceOptions.length ? sourceOptions : providerOptions)]}
                      onChange={(value, option) => handleSelectSource(value || '', option || {})}
                      placeholder={t('singleEpisodeFilter.providerPlaceholder')}
                    />
                    <Input
                      className="md:col-span-2 w-full"
                      value={singleDraft.mediaId}
                      onChange={e => setSingleDraft(prev => ({ ...prev, mediaId: e.target.value }))}
                      placeholder={t('singleEpisodeFilter.mediaIdPlaceholder')}
                    />
                    <Button type="primary" className="md:col-span-1 w-full !h-8" onClick={handleInsertSingleDraft}>{t('singleEpisodeFilter.insertRule')}</Button>
                  </div>
                </div>

                <Form layout="vertical">
                  <Form.Item
                    label={
                      <div className="flex items-center justify-between w-full">
                        <span>{t('singleEpisodeFilter.rawConfig')}</span>
                        <Button type="link" size="small" onClick={handleInsertDefaultFormat}>
                          {t('singleEpisodeFilter.insertDefaultFormat')}
                        </Button>
                      </div>
                    }
                    className="mb-5"
                  >
                    <Input.TextArea
                      rows={8}
                      value={singleFilterContent}
                      onChange={e => setSingleFilterContent(e.target.value)}
                      placeholder={t('singleEpisodeFilter.placeholder')}
                    />
                  </Form.Item>
                  <div className="flex justify-between gap-2 pt-4 border-t border-gray-100 dark:border-white/8">
                    <Button onClick={() => openRegexTestModal(t('singleEpisodeFilter.title'), extractSingleFilterPatterns())}>
                      {t('regexTester.title')}
                    </Button>
                    <Button type="primary" loading={isSingleSaveLoading} onClick={handleSaveSingleFilter}>
                      {t('singleEpisodeFilter.saveChanges')}
                    </Button>
                  </div>
                </Form>
              </div>
            ),
          },
        ]}
      />
    </Card>

      {/* AI 正则生成弹窗（单剧/单源复用）：描述 → 快捷示例 → 生成 → 预览对比 → 覆盖/追加 */}
      <Modal
        title={<span><RobotOutlined className="mr-2" />{t('aiRegexModal.title')}</span>}
        open={aiOpen}
        onCancel={() => setAiOpen(false)}
        width={560}
        footer={[
          <Button key="cancel" onClick={() => setAiOpen(false)}>
            {t('common.cancel')}
          </Button>,
          <Button
            key="generate"
            type={aiResult ? 'default' : 'primary'}
            icon={<RobotOutlined />}
            loading={aiLoading}
            onClick={handleGenerateRulesByAI}
          >
            {aiLoading ? t('aiRegexModal.generating') : t('aiRegexModal.generate')}
          </Button>,
          <Button
            key="append"
            disabled={!aiResult}
            onClick={() => handleApplyAiResult('append')}
          >
            {t('aiRegexModal.applyAppend')}
          </Button>,
          <Button
            key="overwrite"
            type="primary"
            disabled={!aiResult}
            onClick={() => handleApplyAiResult('overwrite')}
          >
            {t('aiRegexModal.applyOverwrite')}
          </Button>,
        ]}
      >
        <div className="space-y-3">
          <div>
            <div className="text-sm mb-1">{t('aiRegexModal.descLabel')}</div>
            <Input.TextArea
              rows={4}
              value={aiDesc}
              onChange={e => setAiDesc(e.target.value)}
              placeholder={t('aiRegexModal.descPlaceholder')}
            />
          </div>
          {/* 快捷示例标签：点击直接填入描述框，降低"不知道写什么"的门槛 */}
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1.5">{t('aiRegexModal.examplesLabel')}</div>
            <div className="flex flex-wrap gap-2">
              {['example1', 'example2', 'example3', 'example4'].map(key => (
                <span
                  key={key}
                  role="button"
                  tabIndex={0}
                  onClick={() => setAiDesc(t(`aiRegexModal.${key}`))}
                  className="cursor-pointer select-none px-2.5 py-1 rounded-full text-xs border border-blue-200 dark:border-blue-800/50 text-blue-600 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
                >
                  {t(`aiRegexModal.${key}`)}
                </span>
              ))}
            </div>
          </div>
          {/* 生成结果预览 + 与当前正则对比，应用前可见，避免误覆盖 */}
          {aiResult && (
            <div className="rounded-lg border border-gray-200 dark:border-white/10 p-3 bg-gray-50/60 dark:bg-white/[0.03] space-y-2">
              <div>
                <div className="text-xs text-gray-400 dark:text-gray-500 mb-1">{t('aiRegexModal.currentLabel')}</div>
                <pre className="text-xs whitespace-pre-wrap break-all font-mono text-gray-500 dark:text-gray-400 m-0">
                  {getAiCurrentRegex().trim() || t('aiRegexModal.empty')}
                </pre>
              </div>
              <div className="border-t border-gray-100 dark:border-white/8 pt-2">
                <div className="text-xs text-green-600 dark:text-green-400 mb-1">{t('aiRegexModal.resultLabel')}</div>
                <pre className="text-xs whitespace-pre-wrap break-all font-mono text-green-700 dark:text-green-300 m-0">
                  {aiResult}
                </pre>
              </div>
            </div>
          )}
        </div>
      </Modal>

      <Modal
        title={`${t('regexTester.title')} - ${regexTestTitle}`}
        open={regexTestOpen}
        onCancel={() => setRegexTestOpen(false)}
        onOk={handleRunRegexTest}
        confirmLoading={regexTestLoading}
        okText={t('regexTester.test')}
        cancelText={t('common.cancel')}
      >
        <div className="space-y-3">
          <Input.TextArea
            rows={3}
            value={regexTestText}
            onChange={e => setRegexTestText(e.target.value)}
            placeholder={t('regexTester.placeholder')}
          />
          <div className="text-xs text-gray-500 dark:text-gray-400">
            {t('regexTester.patternCount', { count: regexTestPatterns.length })}
          </div>
          {regexTestResult && (
            <div className="rounded-lg border border-gray-200 dark:border-white/10 p-3 text-xs space-y-2">
              {regexTestResult.matched ? (
                <div className="text-emerald-600 dark:text-emerald-400">
                  {t('regexTester.matched')}
                  <div className="mt-1 space-y-1">
                    {regexTestResult.matches.map((item, idx) => (
                      <div key={`${item.label}-${idx}`} className="font-mono break-all">
                        • {item.label}: {item.pattern} {item.matchedText ? `=> ${item.matchedText}` : ''}
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-gray-500 dark:text-gray-400">{t('regexTester.missed')}</div>
              )}
              {regexTestResult.invalids?.length > 0 && (
                <div className="text-red-500">
                  {t('regexTester.invalid')}
                  <div className="mt-1 space-y-1">
                    {regexTestResult.invalids.map((item, idx) => (
                      <div key={`${item.label}-invalid-${idx}`} className="font-mono break-all">
                        • {item.label}: {item.pattern} {item.error ? `(${item.error})` : ''}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </Modal>

    </>
  )
}

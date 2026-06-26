import { Button, Card, Form, Input, Modal, Select, Switch, Tabs, Tooltip } from 'antd'
import { useEffect, useState } from 'react'
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
  generateRegex,
  testRegexPatterns,
} from '../../../apis'
import { QuestionCircleOutlined } from '@ant-design/icons'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'


export const GlobalFilter = () => {
  const { t } = useTranslation()
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
  const [isSingleSaveLoading, setIsSingleSaveLoading] = useState(false)
  const [episodeFilterEnabled, setEpisodeFilterEnabled] = useState(false)
  const [isLoadingEpisodeDefaults, setIsLoadingEpisodeDefaults] = useState(false)
  const [episodeFilterRegex, setEpisodeFilterRegex] = useState('')
  const [isEpisodeFilterSaveLoading, setIsEpisodeFilterSaveLoading] = useState(false)
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

  const handleGenerateRulesByAI = async () => {
    if (!aiDesc.trim()) return
    try {
      setAiLoading(true)
      const res = await generateRegex(aiDesc, singleDraft.rules, 'episode_blacklist')
      if (res.data?.regex) {
        setSingleDraft(prev => ({ ...prev, rules: res.data.regex }))
        setAiOpen(false)
        setAiDesc('')
      } else {
        messageApi.warning(t('singleEpisodeFilter.aiNoResult'))
      }
    } catch (error) {
      messageApi.error(t('singleEpisodeFilter.aiFailed'))
    } finally {
      setAiLoading(false)
    }
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
    <div className="my-6" id="feat-global-filter">
      <Tabs defaultActiveKey="global" items={[
        {
          key: 'global',
          label: t('globalFilter.title'),
          children: loading ? null : (
            <>
              <div className="mb-4">
                <div className="text-sm mb-2 opacity-75">
                  <div className="bg-blue-50 dark:bg-blue-900/30 p-3 rounded mb-3">
                    <p className="font-semibold text-blue-800 dark:text-blue-300 mb-2">
                      {t('globalFilter.filterLevelTitle')}
                    </p>
                    <pre className="text-blue-700 dark:text-blue-400 text-xs mb-3 whitespace-pre-wrap font-mono bg-white/50 dark:bg-gray-800/50 p-2 rounded">
                      {t('globalFilter.filterLevelTree')}
                    </pre>
                    <p className="text-blue-600 dark:text-blue-400 text-xs">
                      {t('globalFilter.episodeFilterTip')}
                    </p>
                  </div>
                </div>
              </div>
              <Form form={form} layout="vertical" onFinish={handleSave} className="px-2 pb-4">
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
                      <Button type="link" size="small" loading={isLoadingDefaults.cn} onClick={() => handleFillDefault('cn')}>
                        {t('globalFilter.fillDefaultRules')}
                      </Button>
                    </div>
                  }
                  className="mb-6"
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
                      <Button type="link" size="small" loading={isLoadingDefaults.eng} onClick={() => handleFillDefault('eng')}>
                        {t('globalFilter.fillDefaultRules')}
                      </Button>
                    </div>
                  }
                  className="mb-6"
                >
                  <Input.TextArea rows={4} placeholder={t('globalFilter.enRulesPlaceholder')} />
                </Form.Item>
                <Form.Item>
                  <div className="flex justify-between gap-2">
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
                </Form.Item>
              </Form>
            </>
          ),
        },

        {
          key: 'episodeFilter',
          label: t('globalEpisodeTitleFilter.title'),
          children: loading ? null : (
            <div className="px-2 pb-4 space-y-4">
              <div className="text-sm text-gray-500 dark:text-gray-400">
                {t('globalEpisodeTitleFilter.desc')}
              </div>
              <div className="flex items-center gap-3">
                <Switch checked={episodeFilterEnabled} onChange={setEpisodeFilterEnabled} />
                <span className="text-sm">{t('globalEpisodeTitleFilter.enableLabel')}</span>
              </div>
              <div className="text-xs text-gray-400 dark:text-gray-500 space-y-0.5">
                <p>{t('globalEpisodeTitleFilter.hint1')}</p>
                <p>{t('globalEpisodeTitleFilter.hint2')}</p>
                <p>{t('globalEpisodeTitleFilter.hint3')}</p>
                <p>{t('globalEpisodeTitleFilter.hint4')}</p>
              </div>
              <Form layout="vertical" className="px-0">
                <Form.Item
                  label={
                    <div className="flex items-center justify-between w-full">
                      <span>{t('globalEpisodeTitleFilter.regexLabel')}</span>
                      <Button type="link" size="small" loading={isLoadingEpisodeDefaults} onClick={handleFillEpisodeDefaults}>
                        {t('globalEpisodeTitleFilter.fillDefault')}
                      </Button>
                    </div>
                  }
                  className="mb-6"
                >
                  <Input.TextArea
                    rows={12}
                    value={episodeFilterRegex}
                    onChange={e => setEpisodeFilterRegex(e.target.value)}
                    placeholder={t('globalEpisodeTitleFilter.regexPlaceholder')}
                    disabled={!episodeFilterEnabled}
                  />
                </Form.Item>
                <Form.Item>
                  <div className="flex justify-between gap-2">
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
                </Form.Item>
              </Form>
            </div>
          ),
        },
        {
          key: 'single',
          label: t('singleEpisodeFilter.title'),
          children: loading ? null : (
            <div className="px-2 pb-4 space-y-4">
              <div className="text-sm text-gray-500 dark:text-gray-400">
                {t('singleEpisodeFilter.desc')}
              </div>

          <div className="rounded-lg border border-gray-200 dark:border-white/10 px-3 py-2.5 bg-gray-50/70 dark:bg-white/[0.03]">
            <div className="text-sm font-medium mb-2.5">{t('singleEpisodeFilter.quickTitle')}</div>
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
                  onClick={() => setAiOpen(true)}
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

          <Form layout="vertical" className="px-0">
            <Form.Item
              label={
                <div className="flex items-center justify-between w-full">
                  <span>{t('singleEpisodeFilter.rawConfig')}</span>
                  <Button type="link" size="small" onClick={handleInsertDefaultFormat}>
                    {t('singleEpisodeFilter.insertDefaultFormat')}
                  </Button>
                </div>
              }
              className="mb-6"
            >
              <Input.TextArea
                rows={8}
                value={singleFilterContent}
                onChange={e => setSingleFilterContent(e.target.value)}
                placeholder={t('singleEpisodeFilter.placeholder')}
              />
            </Form.Item>
            <Form.Item>
              <div className="flex justify-between gap-2">
                <Button onClick={() => openRegexTestModal(t('singleEpisodeFilter.title'), extractSingleFilterPatterns())}>
                  {t('regexTester.title')}
                </Button>
                <Button type="primary" loading={isSingleSaveLoading} onClick={handleSaveSingleFilter}>
                  {t('singleEpisodeFilter.saveChanges')}
                </Button>
              </div>
            </Form.Item>
          </Form>
        </div>
          ),
        },
      ]} />

      <Modal
        title={t('singleEpisodeFilter.aiTitle')}
        open={aiOpen}
        onCancel={() => setAiOpen(false)}
        onOk={handleGenerateRulesByAI}
        confirmLoading={aiLoading}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
      >
        <Input.TextArea
          rows={5}
          value={aiDesc}
          onChange={e => setAiDesc(e.target.value)}
          placeholder={t('singleEpisodeFilter.aiPlaceholder')}
        />
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

    </div>
  )
}

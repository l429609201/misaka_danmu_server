import { Button, Card, Collapse, Input, InputNumber, Modal, Select, Space, Tag, Tooltip } from 'antd'
import { useEffect, useState } from 'react'
import { useMessage } from '../../../MessageContext'
import { getRecognition, setRecognition, testRecognition, generateRegex } from '../../../apis'
import { RobotOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

export const Recognition = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [isSaveLoading, setIsSaveLoading] = useState(false)
  const [isTestLoading, setIsTestLoading] = useState(false)
  const messageApi = useMessage()

  const [text, setText] = useState('')
  const [aiModalOpen, setAiModalOpen] = useState(false)
  const [aiDesc, setAiDesc] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState('')

  // 测试工具状态
  const [testTitle, setTestTitle] = useState('')
  const [testSeason, setTestSeason] = useState(1)
  const [testEpisode, setTestEpisode] = useState(1)
  const [testSource, setTestSource] = useState(null)
  const [testStage, setTestStage] = useState('all')
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    setLoading(true)
    getRecognition()
      .then(res => {
        setText(res.data?.content ?? '')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleSave = async () => {
    try {
      setIsSaveLoading(true)
      const response = await setRecognition({ content: text })
      if (response.data?.warnings && response.data.warnings.length > 0) {
        const warningMessages = response.data.warnings.join('\n')
        messageApi.warning(t('recognition.saveSuccessWithWarnings', { warnings: warningMessages }), 8)
      } else {
        messageApi.success(t('recognition.saveSuccess'))
      }
    } catch (error) {
      messageApi.error(t('recognition.saveFailed'))
    } finally {
      setIsSaveLoading(false)
    }
  }

  const handleAiGenerate = async () => {
    if (!aiDesc.trim()) {
      messageApi.warning(t('recognition.aiInputDescription'))
      return
    }
    setAiLoading(true)
    setAiResult('')
    try {
      const res = await generateRegex(aiDesc.trim(), text, 'recognition_rules')
      if (res.data?.regex) {
        setAiResult(res.data.regex)
      } else {
        messageApi.error(t('recognition.aiNoValidConfig'))
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || t('recognition.aiGenFailed'))
    } finally {
      setAiLoading(false)
    }
  }

  const handleApplyAiResult = () => {
    if (!aiResult) return
    setText(aiResult)
    setAiModalOpen(false)
    setAiDesc('')
    setAiResult('')
    messageApi.success(t('recognition.aiConfigApplied'))
  }

  const handleTest = async () => {
    if (!testTitle.trim()) {
      messageApi.warning(t('recognition.testInputTitle'))
      return
    }
    try {
      setIsTestLoading(true)
      const res = await testRecognition({
        title: testTitle,
        season: testSeason,
        episode: testEpisode,
        source: testSource || null,
        stage: testStage,
      })
      setTestResult(res.data)
    } catch (error) {
      messageApi.error(t('recognition.testFailed', { error: error.message || t('common.unknown') }))
    } finally {
      setIsTestLoading(false)
    }
  }

  return (
    <div className="my-6 space-y-4">
      <Card loading={loading} title={t('recognition.title')}>
        <Collapse
          ghost
          items={[{
            key: 'help',
            label: <span className="text-sm opacity-75"><strong>{t('recognition.helpLabel')}</strong></span>,
            children: (
              <div className="text-sm opacity-75 space-y-3">
                <div className="bg-blue-50 dark:bg-blue-950/30 p-3 rounded">
                  <p className="font-semibold text-blue-800 dark:text-blue-300 mb-2">🔍 搜索预处理（在搜索前执行）</p>
                  <ul className="list-disc list-inside space-y-1 text-blue-700 dark:text-blue-400">
                    <li><strong>屏蔽词：</strong> <code>BLOCK:预告</code> — 标题包含该词则跳过搜索</li>
                    <li><strong>简单替换：</strong> <code>奔跑吧 =&gt; 奔跑吧兄弟</code> — 搜索时替换标题</li>
                    <li><strong>集数偏移：</strong> <code>第 &lt;&gt; 话 &gt;&gt; EP-1</code> — 通过定位词提取集数并偏移</li>
                    <li><strong>复合格式：</strong> <code>某动画 =&gt; 正确名称 &amp;&amp; 第 &lt;&gt; 话 &gt;&gt; EP-1</code> — 同时替换标题和偏移集数</li>
                    <li><strong>季度预处理：</strong> <code>{'新说唱2025 => {<search_season=8>}'}</code> — 指定搜索时使用的季度</li>
                  </ul>
                </div>
                <div className="bg-green-50 dark:bg-green-950/30 p-3 rounded">
                  <p className="font-semibold text-green-800 dark:text-green-300 mb-2">🎯 入库后处理（匹配后执行）</p>
                  <ul className="list-disc list-inside space-y-1 text-green-700 dark:text-green-400">
                    <li><strong>季度偏移：</strong> <code>{'标题 => {[title=正确标题;season_offset=1>8]}'}</code> — 替换标题并映射季度</li>
                    <li><strong>源特定偏移：</strong> <code>{'标题 => {[source=tencent;title=正确标题;season_offset=9>13]}'}</code> — 限定特定源</li>
                    <li><strong>元数据替换：</strong> <code>{'错误标题 => {[tmdbid=12345;type=tv;s=1;e=1]}'}</code> — 直接指定TMDB/豆瓣ID</li>
                    <li><strong>部分集数偏移：</strong> <code>{'某动画(下) => {[ep_range=1-12;ep_offset=+12]}'}</code> — 只对范围内集数偏移</li>
                    <li><strong>限定源+集偏移：</strong> <code>{'某动画 => {[ep_range=1-24;ep_offset=-24;source=bilibili]}'}</code></li>
                  </ul>
                </div>
                <div className="bg-gray-50 dark:bg-gray-800/30 p-3 rounded">
                  <p className="font-semibold mb-1">📐 偏移格式参考</p>
                  <div className="space-y-1">
                    <p><strong>季度偏移：</strong> <code>1&gt;8</code>(映射) <code>1+7</code>(加法) <code>9-1</code>(减法) <code>*+4</code>(所有季+4) <code>*&gt;1</code>(所有季改为1)</p>
                    <p><strong>集偏移：</strong> <code>EP+1</code>(加1) <code>EP-1</code>(减1) <code>2*EP</code>(翻倍) <code>2*EP-1</code>(翻倍减1)</p>
                    <p><strong>部分集偏移范围：</strong> <code>ep_range=1-12</code>(第1~12集) <code>ep_range=13-*</code>(第13集起无上限)</p>
                  </div>
                </div>
                <p className="text-xs opacity-60">💡 <code>#</code> 开头为注释，空行会被忽略，注意 <code>=&gt;</code> <code>&lt;&gt;</code> <code>&gt;&gt;</code> <code>&amp;&amp;</code> 左右需要空格</p>
              </div>
            ),
          }]}
          className="mb-4"
        />
        <div className="flex justify-end mb-2">
          <Tooltip title={t('recognition.aiTooltip')}>
            <Button
              type="link"
              size="small"
              icon={<RobotOutlined />}
              onClick={() => setAiModalOpen(true)}
            >
              {t('recognition.aiGenerate')}
            </Button>
          </Tooltip>
        </div>
        <Input.TextArea
          rows={12}
          value={text}
          onChange={value => setText(value.target.value)}
          placeholder={`# ===== 搜索预处理规则 =====
BLOCK:预告
奔跑吧 => 奔跑吧兄弟

# ===== 入库后处理规则 =====
新说唱2025 => {[title=中国新说唱 第8季;season_offset=1>8]}`}
        />
        <div className="flex justify-end mt-4">
          <Button type="primary" onClick={handleSave} loading={isSaveLoading}>
            {t('recognition.saveChanges')}
          </Button>
        </div>
      </Card>

      <Card title={t('recognition.testCardTitle')} size="small">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-[200px]">
              <div className="text-xs text-gray-500 mb-1">{t('recognition.labelTitle')}</div>
              <Input
                value={testTitle}
                onChange={e => setTestTitle(e.target.value)}
                placeholder={t('recognition.labelTitle')}
                onPressEnter={handleTest}
              />
            </div>
            <div className="w-20">
              <div className="text-xs text-gray-500 mb-1">{t('recognition.labelSeason')}</div>
              <InputNumber value={testSeason} onChange={setTestSeason} min={1} className="w-full" />
            </div>
            <div className="w-20">
              <div className="text-xs text-gray-500 mb-1">{t('recognition.labelEpisode')}</div>
              <InputNumber value={testEpisode} onChange={setTestEpisode} min={1} className="w-full" />
            </div>
            <div className="w-32">
              <div className="text-xs text-gray-500 mb-1">{t('recognition.labelSource')}</div>
              <Input value={testSource} onChange={e => setTestSource(e.target.value)} placeholder={t('recognition.sourcePlaceholder')} />
            </div>
            <div className="w-36">
              <div className="text-xs text-gray-500 mb-1">{t('recognition.labelStage')}</div>
              <Select value={testStage} onChange={setTestStage} className="w-full" options={[
                { value: 'all', label: t('recognition.optionAll') },
                { value: 'preprocess', label: t('recognition.optionPreprocess') },
                { value: 'postprocess', label: t('recognition.optionPostprocess') },
              ]} />
            </div>
            <Button type="primary" onClick={handleTest} loading={isTestLoading}>{t('recognition.btnTest')}</Button>
          </div>

          {testResult && (
            <div className={`p-3 rounded border ${testResult.matched ? 'bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-800' : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700'}`}>
              <div className="flex items-center gap-2 mb-2">
                <Tag color={testResult.matched ? 'green' : 'default'}>
                  {testResult.matched ? t('recognition.testHit') : t('recognition.testMiss')}
                </Tag>
              </div>
              <div className="text-sm space-y-1">
                <div className="flex gap-2">
                  <span className="text-gray-500 w-12 shrink-0">{t('recognition.labelResultTitle')}</span>
                  <span>{testResult.originalTitle}</span>
                  {testResult.originalTitle !== testResult.processedTitle && (
                    <><span className="text-gray-400">→</span><span className="font-semibold text-green-600">{testResult.processedTitle}</span></>
                  )}
                </div>
                <div className="flex gap-2">
                  <span className="text-gray-500 w-12 shrink-0">{t('recognition.labelResultSeason')}</span>
                  <span>S{String(testResult.originalSeason ?? '?').padStart(2, '0')}E{String(testResult.originalEpisode ?? '?').padStart(2, '0')}</span>
                  {(testResult.originalSeason !== testResult.processedSeason || testResult.originalEpisode !== testResult.processedEpisode) && (
                    <><span className="text-gray-400">→</span><span className="font-semibold text-green-600">S{String(testResult.processedSeason ?? '?').padStart(2, '0')}E{String(testResult.processedEpisode ?? '?').padStart(2, '0')}</span></>
                  )}
                </div>
                {testResult.matchedRules?.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-600">
                    <div className="text-xs text-gray-500 mb-1">{t('recognition.hitRules')}</div>
                    {testResult.matchedRules.map((rule, i) => (
                      <div key={i} className="text-xs text-gray-600 dark:text-gray-400 pl-2">• {rule}</div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </Card>

      <Modal
        title={<><RobotOutlined /> {t('recognition.aiAssistantTitle')}</>}
        open={aiModalOpen}
        onCancel={() => { setAiModalOpen(false); setAiResult('') }}
        footer={null}
        destroyOnClose
        width={700}
      >
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-600 mb-2">
              {t('recognition.aiDesc')}
            </div>
            <Input.TextArea
              value={aiDesc}
              onChange={e => setAiDesc(e.target.value)}
              placeholder={'例如：\n• 新说唱2025 搜索时使用"中国新说唱 第8季"，季度偏移 1>8\n• 屏蔽包含"预告"的标题\n• 奔跑吧 替换为 奔跑吧兄弟'}
              rows={4}
              onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleAiGenerate() } }}
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={aiLoading}
              onClick={handleAiGenerate}
            >
              {t('recognition.generate')}
            </Button>
          </div>
          {aiResult && (
            <div>
              <div className="text-sm text-gray-600 mb-1">{text.trim() ? t('recognition.mergedResult') : t('recognition.generateResult')}</div>
              <div className="bg-gray-50 border rounded p-3 font-mono text-sm whitespace-pre-wrap" style={{ maxHeight: 300, overflow: 'auto' }}>
                {aiResult}
              </div>
              <div className="flex justify-end mt-3">
                <Space>
                  <Button onClick={() => setAiResult('')}>{t('recognition.clear')}</Button>
                  <Button type="primary" onClick={handleApplyAiResult}>
                    {t('recognition.applyConfig')}
                  </Button>
                </Space>
              </div>
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}

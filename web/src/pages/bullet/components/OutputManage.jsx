import { useEffect, useState } from 'react'
import { Button, Card, ColorPicker, InputNumber, Select, Segmented, Tag, Switch, Input, Tooltip, Modal, Space } from 'antd'
import { QuestionCircleOutlined, RobotOutlined } from '@ant-design/icons'
import {
  getDanmuOutputTotal,
  setDanmuOutputTotal,
  getDanmakuMergeOutputEnabled,
  setDanmakuMergeOutputEnabled,
  getDanmakuChConvert,
  setDanmakuChConvert,
  getDanmakuChConvertPriority,
  setDanmakuChConvertPriority,
  getDanmakuTopConvertTo,
  setDanmakuTopConvertTo,
  getDanmakuBottomConvertTo,
  setDanmakuBottomConvertTo,
  getDanmakuLikesOutputEnabled,
  setDanmakuLikesOutputEnabled,
  getDanmakuLikesStyle,
  setDanmakuLikesStyle,
  getDanmakuRandomColorMode,
  setDanmakuRandomColorMode,
  getDanmakuRandomColorPalette,
  setDanmakuRandomColorPalette,
  getDanmakuBlacklistEnabled,
  setDanmakuBlacklistEnabled,
  getDanmakuBlacklistPatterns,
  setDanmakuBlacklistPatterns,
  getDanmakuBlacklistDefaults,
  generateRegex,
} from '../../../apis'
import { useMessage } from '../../../MessageContext'
import { useTranslation } from 'react-i18next'

const { TextArea } = Input

const DEFAULT_COLOR_PALETTE = [
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ffffff',
  '#ff7f7f',
  '#ffa07a',
  '#fff68f',
  '#90ee90',
  '#7fffd4',
  '#87cefa',
  '#d8bfd8',
  '#ffb6c1',
]

const parsePaletteFromServer = (raw) => {
  if (!raw) return DEFAULT_COLOR_PALETTE
  let values = []
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) values = parsed
  } catch {
    values = String(raw)
      .split(',')
      .map(v => v.trim())
  }
  const toHex = (val) => {
    const num = parseInt(String(val).replace('#', ''), 10)
    if (Number.isNaN(num)) return null
    return `#${num.toString(16).padStart(6, '0')}`
  }
  const palette = values
    .map(toHex)
    .filter(Boolean)
  return palette.length > 0 ? palette : DEFAULT_COLOR_PALETTE
}

const paletteToServer = (palette) => {
  const toInt = (hex) => parseInt(hex.replace('#', ''), 16)
  const arr = palette.map(toInt)
  return JSON.stringify(arr)
}

export const OutputManage = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(false)
  const [limit, setLimit] = useState('-1')
  const [mergeEnabled, setMergeEnabled] = useState(false)
  const [saveLoading, setSaveLoading] = useState(false)
  const [colorMode, setColorMode] = useState('off')
  const [palette, setPalette] = useState(DEFAULT_COLOR_PALETTE)
  const [colorPickerValue, setColorPickerValue] = useState('#ffffff')
  const [colorSaveLoading, setColorSaveLoading] = useState(false)
  const [blacklistEnabled, setBlacklistEnabled] = useState(false)
  const [blacklistPatterns, setBlacklistPatterns] = useState('')
  const [blacklistSaveLoading, setBlacklistSaveLoading] = useState(false)
  const [aiRegexModalOpen, setAiRegexModalOpen] = useState(false)
  const [aiRegexDesc, setAiRegexDesc] = useState('')
  const [aiRegexLoading, setAiRegexLoading] = useState(false)
  const [aiRegexResult, setAiRegexResult] = useState('')
  const [chConvert, setChConvert] = useState('0')
  const [chConvertPriority, setChConvertPriority] = useState('player')
  const [topConvertTo, setTopConvertTo] = useState('none')
  const [bottomConvertTo, setBottomConvertTo] = useState('none')
  const [likesStyle, setLikesStyle] = useState('heart_white')

  const messageApi = useMessage()

  const getConfig = async () => {
    setLoading(true)
    try {
      const [limitRes, mergeEnabledRes, colorModeRes, colorPaletteRes, blacklistEnabledRes, blacklistPatternsRes, chConvertRes, chConvertPriorityRes, likesOutputRes, topConvertRes, bottomConvertRes] = await Promise.all([
        getDanmuOutputTotal(),
        getDanmakuMergeOutputEnabled(),
        getDanmakuRandomColorMode(),
        getDanmakuRandomColorPalette(),
        getDanmakuBlacklistEnabled(),
        getDanmakuBlacklistPatterns(),
        getDanmakuChConvert(),
        getDanmakuChConvertPriority(),
        getDanmakuLikesOutputEnabled(),
        getDanmakuTopConvertTo(),
        getDanmakuBottomConvertTo(),
      ])
      setLimit(limitRes.data?.value ?? '-1')
      setMergeEnabled(mergeEnabledRes.data?.value === 'true')
      setColorMode(colorModeRes.data?.value || 'off')
      setPalette(parsePaletteFromServer(colorPaletteRes.data?.value))
      setBlacklistEnabled(blacklistEnabledRes.data?.value === 'true')
      setBlacklistPatterns(blacklistPatternsRes.data?.value || '')
      setChConvert(chConvertRes.data?.value || '0')
      setChConvertPriority(chConvertPriorityRes.data?.value || 'player')
      setTopConvertTo(topConvertRes.data?.value || 'none')
      setBottomConvertTo(bottomConvertRes.data?.value || 'none')
      const rawStyle = await getDanmakuLikesStyle()
      // 兼容旧配置：danmakuLikesOutputEnabled=false 时映射为 off
      if (likesOutputRes.data?.value === 'false') {
        setLikesStyle('off')
      } else {
        setLikesStyle(rawStyle.data?.value || 'heart_white')
      }
    } catch (e) {
      messageApi.error(t('bullet.outputGetFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleSaveLimit = async () => {
    setSaveLoading(true)
    try {
      await Promise.all([
        setDanmuOutputTotal({ value: `${limit}` }),
        setDanmakuMergeOutputEnabled({ value: mergeEnabled ? 'true' : 'false' }),
        setDanmakuChConvert({ value: chConvert }),
        setDanmakuChConvertPriority({ value: chConvertPriority }),
        setDanmakuTopConvertTo({ value: topConvertTo }),
        setDanmakuBottomConvertTo({ value: bottomConvertTo }),
        setDanmakuLikesOutputEnabled({ value: likesStyle !== 'off' ? 'true' : 'false' }),
        setDanmakuLikesStyle({ value: likesStyle !== 'off' ? likesStyle : 'heart_white' }),
      ])
      messageApi.success(t('bullet.outputSaveSuccess'))
    } catch (e) {
      messageApi.error(t('bullet.saveFailed'))
    } finally {
      setSaveLoading(false)
    }
  }

  const handleSaveColor = async () => {
    setColorSaveLoading(true)
    try {
      await Promise.all([
        setDanmakuRandomColorMode({ value: colorMode }),
        setDanmakuRandomColorPalette({ value: paletteToServer(palette) }),
      ])
      messageApi.success(t('bullet.colorSaveSuccess'))
    } catch (e) {
      messageApi.error(t('bullet.colorSaveFailed'))
    } finally {
      setColorSaveLoading(false)
    }
  }

  const handleSaveBlacklist = async () => {
    setBlacklistSaveLoading(true)
    try {
      await Promise.all([
        setDanmakuBlacklistEnabled({ value: blacklistEnabled ? 'true' : 'false' }),
        setDanmakuBlacklistPatterns({ value: blacklistPatterns }),
      ])
      messageApi.success(t('bullet.blacklistSaveSuccess'))
    } catch (e) {
      messageApi.error(t('bullet.blacklistSaveFailed'))
    } finally {
      setBlacklistSaveLoading(false)
    }
  }

  const handleAiGenerate = async () => {
    if (!aiRegexDesc.trim()) {
      messageApi.warning(t('bullet.aiInputRequired'))
      return
    }
    setAiRegexLoading(true)
    setAiRegexResult('')
    try {
      const res = await generateRegex(aiRegexDesc.trim(), blacklistPatterns, 'danmaku_blacklist')
      if (res.data?.regex) {
        setAiRegexResult(res.data.regex)
      } else {
        messageApi.error(t('bullet.aiInvalidResult'))
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || t('bullet.aiGenerateFailed'))
    } finally {
      setAiRegexLoading(false)
    }
  }

  const handleApplyAiRegex = () => {
    if (!aiRegexResult) return
    setBlacklistPatterns(aiRegexResult)
    setAiRegexModalOpen(false)
    setAiRegexDesc('')
    setAiRegexResult('')
    messageApi.success(t('bullet.aiApplied'))
  }

  const addColorToPalette = (color) => {
    const hex = color.toLowerCase()
    if (palette.includes(hex)) {
      messageApi.info(t('bullet.colorExists'))
      return
    }
    setPalette(prev => [...prev, hex])
  }

  const removeColor = (color) => {
    setPalette(prev => prev.filter(c => c !== color))
  }

  const randomColor = () => {
    const rand = Math.floor(Math.random() * 16777216)
    return `#${rand.toString(16).padStart(6, '0')}`
  }

  useEffect(() => {
    getConfig()
  }, [])

  return (
    <div className="my-6" id="feat-bullet-output">
      <Card loading={loading} title={t('bullet.outputTitle')}>
        <div>{t('bullet.outputDesc')}</div>
        <div className="my-4">
          <div className="flex items-center justify-between gap-4 mb-2 flex-wrap">
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <span>{t('bullet.outputLimit')}</span>
                <InputNumber value={limit} onChange={v => setLimit(v)} />
              </div>
              <div className="flex items-center gap-2">
                <span>{t('bullet.outputMerge')}</span>
                <Switch
                  checked={mergeEnabled}
                  onChange={setMergeEnabled}
                />
                <Tooltip title={t('bullet.outputMergeTip')}>
                  <QuestionCircleOutlined className="text-gray-400 cursor-help" />
                </Tooltip>
              </div>
              <div className="flex items-center gap-2">
                <span>{t('bullet.outputLikes')}</span>
                <Select
                  value={likesStyle}
                  style={{ width: 175 }}
                  onChange={setLikesStyle}
                  options={[
                    { label: t('bullet.outputLikesOff'), value: 'off' },
                    { label: t('bullet.outputLikesHeartWhite'), value: 'heart_white' },
                    { label: t('bullet.outputLikesHeartRed'), value: 'heart_red' },
                    { label: t('bullet.outputLikesHeartOutline'), value: 'heart_outline' },
                    { label: t('bullet.outputLikesBracket'), value: 'like_bracket' },
                    { label: t('bullet.outputLikesText'), value: 'text' },
                    { label: t('bullet.outputLikesNumOnly'), value: 'num_only' },
                  ]}
                />
                <Tooltip title={t('bullet.outputLikesTip')}>
                  <QuestionCircleOutlined className="text-gray-400 cursor-help" />
                </Tooltip>
              </div>
            </div>
          </div>
          <div className="text-sm text-gray-600" style={{ whiteSpace: 'pre-line' }}>
            {t('bullet.outputLimitDesc')}
          </div>
        </div>
        <div className="my-4">
          <div className="flex items-center gap-4 mb-2 flex-wrap">
            <div className="flex items-center gap-2">
              <span>{t('bullet.outputConvert')}</span>
              <Select
                value={chConvert}
                style={{ width: 150 }}
                onChange={setChConvert}
                options={[
                  { label: t('bullet.outputConvertNone'), value: '0' },
                  { label: t('bullet.outputConvertSimplified'), value: '1' },
                  { label: t('bullet.outputConvertTraditional'), value: '2' },
                ]}
              />
            </div>
            <div className="flex items-center gap-2">
              <span>{t('bullet.outputPriority')}</span>
              <Segmented
                value={chConvertPriority}
                onChange={setChConvertPriority}
                options={[
                  { label: t('bullet.outputPriorityServer'), value: 'server' },
                  { label: t('bullet.outputPriorityPlayer'), value: 'player' },
                ]}
              />
              <Tooltip title={
                <div>
                  <div>{t('bullet.outputPriorityServerDesc')}</div>
                  <div>{t('bullet.outputPriorityPlayerDesc')}</div>
                </div>
              }>
                <QuestionCircleOutlined className="text-gray-400 cursor-help" />
              </Tooltip>
            </div>
          </div>
          <div className="text-sm text-gray-600">
            {t('bullet.outputConvertDesc')}
          </div>
          <div className="flex items-center gap-4 mt-4 flex-wrap">
            <div className="flex items-center gap-2">
              <span>{t('bullet.outputPosTop')}</span>
              <Segmented
                value={topConvertTo}
                onChange={setTopConvertTo}
                options={[
                  { label: t('bullet.outputPosNone'), value: 'none' },
                  { label: t('bullet.outputPosBottom'), value: 'bottom' },
                  { label: t('bullet.outputPosScroll'), value: 'scroll' },
                ]}
              />
            </div>
            <div className="flex items-center gap-2">
              <span>{t('bullet.outputPosBottomLabel')}</span>
              <Segmented
                value={bottomConvertTo}
                onChange={setBottomConvertTo}
                options={[
                  { label: t('bullet.outputPosNone'), value: 'none' },
                  { label: t('bullet.outputPosTopValue'), value: 'top' },
                  { label: t('bullet.outputPosScroll'), value: 'scroll' },
                ]}
              />
            </div>
            <Tooltip title={t('bullet.outputPosTip')}>
              <QuestionCircleOutlined className="text-gray-400 cursor-help" />
            </Tooltip>
          </div>
        </div>
        <div className="flex items-center justify-end gap-3">
          <Button
            type="primary"
            loading={saveLoading}
            onClick={handleSaveLimit}
          >
            {t('bullet.outputSave')}
          </Button>
        </div>
      </Card>

      <Card loading={loading} title={t('bullet.colorTitle')} className="mt-4">
        <div className="text-sm text-gray-600 mb-3">
          {t('bullet.colorDesc')}
        </div>
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <div className="flex items-center gap-2">
            <span>{t('bullet.colorMode')}</span>
            <Select
              value={colorMode}
              style={{ width: 220 }}
              onChange={setColorMode}
              options={[
                { label: t('bullet.colorModeOff'), value: 'off' },
                { label: t('bullet.colorModeWhiteToRandom'), value: 'white_to_random' },
                { label: t('bullet.colorModeAllRandom'), value: 'all_random' },
                { label: t('bullet.colorModeAllWhite'), value: 'all_white' },
                { label: t('bullet.colorModeHighlightOnly'), value: 'highlight_only' },
              ]}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 mb-3">
          <div className="flex items-center gap-3">
            <ColorPicker
              value={colorPickerValue}
              showText
              presets={[
                { label: t('bullet.colorDefaultPalette'), colors: DEFAULT_COLOR_PALETTE },
              ]}
              onChange={(_, hex) => setColorPickerValue(hex)}
            />
            <Button
              onClick={() => addColorToPalette(colorPickerValue)}
              disabled={!colorPickerValue}
            >
              {t('bullet.colorAddToPalette')}
            </Button>
            <Button
              onClick={() => {
                const next = randomColor()
                setColorPickerValue(next)
                addColorToPalette(next)
              }}
            >
              {t('bullet.colorRandomOne')}
            </Button>
          </div>
        </div>

        <div className="mb-3">
          <div className="mb-2 text-sm text-gray-700">{t('bullet.colorCurrentSequence')}</div>
          <div className="flex flex-wrap gap-2">
            {palette.map(color => (
              <Tag
                key={color}
                closable
                onClose={() => removeColor(color)}
                style={{
                  backgroundColor: color,
                  borderColor: '#ccc',
                  color: '#000',
                  minWidth: 72,
                  textAlign: 'center',
                }}
              >
                {color}
              </Tag>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          <Button
            type="primary"
            loading={colorSaveLoading}
            onClick={handleSaveColor}
          >
            {t('bullet.colorSave')}
          </Button>
        </div>
      </Card>

      <Card loading={loading} title={t('bullet.blacklistTitle')} className="mt-4">
        <div className="text-sm text-gray-600 mb-4">
          {t('bullet.blacklistDesc')}
        </div>

        <div className="mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span>{t('bullet.blacklistEnable')}</span>
            <Switch
              checked={blacklistEnabled}
              onChange={setBlacklistEnabled}
            />
          </div>

          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-gray-700">{t('bullet.blacklistRules')}</span>
            <Space size="small">
              <Tooltip title={t('bullet.blacklistFillDefaultTip')}>
                <Button
                  type="link"
                  size="small"
                  disabled={!blacklistEnabled}
                  onClick={async () => {
                    const fillDefaults = async () => {
                      try {
                        const res = await getDanmakuBlacklistDefaults()
                        const patterns = res.data?.patterns || ''
                        if (!patterns) {
                          messageApi.warning(t('bullet.blacklistNoDefault'))
                          return
                        }
                        setBlacklistPatterns(patterns)
                        messageApi.success(t('bullet.blacklistFillSuccess'))
                      } catch (e) {
                        messageApi.error(t('bullet.blacklistFillFailed'))
                      }
                    }
                    if (blacklistPatterns.trim()) {
                      Modal.confirm({
                        title: t('bullet.blacklistFillTitle'),
                        content: t('bullet.blacklistFillContent'),
                        okText: t('bullet.blacklistFillOk'),
                        cancelText: t('common.cancel'),
                        onOk: fillDefaults,
                      })
                    } else {
                      await fillDefaults()
                    }
                  }}
                >
                  {t('bullet.blacklistFillDefault')}
                </Button>
              </Tooltip>
              <Tooltip title={t('bullet.blacklistAiTip')}>
                <Button
                  type="link"
                  size="small"
                  icon={<RobotOutlined />}
                  disabled={!blacklistEnabled}
                  onClick={() => setAiRegexModalOpen(true)}
                >
                  {t('bullet.blacklistAiGenerate')}
                </Button>
              </Tooltip>
            </Space>
          </div>
          <TextArea
            value={blacklistPatterns}
            onChange={e => setBlacklistPatterns(e.target.value)}
            placeholder={t('bullet.blacklistPlaceholder')}
            rows={6}
            disabled={!blacklistEnabled}
            style={{ fontFamily: 'monospace', fontSize: '12px' }}
          />

          <div className="mt-2 text-xs text-gray-500">
            <div>{t('bullet.blacklistHint1')}</div>
            <div>{t('bullet.blacklistHint2')}</div>
            <div>{t('bullet.blacklistHint3')}</div>
            <div>{t('bullet.blacklistHint4')}<code className="bg-gray-100 px-1">广告|推广|666</code></div>
            <div>{t('bullet.blacklistHint5')}</div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          <Button
            type="primary"
            loading={blacklistSaveLoading}
            onClick={handleSaveBlacklist}
          >
            {t('bullet.blacklistSave')}
          </Button>
        </div>
      </Card>

      <Modal
        title={<><RobotOutlined /> {t('bullet.aiTitle')}</>}
        open={aiRegexModalOpen}
        onCancel={() => { setAiRegexModalOpen(false); setAiRegexResult('') }}
        footer={null}
        destroyOnClose
      >
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-600 mb-2">
              {t('bullet.aiDesc')}
            </div>
            <TextArea
              value={aiRegexDesc}
              onChange={e => setAiRegexDesc(e.target.value)}
              placeholder={t('bullet.aiPlaceholder')}
              rows={3}
              onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleAiGenerate() } }}
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="primary"
              icon={<RobotOutlined />}
              loading={aiRegexLoading}
              onClick={handleAiGenerate}
            >
              {t('bullet.aiGenerate')}
            </Button>
          </div>
          {aiRegexResult && (
            <div>
              <div className="text-sm text-gray-600 mb-1">{blacklistPatterns.trim() ? t('bullet.aiMergedResult') : t('bullet.aiResult')}</div>
              <div className="bg-gray-50 border rounded p-3 font-mono text-sm break-all" style={{ maxHeight: 200, overflow: 'auto' }}>
                {aiRegexResult}
              </div>
              <div className="flex justify-end mt-3">
                <Space>
                  <Button onClick={() => setAiRegexResult('')}>{t('bullet.aiClear')}</Button>
                  <Button type="primary" onClick={handleApplyAiRegex}>
                    {t('bullet.aiApply')}
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

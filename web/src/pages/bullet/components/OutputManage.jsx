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
  generateRegex,
} from '../../../apis'
import { useMessage } from '../../../MessageContext'

const { TextArea } = Input

// 默认弹幕黑名单规则（参考 hills TG群群友分享过滤规则）
const DEFAULT_BLACKLIST_PATTERNS = `2333|666|哈哈哈|牛逼|前排|抢前排|第[0-9一二三四五六七八九十百千]+排|空降|到此一游|打卡|报道|报到|学[jJvVaA]+|后台播放|生日快乐|现在.+点|几点了|^\\d+小时|^\\d+分钟|^\\d+秒|^\\d{4}年|^\\d+月\\d+日|纯享版|三连|一键三连|恰饭|币没了|热乎的|^\\d+分钟前|白嫖|奥利给|寄了|蚌埠住了|蚌住|绷不住|笑死|草|泪目|哭了|泪奔|我哭了|弹幕护体|高考加油|上岸|保佑|还愿|活该|大快人心|报应|吓得我|一个巴掌拍不响|苍蝇不叮无缝的蛋|可怜之人必有可恨之处|^从.{0,8}来的|广东人|四川人|东北人|山东人|河南人|江苏人|浙江人|上海人|北京人|我老婆|我老公|我儿子|我女儿|我妈|爸|弟|姐|szd|真香|真恶心|太丑了|太美了|抱走|承包|舔屏|鼻血|已存|壁纸|手机壁纸|桌面|高清|无码|开车|手动狗头|手动滑稽|doge|妙啊|寄寄+|111+|222+|333+|444+|555+|777+|888+|999+|000+|(.){6,}|^.{0,9}\\(|^[一-龥\\w]{0,10} \\)|^[^一-龥]{8,}\\(|[·・]?(■|▂|▃|▄|▅|▆|▇|█){3,}[·・]?|^[一-龥]{5}[，,][一-龥]{7}[，,][一-龥]{5} \\)|见.{0,6}滚|滚.{0,6}见|智障|弱智|脑残|垃圾|辣鸡|恶心|死全家|去死妈|死爹|去死|傻逼|傻B|SB|sb|S ?b|cnm|你妈|NMSL|nm+l|tmd|他妈|操|艹|曹|叉|尼玛|泥马|日你|日死|去死吧|傻吊|阳痿|早泄|卖鲍|约炮|赌博|菠菜|开盘|杀猪盘|三狗|pg|AG|DG|OB|MG|BBIN|PT|EA|JDB|已三连|已投币|已充电|已关注|已收藏|已点赞|已打赏|已上舰|已续舰|提督|总督|舰长|大会员|年度大会员|小心心|辣条|打call|冲鸭|yyds|YYDS|绝绝子|神作|神番|封神|名场面|修罗场|真香警告|社死|翻车|贴贴|抱抱|亲亲|我爱你|娶我|嫁我|已婚|已离婚|已出轨|已出柜|已弯|已直|已黑化|已净化|已成佛|已飞升|已圆寂|已投胎|已退网|已退圈|已取关|已拉黑|已举报|已切割|已脱粉|已回踩|已反黑|已洗白|世界尽头|冷酷异变|生崽|生猴子|生一窝|小奶猫|小奶狗|小奶狐|小奶狼|小奶龙|舔狗|舔狼|上头了|太上头|眼睛怀孕|耳朵怀孕|妊娠纹|打桩机|大力出奇迹|黑化强三倍|洗白弱三倍|寄中寄|寄里寄气|玉玉了|已紫砂|我裂开了|xswl|awsl|AWSL|好甜|好刀|锁死|嗑疯了|嗑到脑溢血|嗑拉了|嗑吐了|cp粉狂喜|大型发糖|大型撒狗粮|大型虐狗|大型修罗场|大型翻车现场|大型社死现场|大型真香现场|大型纪录片|太顶了|太硬了|太粗了|太长了|太快了|太刺激了|爽飞了|高潮了|喷了|射了|已升天|手动@所有人|我来晚了|我先润了|我先溜了|我先寄了|88|886|拜拜|202[5-9]|2030|新年快乐|跨年快乐|龙年大吉|恭喜发财|暴富|脱单`

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
  const [likesStyle, setLikesStyle] = useState('heart_white')

  const messageApi = useMessage()

  const getConfig = async () => {
    setLoading(true)
    try {
      const [limitRes, mergeEnabledRes, colorModeRes, colorPaletteRes, blacklistEnabledRes, blacklistPatternsRes, chConvertRes, chConvertPriorityRes, likesOutputRes] = await Promise.all([
        getDanmuOutputTotal(),
        getDanmakuMergeOutputEnabled(),
        getDanmakuRandomColorMode(),
        getDanmakuRandomColorPalette(),
        getDanmakuBlacklistEnabled(),
        getDanmakuBlacklistPatterns(),
        getDanmakuChConvert(),
        getDanmakuChConvertPriority(),
        getDanmakuLikesOutputEnabled(),
      ])
      setLimit(limitRes.data?.value ?? '-1')
      setMergeEnabled(mergeEnabledRes.data?.value === 'true')
      setColorMode(colorModeRes.data?.value || 'off')
      setPalette(parsePaletteFromServer(colorPaletteRes.data?.value))
      setBlacklistEnabled(blacklistEnabledRes.data?.value === 'true')
      setBlacklistPatterns(blacklistPatternsRes.data?.value || '')
      setChConvert(chConvertRes.data?.value || '0')
      setChConvertPriority(chConvertPriorityRes.data?.value || 'player')
      const rawStyle = await getDanmakuLikesStyle()
      // 兼容旧配置：danmakuLikesOutputEnabled=false 时映射为 off
      if (likesOutputRes.data?.value === 'false') {
        setLikesStyle('off')
      } else {
        setLikesStyle(rawStyle.data?.value || 'heart_white')
      }
    } catch (e) {
      console.log(e)
      messageApi.error('获取配置失败')
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
        setDanmakuLikesOutputEnabled({ value: likesStyle !== 'off' ? 'true' : 'false' }),
        setDanmakuLikesStyle({ value: likesStyle !== 'off' ? likesStyle : 'heart_white' }),
      ])
      messageApi.success('弹幕输出配置已保存')
    } catch (e) {
      messageApi.error('保存失败')
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
      messageApi.success('随机颜色配置已保存')
    } catch (e) {
      messageApi.error('保存随机颜色配置失败')
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
      messageApi.success('弹幕黑名单配置已保存')
    } catch (e) {
      messageApi.error('保存弹幕黑名单配置失败')
    } finally {
      setBlacklistSaveLoading(false)
    }
  }

  const handleAiGenerate = async () => {
    if (!aiRegexDesc.trim()) {
      messageApi.warning('请输入描述')
      return
    }
    setAiRegexLoading(true)
    setAiRegexResult('')
    try {
      const res = await generateRegex(aiRegexDesc.trim(), blacklistPatterns, 'danmaku_blacklist')
      if (res.data?.regex) {
        setAiRegexResult(res.data.regex)
      } else {
        messageApi.error('AI 未能生成有效的正则表达式')
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || 'AI 正则生成失败')
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
    messageApi.success('已应用 AI 生成的规则')
  }

  const addColorToPalette = (color) => {
    const hex = color.toLowerCase()
    if (palette.includes(hex)) {
      messageApi.info('该颜色已存在')
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
    <div className="my-6">
      <Card loading={loading} title="弹幕输出配置">
        <div>在这里调整弹幕 API 的输出行为。</div>
        <div className="my-4">
          <div className="flex items-center justify-between gap-4 mb-2 flex-wrap">
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-2">
                <span>弹幕输出上限</span>
                <InputNumber value={limit} onChange={v => setLimit(v)} />
              </div>
              <div className="flex items-center gap-2">
                <span>合并输出</span>
                <Switch
                  checked={mergeEnabled}
                  onChange={setMergeEnabled}
                />
                <Tooltip title="启用后，将所有源的弹幕合并后再进行均衡采样输出，而不是每个源单独采样">
                  <QuestionCircleOutlined className="text-gray-400 cursor-help" />
                </Tooltip>
              </div>
              <div className="flex items-center gap-2">
                <span>输出点赞状态</span>
                <Select
                  value={likesStyle}
                  style={{ width: 175 }}
                  onChange={setLikesStyle}
                  options={[
                    { label: '🚫 关闭', value: 'off' },
                    { label: '🤍/🔥 默认', value: 'heart_white' },
                    { label: '❤️/🔥 红心', value: 'heart_red' },
                    { label: '♡/🔥 空心', value: 'heart_outline' },
                    { label: '[👍]/[🔥] 方括号', value: 'like_bracket' },
                    { label: '(点赞)/(热门) 文字', value: 'text' },
                    { label: '+数字 纯数字', value: 'num_only' },
                  ]}
                />
                <Tooltip title="选择点赞数的显示样式，达到热度阈值时显示🔥。选择「关闭」则不显示点赞信息">
                  <QuestionCircleOutlined className="text-gray-400 cursor-help" />
                </Tooltip>
              </div>
            </div>
          </div>
          <div className="text-sm text-gray-600">
            设置弹幕 API 返回的最大数量。-1 表示无限制。为防止客户端卡顿，建议设置 1000-5000。
            当弹幕总数超过限制时，系统按时间段均匀采样，确保弹幕在视频时长中分布均匀。
          </div>
        </div>
        <div className="my-4">
          <div className="flex items-center gap-4 mb-2 flex-wrap">
            <div className="flex items-center gap-2">
              <span>简繁转换</span>
              <Select
                value={chConvert}
                style={{ width: 150 }}
                onChange={setChConvert}
                options={[
                  { label: '不转换', value: '0' },
                  { label: '转换为简体', value: '1' },
                  { label: '转换为繁体', value: '2' },
                ]}
              />
            </div>
            <div className="flex items-center gap-2">
              <span>优先级</span>
              <Segmented
                value={chConvertPriority}
                onChange={setChConvertPriority}
                options={[
                  { label: '服务端优先', value: 'server' },
                  { label: '播放器优先', value: 'player' },
                ]}
              />
              <Tooltip title={
                <div>
                  <div><b>服务端优先</b>：始终使用此处配置，忽略播放器传入的参数</div>
                  <div><b>播放器优先</b>：播放器明确指定转换模式时覆盖此处配置；未指定时使用此处配置作为默认值</div>
                </div>
              }>
                <QuestionCircleOutlined className="text-gray-400 cursor-help" />
              </Tooltip>
            </div>
          </div>
          <div className="text-sm text-gray-600">
            控制弹幕输出时的简繁体转换行为。大多数播放器默认不指定转换模式，此时服务端配置生效。
          </div>
        </div>
        <div className="flex items-center justify-end gap-3">
          <Button
            type="primary"
            loading={saveLoading}
            onClick={handleSaveLimit}
          >
            保存输出配置
          </Button>
        </div>
      </Card>

      <Card loading={loading} title="随机弹幕颜色" className="mt-4">
        <div className="text-sm text-gray-600 mb-3">
          可配置随机色板和生效模式。默认不改色。
        </div>
        <div className="flex flex-wrap items-center gap-4 mb-4">
          <div className="flex items-center gap-2">
            <span>模式</span>
            <Select
              value={colorMode}
              style={{ width: 220 }}
              onChange={setColorMode}
              options={[
                { label: '不使用', value: 'off' },
                { label: '白色弹幕变随机颜色', value: 'white_to_random' },
                { label: '全部随机上色', value: 'all_random' },
                { label: '全部变白色', value: 'all_white' },
                { label: '仅上色点赞/重复弹幕', value: 'highlight_only' },
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
                { label: '默认色板', colors: DEFAULT_COLOR_PALETTE },
              ]}
              onChange={(_, hex) => setColorPickerValue(hex)}
            />
            <Button
              onClick={() => addColorToPalette(colorPickerValue)}
              disabled={!colorPickerValue}
            >
              添加到色板
            </Button>
            <Button
              onClick={() => {
                const next = randomColor()
                setColorPickerValue(next)
                addColorToPalette(next)
              }}
            >
              随机一个颜色
            </Button>
          </div>
        </div>

        <div className="mb-3">
          <div className="mb-2 text-sm text-gray-700">当前随机颜色序列</div>
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
            保存随机颜色
          </Button>
        </div>
      </Card>

      <Card loading={loading} title="弹幕输出黑名单" className="mt-4">
        <div className="text-sm text-gray-600 mb-4">
          使用正则表达式过滤弹幕内容。启用后，匹配黑名单规则的弹幕将被拦截，不会输出到客户端。
        </div>

        <div className="mb-4">
          <div className="flex items-center gap-2 mb-3">
            <span>启用黑名单过滤</span>
            <Switch
              checked={blacklistEnabled}
              onChange={setBlacklistEnabled}
            />
          </div>

          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-gray-700">黑名单规则（正则表达式）</span>
            <Space size="small">
              <Tooltip title="填充推荐的默认过滤规则（会覆盖当前内容）">
                <Button
                  type="link"
                  size="small"
                  disabled={!blacklistEnabled}
                  onClick={() => {
                    if (blacklistPatterns.trim()) {
                      Modal.confirm({
                        title: '填充默认配置',
                        content: '当前已有规则内容，填充默认配置将覆盖现有内容。是否继续？',
                        okText: '覆盖',
                        cancelText: '取消',
                        onOk: () => setBlacklistPatterns(DEFAULT_BLACKLIST_PATTERNS),
                      })
                    } else {
                      setBlacklistPatterns(DEFAULT_BLACKLIST_PATTERNS)
                      messageApi.success('已填充默认黑名单规则')
                    }
                  }}
                >
                  填充默认配置
                </Button>
              </Tooltip>
              <Tooltip title="使用 AI 根据自然语言描述生成正则表达式">
                <Button
                  type="link"
                  size="small"
                  icon={<RobotOutlined />}
                  disabled={!blacklistEnabled}
                  onClick={() => setAiRegexModalOpen(true)}
                >
                  AI 生成
                </Button>
              </Tooltip>
            </Space>
          </div>
          <TextArea
            value={blacklistPatterns}
            onChange={e => setBlacklistPatterns(e.target.value)}
            placeholder="支持两种格式：&#10;1. 单行格式：用 | 分隔多个规则，如：广告|推广|666&#10;2. 多行格式：每行一个正则表达式"
            rows={6}
            disabled={!blacklistEnabled}
            style={{ fontFamily: 'monospace', fontSize: '12px' }}
          />

          <div className="mt-2 text-xs text-gray-500">
            <div>• 默认过滤规则参考hills TG群群友分享过滤规则</div>
            <div>• 支持单行格式（用 | 分隔）或多行格式（每行一个规则）</div>
            <div>• 不区分大小写，自动匹配弹幕内容</div>
            <div>• 示例（单行）：<code className="bg-gray-100 px-1">广告|推广|666</code></div>
            <div>• 示例（多行）：每行写一个规则，# 开头的行为注释</div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3">
          <Button
            type="primary"
            loading={blacklistSaveLoading}
            onClick={handleSaveBlacklist}
          >
            保存黑名单配置
          </Button>
        </div>
      </Card>

      <Modal
        title={<><RobotOutlined /> AI 正则生成助手</>}
        open={aiRegexModalOpen}
        onCancel={() => { setAiRegexModalOpen(false); setAiRegexResult('') }}
        footer={null}
        destroyOnClose
      >
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-600 mb-2">
              用自然语言描述你想过滤的内容，AI 会帮你生成对应的正则表达式。
            </div>
            <TextArea
              value={aiRegexDesc}
              onChange={e => setAiRegexDesc(e.target.value)}
              placeholder="例如：过滤掉包含 抽奖、红包、转发抽奖 的弹幕"
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
              生成
            </Button>
          </div>
          {aiRegexResult && (
            <div>
              <div className="text-sm text-gray-600 mb-1">{blacklistPatterns.trim() ? '合并后的完整规则：' : '生成结果：'}</div>
              <div className="bg-gray-50 border rounded p-3 font-mono text-sm break-all" style={{ maxHeight: 200, overflow: 'auto' }}>
                {aiRegexResult}
              </div>
              <div className="flex justify-end mt-3">
                <Space>
                  <Button onClick={() => setAiRegexResult('')}>清除</Button>
                  <Button type="primary" onClick={handleApplyAiRegex}>
                    应用规则
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

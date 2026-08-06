import { useEffect, useState } from 'react'
import { CheckOutlined, PictureOutlined } from '@ant-design/icons'
import { Input } from 'antd'
import { useTranslation } from 'react-i18next'
import { useThemeMode, PAGE_STYLES, WALLPAPER_STYLE_KEYS } from '../ThemeProvider'
import { ResponsiveModal } from './ResponsiveModal'

// 每种风格的缩略图预览样式
const PREVIEW_STYLES = {
  normal: {
    wrap: {
      background: 'linear-gradient(135deg, #fff 0%, #f5f5f5 100%)',
      border: '1px solid #e5e7eb',
    },
    card: {
      background: '#ffffff',
      border: '1px solid #e5e7eb',
      boxShadow: '0 2px 4px rgba(0,0,0,0.04)',
    },
  },
  'liquid-glass': {
    wrap: {
      background: 'linear-gradient(135deg, rgba(255,107,155,0.22) 0%, rgba(64,150,255,0.22) 100%)',
      backdropFilter: 'blur(10px)',
      border: '1px solid rgba(255,255,255,0.6)',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.6), 0 4px 16px rgba(0,0,0,0.08)',
    },
    card: {
      background: 'rgba(255,255,255,0.45)',
      backdropFilter: 'blur(8px)',
      border: '1px solid rgba(255,255,255,0.6)',
      borderRadius: 10,
    },
  },
  'wallpaper-acg': {
    wrap: {
      background: 'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 50%, #74b9ff 100%)',
      border: '1px solid rgba(255,255,255,0.5)',
    },
    card: {
      background: 'rgba(255,255,255,0.55)',
      backdropFilter: 'blur(8px)',
      border: '1px solid rgba(255,255,255,0.7)',
      borderRadius: 10,
    },
    badge: { emoji: '🌸', label: '壁纸' },
  },

  sakura: {
    wrap: {
      background: 'linear-gradient(135deg, #fff0f5 0%, #ffe4f0 50%, #fff0fa 100%)',
      border: '1px solid #ffb8d4',
    },
    card: {
      background: 'rgba(255,255,255,0.85)',
      border: '1px solid #ffc8dd',
      borderRadius: 999,
      boxShadow: '0 2px 8px rgba(255,107,155,0.15)',
    },
    badge: { emoji: '🌸', label: '桜' },
  },
  glass: {
    wrap: {
      background:
        'radial-gradient(ellipse at 25% 25%, rgba(100,200,255,0.5) 0%, transparent 55%), radial-gradient(ellipse at 75% 75%, rgba(180,110,255,0.42) 0%, transparent 55%), linear-gradient(155deg, #c6edf8 0%, #d5c0f0 55%, #b6e8f2 100%)',
      border: '1px solid rgba(255,255,255,0.6)',
    },
    card: {
      background: 'rgba(255,255,255,0.5)',
      backdropFilter: 'blur(8px)',
      border: '1px solid rgba(255,255,255,0.65)',
      borderRadius: 10,
    },
    badge: { emoji: '☁️', label: '极光' },
  },
  paper: {
    wrap: {
      background:
        'repeating-linear-gradient(transparent, transparent 8px, rgba(0,0,100,0.07) 9px), #f8f4ec',
      border: '1px solid rgba(0,0,0,0.22)',
    },
    card: { background: '#fffef8', border: '1px solid rgba(0,0,0,0.22)', borderRadius: 0 },
  },
  calendar: {
    wrap: {
      background:
        'repeating-linear-gradient(90deg, transparent, transparent 16px, rgba(0,0,0,0.07) 16px, rgba(0,0,0,0.07) 17px), repeating-linear-gradient(0deg, transparent, transparent 16px, rgba(0,0,0,0.07) 16px, rgba(0,0,0,0.07) 17px), #fefcfa',
      border: '1px solid rgba(0,0,0,0.14)',
    },
    card: { background: '#ffffff', border: '1px solid rgba(0,0,0,0.16)', borderRadius: 2 },
  },
  github: {
    wrap: {
      background: '#f6f8fa',
      backgroundImage:
        'repeating-linear-gradient(90deg, rgba(0,0,0,0.045) 0, rgba(0,0,0,0.045) 1px, transparent 1px, transparent 13px), repeating-linear-gradient(0deg, rgba(0,0,0,0.045) 0, rgba(0,0,0,0.045) 1px, transparent 1px, transparent 13px)',
      border: '1px solid #d0d7de',
    },
    card: { background: '#fff', border: '1px solid #d0d7de', borderRadius: 6 },
  },
  material: {
    wrap: {
      background: 'linear-gradient(135deg, #f3eeff 0%, #e8def8 100%)',
      border: '1px solid #d7cce8',
    },
    card: {
      background: '#fff',
      borderRadius: 16,
      border: 'none',
      boxShadow: '0 3px 10px rgba(103,58,183,0.16)',
    },
    badge: { emoji: '✨', label: 'M3' },
  },
  'acg-glass': {
    wrap: {
      background:
        'linear-gradient(135deg, rgba(120,80,220,0.42) 0%, rgba(60,120,240,0.34) 50%, rgba(190,90,210,0.3) 100%), #e8e0f8',
      border: '1px solid rgba(160,130,255,0.45)',
    },
    card: {
      background: 'rgba(255,255,255,0.5)',
      backdropFilter: 'blur(8px)',
      border: '1px solid rgba(180,150,255,0.6)',
      borderRadius: 14,
    },
    badge: { emoji: '💎', label: '玻璃' },
  },
}

// 徽标配色：统一走主题粉色（页面样式不再覆写配色，无需按风格分色）
const BADGE_STYLES = {
  _default: { background: 'rgba(255,255,255,0.72)', color: '#ff6b9b', border: '1px solid #ffb8d4' },
}

// 各风格对应的 tip i18n key；卡片描述直接复用该文案，避免维护两套说明
const STYLE_TIP_KEYS = {
  normal:          'normalTip',
  'liquid-glass':  'liquidGlassTip',
  glass:           'glassTip',
  'acg-glass':     'acgGlassTip',
  'wallpaper-acg': 'wallpaperAcgTip',
  sakura:          'sakuraTip',
  paper:           'paperTip',
  calendar:        'calendarTip',
  github:          'githubTip',
  material:        'materialTip',
}

const PageStylePicker = ({ open, onClose }) => {
  const { t } = useTranslation()
  const { pageStyle, setPageStyle, wallpaperUrl, setWallpaperUrl } = useThemeMode()

  // 仅在选中需要背景图的风格时显示地址输入框
  const needsWallpaper = WALLPAPER_STYLE_KEYS.includes(pageStyle)

  // 本地草稿：避免每输入一个字符就写 CSS 变量触发图片请求，失焦/回车才提交
  const [draftUrl, setDraftUrl] = useState(wallpaperUrl)
  useEffect(() => { setDraftUrl(wallpaperUrl) }, [wallpaperUrl, open])

  const commitUrl = () => {
    if (draftUrl.trim() !== wallpaperUrl) setWallpaperUrl(draftUrl)
  }

  const invalidUrl = !!draftUrl.trim() && !/^https?:\/\//i.test(draftUrl.trim())

  return (
    <ResponsiveModal
      title={t('pageStyle.title')}
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      <div className="py-2">
        <div className="mb-4 text-sm leading-6" style={{ color: 'var(--color-text-secondary)' }}>
          {t('pageStyle.selectTip')}
        </div>

        {/* why：双列正好容纳 10 款风格，避免三列缩略图拥挤和末行孤项；小屏自动回到单列。 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[58vh] overflow-y-auto pr-1 pb-1">
          {PAGE_STYLES.map(({ key, name }) => {
            const active = pageStyle === key
            const pv = PREVIEW_STYLES[key] || PREVIEW_STYLES.normal
            const descriptionKey = STYLE_TIP_KEYS[key]
            return (
              <button
                key={key}
                type="button"
                aria-pressed={active}
                className="group relative flex min-h-[104px] w-full items-center gap-3 rounded-2xl border p-3 text-left transition-all duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                style={{
                  borderColor: active ? 'var(--color-primary)' : 'var(--color-border)',
                  backgroundColor: active ? 'var(--color-hover)' : 'var(--color-card)',
                  boxShadow: active
                    ? '0 6px 20px color-mix(in srgb, var(--color-primary) 18%, transparent)'
                    : '0 2px 8px rgba(0,0,0,0.04)',
                  '--tw-ring-color': 'var(--color-primary)',
                }}
                onClick={() => setPageStyle(key)}
              >
                <div
                  className="relative h-[76px] w-[104px] shrink-0 overflow-hidden rounded-xl transition-transform duration-200 group-hover:scale-[1.02] flex items-center justify-center"
                  style={pv.wrap}
                >
                  {/* 内部小卡片用于突出圆角、边框、投影与玻璃等风格差异。 */}
                  <div className="h-9 w-3/4" style={pv.card} />
                  {pv.badge && (
                    <div
                      className="absolute bottom-1.5 left-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                      style={BADGE_STYLES._default}
                    >
                      {pv.badge.emoji} {pv.badge.label}
                    </div>
                  )}
                </div>

                <div className="min-w-0 flex-1 pr-6">
                  <div
                    className="mb-1 text-sm font-semibold"
                    style={{ color: active ? 'var(--color-primary)' : 'var(--color-text)' }}
                  >
                    {t(`pageStyle.${name}`)}
                  </div>
                  <div
                    className="text-xs leading-5"
                    style={{
                      color: 'var(--color-text-secondary)',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}
                  >
                    {descriptionKey ? t(`pageStyle.${descriptionKey}`) : ''}
                  </div>
                </div>

                {active && (
                  <div
                    className="absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full"
                    style={{ backgroundColor: 'var(--color-primary)' }}
                  >
                    <CheckOutlined style={{ color: '#fff', fontSize: 10 }} />
                  </div>
                )}
              </button>
            )
          })}
        </div>

        {/* why：壁纸配置独立成面板，避免它与主题卡片、状态说明混成同一层级。 */}
        {needsWallpaper && (
          <div
            className="mt-4 rounded-xl border p-3"
            style={{ borderColor: 'var(--color-border)', backgroundColor: 'var(--color-hover)' }}
          >
            <div className="mb-2 text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
              <PictureOutlined className="mr-1.5" />
              {t('pageStyle.wallpaperUrlLabel')}
            </div>
            <Input
              allowClear
              value={draftUrl}
              onChange={e => setDraftUrl(e.target.value)}
              onBlur={commitUrl}
              onPressEnter={commitUrl}
              placeholder={t('pageStyle.wallpaperUrlPlaceholder')}
              status={invalidUrl ? 'error' : undefined}
            />
            <div className="mt-2 text-xs leading-5" style={{ color: 'var(--color-text-secondary)' }}>
              {invalidUrl
                ? t('pageStyle.wallpaperUrlInvalid')
                : draftUrl.trim()
                  ? t('pageStyle.wallpaperUrlActiveHint')
                  : t('pageStyle.wallpaperUrlEmptyHint')}
            </div>
          </div>
        )}
      </div>
    </ResponsiveModal>
  )
}

export default PageStylePicker

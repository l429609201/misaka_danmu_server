import { useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Modal, Tooltip } from 'antd'
import { CheckOutlined } from '@ant-design/icons'
import { useThemeMode, PRESET_THEME_COLORS } from '../ThemeProvider'

const ThemeColorPicker = ({ open, onClose }) => {
  const { t } = useTranslation()
  const { themeColor, setThemeColor } = useThemeMode()
  const [customColor, setCustomColor] = useState(themeColor)
  const colorInputRef = useRef(null)

  const handlePresetClick = (color) => {
    setThemeColor(color)
    setCustomColor(color)
  }

  const handleCustomColorChange = (e) => {
    const color = e.target.value
    setCustomColor(color)
    setThemeColor(color)
  }

  return (
    <Modal
      title={t('themeColor.title')}
      open={open}
      onCancel={onClose}
      footer={null}
      width={380}
      centered
    >
      <div className="py-2">
        {/* 预设色板 */}
        <div className="mb-4">
          <div className="text-sm text-gray-500 dark:text-gray-400 mb-3">{t('themeColor.presetTheme')}</div>
          <div className="grid grid-cols-4 gap-3">
            {PRESET_THEME_COLORS.map(({ name, color }) => (
              <Tooltip title={t(`themeColor.${name}`)} key={color}>
                <div
                  className="flex flex-col items-center gap-1.5 cursor-pointer group"
                  onClick={() => handlePresetClick(color)}
                >
                  <div
                    className="w-10 h-10 rounded-full transition-all duration-200 flex items-center justify-center group-hover:scale-110 group-hover:shadow-lg"
                    style={{
                      backgroundColor: color,
                      boxShadow: themeColor.toUpperCase() === color.toUpperCase()
                        ? `0 0 0 3px var(--color-bg), 0 0 0 5px ${color}`
                        : '0 2px 8px rgba(0,0,0,0.15)',
                    }}
                  >
                    {themeColor.toUpperCase() === color.toUpperCase() && (
                      <CheckOutlined style={{ color: '#fff', fontSize: 16 }} />
                    )}
                  </div>
                  <span className="text-xs text-gray-500 dark:text-gray-400">{t(`themeColor.${name}`)}</span>
                </div>
              </Tooltip>
            ))}
          </div>
        </div>

        {/* 分割线 */}
        <div className="border-t border-gray-200 dark:border-gray-700 my-4" />

        {/* 自定义调色板 */}
        <div>
          <div className="text-sm text-gray-500 dark:text-gray-400 mb-3">{t('themeColor.customColor')}</div>
          <div className="flex items-center gap-3">
            <div
              className="relative w-10 h-10 rounded-full cursor-pointer overflow-hidden transition-transform hover:scale-110"
              style={{
                backgroundColor: customColor,
                boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              }}
              onClick={() => colorInputRef.current?.click()}
            >
              <input
                ref={colorInputRef}
                type="color"
                value={customColor}
                onChange={handleCustomColorChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
              />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={customColor}
                  onChange={(e) => {
                    const val = e.target.value
                    setCustomColor(val)
                    // 只有合法的 hex 颜色才应用
                    if (/^#[0-9A-Fa-f]{6}$/.test(val)) {
                      setThemeColor(val)
                    }
                  }}
                  className="flex-1 px-3 py-1.5 rounded-lg border text-sm font-mono bg-transparent"
                  style={{
                    borderColor: 'var(--color-border)',
                    color: 'var(--color-text)',
                  }}
                  maxLength={7}
                  placeholder="#FF6B9B"
                />
              </div>
              <div className="text-xs text-gray-400 mt-1">{t('themeColor.inputHexTip')}</div>
            </div>
          </div>
        </div>

        {/* 当前效果预览 */}
        <div className="mt-4 p-3 rounded-lg" style={{ backgroundColor: themeColor + '18' }}>
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 rounded-full" style={{ backgroundColor: themeColor }} />
            <span className="text-sm" style={{ color: themeColor, fontWeight: 500 }}>
              {t('themeColor.currentColor')}：{themeColor}
            </span>
          </div>
        </div>
      </div>
    </Modal>
  )
}

export default ThemeColorPicker


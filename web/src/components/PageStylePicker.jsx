import { CheckOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { useThemeMode, PAGE_STYLES } from '../ThemeProvider'
import { ResponsiveModal } from './ResponsiveModal'

const PageStylePicker = ({ open, onClose }) => {
  const { t } = useTranslation()
  const { pageStyle, setPageStyle } = useThemeMode()

  const handleSelect = (key) => {
    setPageStyle(key)
  }

  // 每种风格的预览样式（缩略图）
  const previewStyles = {
    normal: {
      background: 'linear-gradient(135deg, #fff 0%, #f5f5f5 100%)',
      border: '1px solid #e5e7eb',
    },
    'liquid-glass': {
      background:
        'linear-gradient(135deg, rgba(255,107,155,0.25) 0%, rgba(64,150,255,0.25) 100%)',
      backdropFilter: 'blur(10px)',
      border: '1px solid rgba(255,255,255,0.6)',
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.6), 0 4px 16px rgba(0,0,0,0.08)',
    },
  }

  return (
    <ResponsiveModal
      title={t('pageStyle.title')}
      open={open}
      onCancel={onClose}
      footer={null}
      width={420}
    >
      <div className="py-2">
        <div className="text-sm text-gray-500 dark:text-gray-400 mb-3">
          {t('pageStyle.selectTip')}
        </div>
        <div className="grid grid-cols-2 gap-3">
          {PAGE_STYLES.map(({ key, name }) => {
            const active = pageStyle === key
            return (
              <div
                key={key}
                className="cursor-pointer group flex flex-col items-center gap-2"
                onClick={() => handleSelect(key)}
              >
                <div
                  className="relative w-full h-20 rounded-xl transition-all duration-200 group-hover:scale-[1.02] flex items-center justify-center overflow-hidden"
                  style={{
                    ...previewStyles[key],
                    outline: active
                      ? '3px solid var(--color-primary)'
                      : 'none',
                    outlineOffset: 2,
                  }}
                >
                  {/* 内部装饰小卡片，体现风格 */}
                  <div
                    className="w-3/4 h-10 rounded-lg"
                    style={
                      key === 'liquid-glass'
                        ? {
                            background: 'rgba(255,255,255,0.45)',
                            backdropFilter: 'blur(8px)',
                            border: '1px solid rgba(255,255,255,0.6)',
                          }
                        : {
                            background: '#ffffff',
                            border: '1px solid #e5e7eb',
                            boxShadow: '0 2px 4px rgba(0,0,0,0.04)',
                          }
                    }
                  />
                  {active && (
                    <div
                      className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full flex items-center justify-center"
                      style={{ backgroundColor: 'var(--color-primary)' }}
                    >
                      <CheckOutlined style={{ color: '#fff', fontSize: 12 }} />
                    </div>
                  )}
                </div>
                <span
                  className="text-sm font-medium"
                  style={{
                    color: active
                      ? 'var(--color-primary)'
                      : 'var(--color-text)',
                  }}
                >
                  {t(`pageStyle.${name}`)}
                </span>
              </div>
            )
          })}
        </div>

        <div
          className="mt-4 p-3 rounded-lg text-xs"
          style={{
            backgroundColor: 'var(--color-hover)',
            color: 'var(--color-text)',
          }}
        >
          {t('pageStyle.liquidGlassTip')}
        </div>
      </div>
    </ResponsiveModal>
  )
}

export default PageStylePicker

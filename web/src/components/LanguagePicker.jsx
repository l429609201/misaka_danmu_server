import { CheckOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { useThemeMode } from '../ThemeProvider'
import { SUPPORTED_LANGUAGES } from '../i18n'
import { ResponsiveModal } from './ResponsiveModal'

const LanguagePicker = ({ open, onClose }) => {
  const { t } = useTranslation()
  const { language, setLanguage } = useThemeMode()

  const handleSelect = (key) => {
    setLanguage(key)
    onClose?.()
  }

  return (
    <ResponsiveModal
      title={t('language.title')}
      open={open}
      onCancel={onClose}
      footer={null}
      width={380}
    >
      <div className="py-2">
        <div className="text-sm text-gray-500 dark:text-gray-400 mb-3">
          {t('language.switchTip')}
        </div>
        <div className="flex flex-col gap-2">
          {SUPPORTED_LANGUAGES.map(({ key, name, iconfontIcon }) => {
            const active = language === key
            return (
              <div
                key={key}
                className="cursor-pointer flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 hover:bg-base-hover"
                style={{
                  outline: active ? '2px solid var(--color-primary)' : '1px solid var(--color-border)',
                  outlineOffset: -1,
                }}
                onClick={() => handleSelect(key)}
              >
                <div className="flex items-center gap-3">
                  <i
                    className={`iconfont ${iconfontIcon}`}
                    style={{ color: active ? 'var(--color-primary)' : 'var(--color-text)', fontSize: 18, lineHeight: 1 }}
                  />
                  <span
                    className="text-sm font-medium"
                    style={{ color: active ? 'var(--color-primary)' : 'var(--color-text)' }}
                  >
                    {name}
                  </span>
                </div>
                {active && (
                  <CheckOutlined style={{ color: 'var(--color-primary)', fontSize: 16 }} />
                )}
              </div>
            )
          })}
        </div>
      </div>
    </ResponsiveModal>
  )
}

export default LanguagePicker

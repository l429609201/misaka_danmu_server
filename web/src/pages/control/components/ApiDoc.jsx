import { Button, Card } from 'antd'
import { useTranslation } from 'react-i18next'

export const ApiDoc = () => {
  const { t } = useTranslation()
  return (
    <div className="my-6">
      <Card
        title={t('control.apiDocTitle')}
        extra={
          <Button
            onClick={() => {
              window.open('/api/control/docs', '_blank')
            }}
          >
            {t('control.apiDocLink')}
          </Button>
        }
      >
        <div className="w-full">
          <iframe
            className="w-full"
            style={{
              height: `calc(100vh - 300px)`,
              backgroundColor: '#fff9fb',
            }}
            src="/api/control/docs"
          ></iframe>
        </div>
      </Card>
    </div>
  )
}

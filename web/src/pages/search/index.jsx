import { Card } from 'antd'
import { useTranslation } from 'react-i18next'
import { SearchBar } from '../home/components/SearchBar'
import { SearchResult } from '../home/components/SearchResult'

export const Search = () => {
  const { t } = useTranslation()
  return (
    <div className="my-4">
      <Card title={t('nav.search')}>
        <SearchBar />
        <SearchResult />
      </Card>
    </div>
  )
}

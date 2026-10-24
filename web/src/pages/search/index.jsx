import { Card } from 'antd'
import { useTranslation } from 'react-i18next'
import { SearchBar } from '../home/components/SearchBar'
import { SearchResult } from '../home/components/SearchResult'
import { Test } from '../home/components/Test'

export const Search = () => {
  const { t } = useTranslation()
  return (
    <>
      <div className="my-4">
        <Card title={t('home.search')}>
          <SearchBar />
          <SearchResult />
        </Card>
      </div>
      <Test />
    </>
  )
}

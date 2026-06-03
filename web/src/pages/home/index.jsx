import { SearchBar } from './components/SearchBar'
import { SearchResult } from './components/SearchResult'
import { Test } from './components/Test'
import { Card } from 'antd'
import { useTranslation } from 'react-i18next'

export const Home = () => {
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

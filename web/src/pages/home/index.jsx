import { SearchBar } from './components/SearchBar'
import { SearchResult } from './components/SearchResult'
import { Test } from './components/Test'
import { Card } from 'antd'

export const Home = () => (
  <>
    <div className="my-4">
      <Card title="搜索">
        <SearchBar />
        <SearchResult />
      </Card>
    </div>
    <Test />
  </>
)

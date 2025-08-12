import { Logs } from './components/Logs'
import { SearchBar } from './components/SearchBar'
import { SearchResult } from './components/SearchResult'
import { Test } from './components/Test'

export const Home = () => (
  <>
    <SearchBar />
    <SearchResult />
    <Logs />
    <Test />
  </>
)

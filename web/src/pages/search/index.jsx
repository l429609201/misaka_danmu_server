import { useAtomValue } from 'jotai'
import { searchLoadingAtom } from '../../../store'
import { SearchBar } from '../home/components/SearchBar'
import { SearchResult } from '../home/components/SearchResult'

// 搜索结果页：关键词入口在顶栏，本页只放结果卡片（搜索中显示进度卡片，参考 MoviePilot）
export const Search = () => {
  const loading = useAtomValue(searchLoadingAtom)
  return (
    <div className="my-4 space-y-4">
      <SearchBar />
      {/* 搜索期间整页只展示进度（由 SearchBar 渲染），结果区隐藏但不卸载，保留分页/筛选状态 */}
      <div style={{ display: loading ? 'none' : undefined }}>
        <SearchResult />
      </div>
    </div>
  )
}

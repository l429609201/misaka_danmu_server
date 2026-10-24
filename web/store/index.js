import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

/** 768 手机尺寸 */
export const isMobileAtom = atom(false)

/** 桌面端左侧边栏是否折叠（持久化，刷新后保持） */
export const sidebarCollapsedAtom = atomWithStorage(
  'sidebarCollapsedAtom',
  false,
  undefined,
  { getOnInit: true }
)

/** 用户信息 */
export const userinfoAtom = atom(undefined)

/** 最后一次搜索的结果存储 */
export const lastSearchResultAtom = atomWithStorage(
  'lastSearchResultAtom',
  {
    results: [],
    searchSeason: null,
    keyword: '',
    total: 0,
    page: 1,
    pageSize: 10,
  },
  undefined,
  { getOnInit: true }
)

/** 存储 搜索记录 */
export const searchHistoryAtom = atomWithStorage(
  'searchHistoryAtom',
  [],
  undefined,
  { getOnInit: true }
)

/** 首页searchLoafing,跨模块状态 */
export const searchLoadingAtom = atom(false)

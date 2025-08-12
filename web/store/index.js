import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

/** 768 手机尺寸 */
export const isMobileAtom = atom(false)

/** 用户信息 */
export const userinfoAtom = atom(undefined)

/** 最后一次搜索的结果存储 */
export const lastSearchResultAtom = atomWithStorage(
  'lastSearchResultAtom',
  {
    results: [],
    searchSeason: null,
    keyword: '',
  },
  undefined,
  { getOnInit: true }
)

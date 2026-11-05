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

/**
 * 御坂助手看板娘形象是否显示（默认关闭，可在头像菜单开启）。
 * 状态由后端配置项 assistantPetVisible 保存（换浏览器/设备保持一致），
 * AssistantWidget 挂载时从后端读取，头像菜单切换时写回后端。
 */
export const ASSISTANT_PET_CONFIG_KEY = 'assistantPetVisible'
export const assistantPetVisibleAtom = atom(false)

/**
 * 御坂助手总开关（默认关闭）：关闭后全站不显示助手入口、面板，也不再轮询任务播报。
 * 状态由后端配置项 assistantEnabled 保存，在「AI → 御坂助手」设置页切换。
 */
export const ASSISTANT_ENABLED_CONFIG_KEY = 'assistantEnabled'
export const assistantEnabledAtom = atom(false)

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

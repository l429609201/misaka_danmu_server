import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

export const themeAtom = atomWithStorage('themeAtom', 'light', undefined, {
  getOnInit: true,
})

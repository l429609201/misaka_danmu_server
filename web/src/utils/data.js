/**
 * A lightweight keyword parser for frontend caching logic.
 * It's not as robust as the backend's but covers common cases.
 * @param {string} keyword The search keyword.
 * @returns {{title: string, season: number|null, episode: number|null}}
 */
export function parseSearchKeyword(keyword) {
  if (!keyword) return { title: keyword, season: null, episode: null }

  keyword = keyword.trim()

  // Pattern for SXXEXX
  let match = keyword.match(/^(.*?)\s*S(\d{1,2})E(\d{1,4})$/i)
  if (match) {
    return {
      title: match[1].trim(),
      season: parseInt(match[2], 10),
      episode: parseInt(match[3], 10),
    }
  }

  // Pattern for Season XX or SXX
  match = keyword.match(/^(.*?)\s*(?:S|Season)\s*(\d{1,2})$/i)
  if (match) {
    return {
      title: match[1].trim(),
      season: parseInt(match[2], 10),
      episode: null,
    }
  }

  const chineseNumMap = {
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
    十: 10,
  }
  match = keyword.match(/^(.*?)\s*第\s*([一二三四五六七八九十\d]+)\s*[季部]$/i)
  if (match) {
    const numStr = match[2]
    const season = chineseNumMap[numStr] || parseInt(numStr, 10)
    if (!isNaN(season))
      return { title: match[1].trim(), season: season, episode: null }
  }
  return { title: keyword, season: null, episode: null }
}

export function isUrl(str) {
  const urlRegex = /^https?:\/\//i
  return urlRegex.test(str)
}

export function generateRandomStr(len = 15) {
  const numbers = '0123456789'
  const upperLetters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  const lowerLetters = 'abcdefghijklmnopqrstuvwxyz'
  const charPool = numbers + upperLetters + lowerLetters

  let result = ''
  const poolLength = charPool.length
  for (let i = 0; i < len; i++) {
    const randomIndex = Math.floor(Math.random() * poolLength)
    result += charPool[randomIndex]
  }

  return result
}

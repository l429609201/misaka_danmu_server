export const DANMU_API_TOKEN_KEY = 'danmu_api_token'

export const DANDAN_TYPE_MAPPING = {
  tvseries: 'tv_series',
  movie: 'movie',
  ova: 'ova',
  other: 'other',
  // 综艺/短剧：与后端 src/schemas/media_types.py 的 ANIME_TYPES 保持一致
  variety: 'variety',
  shortDrama: 'short_drama',
}
export const DANDAN_TYPE_DESC_MAPPING = {
  tv_series: '电视节目',
  movie: '电影/剧场版',
  ova: 'OVA',
  other: '其他',
  variety: '综艺',
  short_drama: '短剧',
}

// 弹幕库可选类型（编辑、新建、筛选下拉共用）
export const ANIME_TYPE_OPTIONS = Object.entries(DANDAN_TYPE_DESC_MAPPING).map(
  ([value, label]) => ({ value, label })
)

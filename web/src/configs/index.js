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

// 弹幕存储类型（存储设置页唯一数据源，5 个类型统一渲染）：
// - key：后端 config 表键前缀（xxxDanmakuDirectoryPath / xxxDanmakuFilenameTemplate）
// - fallback：留空时沿用的类型；带 fallback 的类型须与后端 src/schemas/media_types.py 的
//   OWN_STORAGE_TYPE_PREFIXES 一致（后端 resolve_storage_config 逐项回退到 tv）
// - icon：MyIcon 图标名（与媒体库卡片 typeIconMap 一致，短剧暂无专属图标沿用 tv）
// - i18n：可选的专属文案键，缺省时使用通用文案
// - example：路径预览用的示例作品
export const STORAGE_TYPES = [
  {
    key: 'movie', type: DANDAN_TYPE_MAPPING.movie, icon: 'movie',
    defaultDir: '/app/config/danmaku/movies', defaultTemplate: '${title}/${episodeId}',
    i18n: { tab: 'danmakuStorage.tabMovie', dirDesc: 'danmakuStorage.descMovieDir', dirSelected: 'danmakuStorage.movieDirSelected', previewExample: 'danmakuStorage.moviePreviewExample' },
    example: { title: '铃芽之旅 第二季', titleBase: '铃芽之旅', year: '2022', tmdbId: '1022789' },
  },
  {
    key: 'tv', type: DANDAN_TYPE_MAPPING.tvseries, icon: 'tv',
    defaultDir: '/app/config/danmaku/tv', defaultTemplate: '${animeId}/${episodeId}',
    i18n: { tab: 'danmakuStorage.tabTv', dirDesc: 'danmakuStorage.descTvDir', dirSelected: 'danmakuStorage.tvDirSelected', previewExample: 'danmakuStorage.tvPreviewExample' },
    example: { title: '葬送的芙莉莲 第二季', titleBase: '葬送的芙莉莲', year: '2023', tmdbId: '209867' },
  },
  { key: 'ova', type: DANDAN_TYPE_MAPPING.ova, icon: 'ova', fallback: 'tv' },
  { key: 'variety', type: DANDAN_TYPE_MAPPING.variety, icon: 'variety', fallback: 'tv' },
  { key: 'shortDrama', type: DANDAN_TYPE_MAPPING.shortDrama, icon: 'tv', fallback: 'tv' },
].map((item) => ({
  defaultDir: '',
  defaultTemplate: '',
  i18n: {},
  example: { title: '示例作品 第二季', titleBase: '示例作品', year: '2024', tmdbId: '209867' },
  ...item,
  category: DANDAN_TYPE_DESC_MAPPING[item.type],
  label: DANDAN_TYPE_DESC_MAPPING[item.type],
}))

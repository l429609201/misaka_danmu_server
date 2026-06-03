/**
 * 弹幕库卡片视图组件
 */
import React from 'react'
import { Tag, Tooltip, Space, Dropdown } from 'antd'
import { MenuOutlined } from '@ant-design/icons'
import { DANDAN_TYPE_DESC_MAPPING } from '../../configs'
import { MyIcon } from '@/components/MyIcon'
import { useTranslation } from 'react-i18next'

const getImageSrc = (record) => {
  let src = record.localImagePath || record.imageUrl
  if (src && src.startsWith('/images/')) src = src.replace('/images/', '/data/images/')
  return src
}

const typeIconMap = {
  tv_series: 'tv',
  movie: 'movie',
  ova: 'tv',
  other: 'tv',
}

const AnimeCard = ({ record, onEdit, onDelete, onNavigate, onFavorite, onIncremental, onFinished }) => {
  const { t } = useTranslation()
  const imageSrc = getImageSrc(record)
  const hasFavorited = record.sources?.some(s => s.isFavorited)
  const hasIncremental = record.sources?.some(s => s.incrementalRefreshEnabled)
  const allFinished = record.sources?.length > 0 && record.sources.every(s => s.isFinished)

  const menuItems = [
    {
      key: 'favorite',
      label: hasFavorited ? t('libraryGroup.menuUnFav') : t('libraryGroup.menuFav'),
      icon: <MyIcon icon={hasFavorited ? 'favorites-fill' : 'favorites'} size={16} className={hasFavorited ? 'text-yellow-400' : ''} />,
      onClick: () => onFavorite?.(record),
    },
    {
      key: 'incremental',
      label: hasIncremental ? t('libraryGroup.menuUnInc') : t('libraryGroup.menuInc'),
      icon: <MyIcon icon={hasIncremental ? 'zengliang' : 'clock'} size={16} className={hasIncremental ? 'text-green-500' : ''} />,
      onClick: () => onIncremental?.(record),
    },
    {
      key: 'finished',
      label: allFinished ? t('libraryGroup.menuUnFin') : t('libraryGroup.menuFin'),
      icon: <MyIcon icon={allFinished ? 'wanjie1' : 'wanjie'} size={16} className={allFinished ? 'text-blue-500' : 'text-gray-400'} />,
      onClick: () => onFinished?.(record),
    },
  ]

  return (
    <div
      className="group relative flex flex-col rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:shadow-md transition-shadow cursor-pointer"
      style={{ width: '100%' }}
    >
      {/* 封面 */}
      <div className="relative aspect-[2/3] overflow-hidden bg-gray-100 dark:bg-gray-700"
        onClick={() => onNavigate(record)}>
        {imageSrc ? (
          <img src={imageSrc} alt={record.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-gray-300 text-4xl">
            🎬
          </div>
        )}
        {/* 类型角标 */}
        <div className="absolute top-1 left-1 bg-black/60 rounded px-1 py-0.5">
          <MyIcon icon={typeIconMap[record.type] || 'tv'} size={16} color="#fff" />
        </div>
        {/* 右上角状态标记（追更/完结/标记，从右到左） */}
        <div className="absolute top-1 right-1 flex items-center gap-0.5 pointer-events-none">
          {allFinished && (
            <div className="bg-black/60 rounded px-1 py-0.5">
              <MyIcon icon="wanjie1" size={14} color="#60a5fa" />
            </div>
          )}
          {hasIncremental && (
            <div className="bg-black/60 rounded px-1 py-0.5">
              <MyIcon icon="zengliang" size={14} color="#4ade80" />
            </div>
          )}
          {hasFavorited && (
            <div className="bg-black/60 rounded px-1 py-0.5">
              <MyIcon icon="favorites-fill" size={14} color="#facc15" />
            </div>
          )}
        </div>
        {/* 悬浮操作层 */}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-200 flex items-end justify-end p-2 opacity-0 group-hover:opacity-100">
          <Space size={6} onClick={e => e.stopPropagation()}>
            <Tooltip title={t('libraryGroup.btnEdit')}>
              <span className="w-7 h-7 bg-white/90 dark:bg-gray-600/90 rounded flex items-center justify-center cursor-pointer hover:bg-white dark:hover:bg-gray-500"
                onClick={() => onEdit(record)}>
                <MyIcon icon="edit" size={14} />
              </span>
            </Tooltip>
            <Dropdown menu={{ items: menuItems }} trigger={['click']}>
              <span className="w-7 h-7 bg-white/90 dark:bg-gray-600/90 rounded flex items-center justify-center cursor-pointer hover:bg-white dark:hover:bg-gray-500">
                <MenuOutlined style={{ fontSize: 13 }} />
              </span>
            </Dropdown>
            <Tooltip title={t('libraryGroup.btnDelete')}>
              <span className="w-7 h-7 bg-white/90 dark:bg-gray-600/90 rounded flex items-center justify-center cursor-pointer hover:bg-white dark:hover:bg-gray-500 hover:text-red-500"
                onClick={() => onDelete(record)}>
                <MyIcon icon="delete" size={14} />
              </span>
            </Tooltip>
          </Space>
        </div>
      </div>

      {/* 信息区 */}
      <div className="p-2 flex flex-col gap-1.5">
        <Tooltip title={record.title}>
          <div className="text-sm font-medium leading-tight line-clamp-2 text-gray-900 dark:text-gray-100"
            style={{ minHeight: '2.5rem' }}
            onClick={() => onNavigate(record)}>
            {record.title}
          </div>
        </Tooltip>
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400" style={{ minHeight: '1.25rem' }}>
          {record.year && <span>{record.year}</span>}
          {record.season && <span>S{record.season}</span>}
          <span>·</span>
          <span>{t('libraryGroup.episodeUnit', { count: record.episodeCount || 0 })}</span>
          {record.sourceCount > 0 && (
            <>
              <span>·</span>
              <span>{t('libraryGroup.sourceUnit', { count: record.sourceCount })}</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const LibraryCardView = ({
  list,
  onEdit,
  onDelete,
  onNavigate,
  onFavorite,
  onIncremental,
  onFinished,
}) => {
  if (!list?.length) return null

  return (
    <div
      className="grid gap-3"
      style={{
        gridTemplateColumns: 'repeat(auto-fill, 160px)',
      }}
    >
      {list.map(record => (
        <AnimeCard
          key={record.animeId}
          record={record}
          onEdit={onEdit}
          onDelete={onDelete}
          onNavigate={onNavigate}
          onFavorite={onFavorite}
          onIncremental={onIncremental}
          onFinished={onFinished}
        />
      ))}
    </div>
  )
}

export default LibraryCardView


import React from 'react'
import { Table, Card, Space, Empty } from 'antd'
import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { isMobileAtom } from '../../store/index.js'
import classNames from 'classnames'

/**
 * 响应式表格组件
 * 桌面端显示为表格，移动端显示为卡片列表
 * @param {Object} props
 * @param {Array} props.dataSource - 数据源
 * @param {Array} props.columns - 列配置
 * @param {Function} props.renderCard - 移动端卡片渲染函数 (item, index) => ReactNode
 * @param {Object} props.pagination - 分页配置
 * @param {boolean} props.loading - 加载状态
 * @param {string} props.rowKey - 行key
 * @param {Function} props.onRow - 行点击事件处理函数
 * @param {Object} props.tableProps - 传递给Table的其他属性
 * @param {Object} props.cardProps - 传递给Card的其他属性
 */
export const ResponsiveTable = ({
  dataSource = [],
  columns = [],
  renderCard,
  pagination,
  loading = false,
  rowKey = 'id',
  onRow,
  tableProps = {},
  cardProps = {},
  emptyText,
}) => {
  const { t } = useTranslation()
  const isMobile = useAtomValue(isMobileAtom)
  const emptyDesc = emptyText ?? t('common.noData')

  if (isMobile) {
    // 移动端卡片视图
    return (
      <div className="space-y-3">
        {loading ? (
          <Card loading={loading} />
        ) : dataSource.length === 0 ? (
          <Empty description={emptyDesc} />
        ) : (
          <>
            {dataSource.map((item, index) => {
              const onRowProps = onRow ? onRow(item, index) : {}
              return (
                <Card
                  key={item[rowKey] || index}
                  size="small"
                  className={classNames("shadow-sm hover:shadow-md transition-shadow", onRowProps.style)}
                  onClick={onRowProps.onClick}
                  {...cardProps}
                >
                  {renderCard ? renderCard(item, index) : <DefaultCardContent item={item} columns={columns} />}
                </Card>
              )
            })}
            {pagination && dataSource.length > 0 && (
              <div className="flex justify-center mt-4">
                <Space>
                  {pagination.current > 1 && (
                    <button
                      className="px-4 py-2 bg-primary text-white rounded"
                      onClick={() => pagination.onChange(pagination.current - 1, pagination.pageSize)}
                    >
                      {t('common.prevPage')}
                    </button>
                  )}
                  <span className="px-4 py-2">
                    {pagination.current} / {Math.ceil(pagination.total / pagination.pageSize)}
                  </span>
                  {pagination.current < Math.ceil(pagination.total / pagination.pageSize) && (
                    <button
                      className="px-4 py-2 bg-primary text-white rounded"
                      onClick={() => pagination.onChange(pagination.current + 1, pagination.pageSize)}
                    >
                      {t('common.nextPage')}
                    </button>
                  )}
                </Space>
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  // 桌面端表格视图
  return (
    <Table
      dataSource={dataSource}
      columns={columns}
      pagination={pagination}
      loading={loading}
      rowKey={rowKey}
      scroll={{ x: '100%' }}
      onRow={onRow}
      {...tableProps}
    />
  )
}

/**
 * 默认的卡片内容渲染
 */
const DefaultCardContent = ({ item, columns }) => {
  return (
    <div className="space-y-2">
      {columns
        .filter(col => !col.hideInCard && col.dataIndex)
        .map((col, idx) => {
          const value = item[col.dataIndex]
          const displayValue = col.render ? col.render(value, item, idx) : value

          return (
            <div key={col.key || col.dataIndex} className="flex justify-between items-start">
              <span className="font-medium text-gray-600 dark:text-gray-400 min-w-20">
                {col.title}:
              </span>
              <span className="flex-1 text-right">{displayValue}</span>
            </div>
          )
        })}
    </div>
  )
}

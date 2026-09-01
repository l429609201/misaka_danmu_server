import { Pagination, Button, Tooltip } from 'antd'
import { VerticalAlignTopOutlined, VerticalAlignBottomOutlined } from '@ant-design/icons'
import { useAtomValue } from 'jotai'
import { isMobileAtom } from '../../store'
import { MobileFloatingPagination } from './MobileFloatingPagination'

/**
 * 分页器（响应式）
 *
 * 桌面端：吸底的圆角卡片，内含完整分页器 + 置顶/置底按钮。
 * 移动端：底部不放常驻横条，改用右下角悬浮按钮 + 底部抽屉（见 MobileFloatingPagination）。
 *
 * 桌面端设计说明：
 * - 用 sticky 而不是 fixed：sticky 仍占据文档流空间，不会遮挡列表最后一项，
 *   也不需要给外层容器额外加 padding 来避让。
 * - 外层 Layout 没有 overflow 裁剪，sticky 能正常生效。
 *
 * 移动端之所以完全换一套交互：移动端横向空间有限，常驻底条既挤占内容视野，
 * 又让页码、每页条数、跳转框等控件被压缩到难以点击的尺寸。
 *
 * @param {object} props - 透传给 antd Pagination 的属性（current/pageSize/total 等）
 * @param {string} [props.className] - 追加到外层容器的类名
 * @param {'start'|'center'|'end'} [props.justify='center'] - 分页器在卡片内的水平对齐方式
 * @param {boolean} [props.showScrollButtons=false] - 桌面端是否显示置顶/置底按钮
 */
export const StickyPagination = ({ className = '', justify = 'center', showScrollButtons = false, ...paginationProps }) => {
  const isMobile = useAtomValue(isMobileAtom)

  // 水平对齐方式映射，避免在 JSX 里写三元嵌套
  const justifyClass = {
    start: 'justify-start',
    center: 'justify-center',
    end: 'justify-end',
  }[justify] ?? 'justify-center'

  // 平滑滚动到顶部
  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // 平滑滚动到底部
  const scrollToBottom = () => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })
  }

  // 移动端：整套换成悬浮按钮 + 抽屉，不渲染吸底横条
  if (isMobile) {
    const { current, pageSize, total, onChange, onShowSizeChange, pageSizeOptions } = paginationProps
    return (
      <MobileFloatingPagination
        current={current}
        pageSize={pageSize}
        total={total}
        onChange={onChange}
        onShowSizeChange={onShowSizeChange}
        pageSizeOptions={pageSizeOptions}
      />
    )
  }

  // 桌面端：吸底圆角卡片，底部留少量呼吸空间
  return (
    <div className={`sticky z-30 mt-4 ${className}`} style={{ bottom: 12 }}>
      <div
        className={`flex items-center ${justifyClass} rounded-2xl px-3 py-2 backdrop-blur-md sticky-pagination-card`}
      >
        {showScrollButtons && (
          <div className="flex gap-1 mr-3">
            <Tooltip title="置顶">
              <Button
                type="text"
                size="small"
                icon={<VerticalAlignTopOutlined />}
                onClick={scrollToTop}
              />
            </Tooltip>
            <Tooltip title="置底">
              <Button
                type="text"
                size="small"
                icon={<VerticalAlignBottomOutlined />}
                onClick={scrollToBottom}
              />
            </Tooltip>
          </div>
        )}

        <Pagination {...paginationProps} />
      </div>
    </div>
  )
}

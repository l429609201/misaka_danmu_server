import { useState, useEffect, useMemo } from 'react'
import { Drawer, Button } from 'antd'
import {
  VerticalAlignTopOutlined,
  VerticalAlignBottomOutlined,
} from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

/**
 * 移动端悬浮分页器（E 方案）
 *
 * 设计要点：
 * - 底部不放任何常驻横条，视野完全交给内容；只在右下角保留一个显示当前页码的悬浮按钮。
 * - 点击悬浮按钮从底部滑出抽屉，内含「回到顶部 / 跳到底部」快捷操作、页码网格、每页条数。
 * - 抽屉上方另有一个独立的置顶按钮，页面滚动超过一定距离后弹性淡入。
 *
 * 为什么把置顶/置底同时放在抽屉里和抽屉外：
 * - 抽屉外的置顶按钮解决高频需求（看完内容快速回顶），一次点击即可完成；
 * - 抽屉内再提供一组，是为了让用户打开抽屉准备翻页时，也能顺手定位，不必先关抽屉。
 *
 * @param {number} props.current - 当前页码
 * @param {number} props.pageSize - 每页条数
 * @param {number} props.total - 数据总条数
 * @param {(page:number,pageSize:number)=>void} props.onChange - 页码变化回调
 * @param {(current:number,size:number)=>void} [props.onShowSizeChange] - 每页条数变化回调
 * @param {number[]} [props.pageSizeOptions] - 可选的每页条数
 */
export const MobileFloatingPagination = ({
  current = 1,
  pageSize = 20,
  total = 0,
  onChange,
  onShowSizeChange,
  pageSizeOptions = [20, 50, 100],
}) => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  // 是否已滚动足够距离（用于控制置顶按钮的显隐）
  const [scrolled, setScrolled] = useState(false)

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil(total / pageSize)),
    [total, pageSize]
  )

  useEffect(() => {
    // 滚动超过 60% 屏高时显示置顶按钮，用 rAF 节流避免高频重渲染
    let ticking = false
    const onScroll = () => {
      if (ticking) return
      ticking = true
      window.requestAnimationFrame(() => {
        setScrolled(window.scrollY > window.innerHeight * 0.6)
        ticking = false
      })
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' })
  const scrollToBottom = () =>
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: 'smooth',
    })

  // 抽屉内的定位操作：先收起抽屉再滚动，否则抽屉会挡住滚动结果
  const handleSheetScroll = fn => {
    setOpen(false)
    setTimeout(fn, 180)
  }

  // 跳转到指定页：翻页后回到顶部，避免停留在上一页的滚动位置
  const handleJump = page => {
    if (page === current) {
      setOpen(false)
      return
    }
    onChange?.(page, pageSize)
    setOpen(false)
    setTimeout(scrollToTop, 180)
  }

  const handleSizeChange = size => {
    if (size === pageSize) return
    onShowSizeChange?.(current, size)
    onChange?.(1, size)
    setTimeout(scrollToTop, 180)
  }

  // 数据为空时不渲染，避免出现无意义的悬浮按钮
  if (!total) return null

  return (
    <>
      <div className="mobile-pagination-fabs">
        {/* 置顶按钮：滚动后弹性淡入，样式做成次级（描边）以区分主次 */}
        <button
          type="button"
          aria-label={t('pagination.backToTop')}
          className={`mobile-pagination-fab-top${scrolled ? ' is-visible' : ''}`}
          onClick={scrollToTop}
        >
          <VerticalAlignTopOutlined />
        </button>

        {/* 分页主按钮：常驻显示当前页码，点击展开抽屉 */}
        <button
          type="button"
          aria-label={t('pagination.openPageSheet')}
          className="mobile-pagination-fab-page"
          onClick={() => setOpen(true)}
        >
          <b>{current}</b>
          <small>/{totalPages}</small>
        </button>
      </div>

      <Drawer
        placement="bottom"
        open={open}
        onClose={() => setOpen(false)}
        height="auto"
        styles={{ body: { padding: 16 } }}
        title={t('pagination.sheetTitle')}
        className="mobile-pagination-drawer"
      >
        {/* 快捷定位：置顶 / 置底 */}
        <div className="flex gap-2 mb-4">
          <Button
            block
            size="large"
            icon={<VerticalAlignTopOutlined />}
            onClick={() => handleSheetScroll(scrollToTop)}
          >
            {t('pagination.backToTop')}
          </Button>
          <Button
            block
            size="large"
            icon={<VerticalAlignBottomOutlined />}
            onClick={() => handleSheetScroll(scrollToBottom)}
          >
            {t('pagination.goToBottom')}
          </Button>
        </div>

        <p className="mobile-pagination-summary">
          {t('pagination.summary', { total, current, totalPages })}
        </p>

        {/* 页码网格：每行 5 个，触控目标 44px 满足移动端最小点击区域 */}
        <div className="mobile-pagination-grid">
          {Array.from({ length: totalPages }, (_, i) => i + 1).map(page => (
            <button
              key={page}
              type="button"
              className={`mobile-pagination-page${page === current ? ' is-active' : ''}`}
              onClick={() => handleJump(page)}
            >
              {page}
            </button>
          ))}
        </div>

        <div className="mobile-pagination-size">
          <span>{t('pagination.perPage')}</span>
          <div className="flex gap-2 flex-1">
            {pageSizeOptions.map(size => (
              <Button
                key={size}
                block
                type={size === pageSize ? 'primary' : 'default'}
                onClick={() => handleSizeChange(size)}
              >
                {size}
              </Button>
            ))}
          </div>
        </div>
      </Drawer>
    </>
  )
}

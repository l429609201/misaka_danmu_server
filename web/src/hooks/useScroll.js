import { useRef, useEffect, useCallback } from 'react'
import _ from 'lodash'

/**
 * 自定义滚动加载更多Hook
 * 使用IntersectionObserver API实现高性能的滚动加载
 * @param {Object} options - 配置选项
 * @param {boolean} options.canLoadMore - 是否可以加载更多
 * @param {Function} options.onLoadMore - 加载更多的回调函数
 * @param {Function} options.setRoot - 设置观察根元素的函数
 * @returns {Array} 返回设置目标元素引用的函数
 */
export const useScroll = ({ canLoadMore, onLoadMore, setRoot }) => {
  const onLoadMoreRef = useRef(() => {})
  const targetRef = useRef(null)
  const unobserveRef = useRef(null)
  const isLoadingRef = useRef(false)

  /**
   * 使用防抖处理加载更多函数，避免频繁触发
   */
  useEffect(() => {
    onLoadMoreRef.current = _.debounce(async () => {
      // 防止重复触发
      if (isLoadingRef.current || !canLoadMore) {
        return
      }

      isLoadingRef.current = true
      try {
        await onLoadMore()
      } finally {
        // 延迟重置加载状态，避免DOM更新时立即重新触发
        setTimeout(() => {
          isLoadingRef.current = false
        }, 500)
      }
    }, 300)
  }, [onLoadMore, canLoadMore])

  /**
   * 观察目标元素，当元素进入视口时触发加载更多
   */
  const observeTarget = useCallback(() => {
    // 清理之前的观察器
    if (unobserveRef.current) {
      unobserveRef.current()
      unobserveRef.current = null
    }

    const target = targetRef.current
    if (!target || !canLoadMore) {
      return
    }

    // 创建IntersectionObserver实例
    const observer = new IntersectionObserver(
      entries => {
        entries.forEach(({ isIntersecting, intersectionRatio }) => {
          // 只有当元素进入视口且不在加载状态时才触发
          if (
            isIntersecting &&
            intersectionRatio > 0 &&
            !isLoadingRef.current &&
            canLoadMore
          ) {
            onLoadMoreRef.current()
          }
        })
      },
      {
        // 使用null作为root，表示使用viewport作为根元素
        root: null,
        rootMargin: '0px 0px 100px 0px', // 提前100px触发
        threshold: 0.1, // 至少10%的元素可见时才触发
      }
    )

    observer.observe(target)
    unobserveRef.current = () => observer.unobserve(target)

    return () => observer.disconnect()
  }, [canLoadMore, setRoot])

  /**
   * 设置目标元素引用的回调函数
   * @param {Element|null} target - 目标DOM元素
   */
  const setTargetRef = useCallback(
    target => {
      targetRef.current = target
      // 设置目标元素后立即开始观察
      if (target) {
        observeTarget()
      }
    },
    [observeTarget, canLoadMore]
  )

  return [setTargetRef]
}

import { cn } from './utils'

// 移植自 AniBT UI empty：well（下沉面板）/ plain（无框）
/** 空状态容器 */
export function Empty({ variant = 'well', size = 'default', className, ...props }) {
  return (
    <div
      className={cn(
        'flex w-full min-w-0 flex-1 flex-col items-center justify-center gap-4 px-6 text-center text-balance',
        variant === 'well' && 'anibt-well',
        size === 'sm' ? 'py-6' : 'py-10',
        className
      )}
      {...props}
    />
  )
}

/** 空状态图标：贴纸风格小方块 */
export function EmptyMedia({ className, ...props }) {
  return (
    <div
      aria-hidden="true"
      className={cn('anibt-rim relative mb-1 flex size-10 shrink-0 -rotate-3 items-center justify-center rounded-[10px] bg-ab-card text-[20px] text-anibt-mark', className)}
      {...props}
    />
  )
}

export function EmptyTitle({ className, ...props }) {
  return <div className={cn('text-[15px] leading-[1.45] font-bold text-ab-foreground', className)} {...props} />
}

export function EmptyDescription({ className, ...props }) {
  return <div className={cn('text-[13px] leading-relaxed text-ab-muted-foreground', className)} {...props} />
}

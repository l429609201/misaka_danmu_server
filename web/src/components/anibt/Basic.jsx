import { LoadingOutlined } from '@ant-design/icons'
import { cn } from './utils'

// 移植自 AniBT UI：spinner / skeleton / kbd / separator

const SPINNER_SIZE = { sm: 'size-3', default: 'size-4', lg: 'size-6' }

/** 加载指示器 */
export function Spinner({ size = 'default', tone = 'current', label = '加载中', className, ...props }) {
  return (
    <output aria-label={label} className={cn('inline-grid shrink-0 place-items-center', SPINNER_SIZE[size], tone === 'mark' ? 'text-anibt-mark' : 'text-current', className)} {...props}>
      <LoadingOutlined className="text-[length:inherit]" style={{ fontSize: 'inherit' }} aria-hidden="true" />
    </output>
  )
}

/** 骨架占位：box / line / pill / circle */
export function Skeleton({ shape = 'box', className, ...props }) {
  return (
    <div
      aria-hidden="true"
      data-shape={shape}
      className={cn(
        'animate-pulse bg-anibt-deep2 motion-reduce:animate-none',
        shape === 'box' && 'rounded-[10px]',
        shape === 'line' && 'h-3.5 rounded-[6px]',
        shape === 'pill' && 'rounded-full',
        shape === 'circle' && 'aspect-square rounded-full',
        className
      )}
      {...props}
    />
  )
}

/** 键帽 */
export function Kbd({ className, ...props }) {
  return (
    <kbd
      className={cn(
        'pointer-events-none inline-flex h-5 w-fit min-w-5 shrink-0 items-center justify-center gap-0.5 rounded-[6px] bg-ab-muted px-1 font-anibt-display text-[12px] leading-none font-semibold text-ab-muted-foreground shadow-[inset_0_-1px_0_var(--ab-input),inset_0_0_0_1px_var(--ab-border)] select-none',
        className
      )}
      {...props}
    />
  )
}

/** 分隔线：stitch 为缝线虚线，line 为实线 */
export function Separator({ orientation = 'horizontal', variant = 'stitch', className, ...props }) {
  const horizontal = orientation === 'horizontal'
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        'shrink-0',
        horizontal ? 'h-px w-full' : 'w-px self-stretch',
        variant === 'line' && 'bg-ab-border',
        variant === 'stitch' && horizontal && 'bg-(image:--anibt-stitch) bg-size-[8px_1px] bg-repeat-x',
        variant === 'stitch' && !horizontal && 'bg-(image:--anibt-stitch-y) bg-size-[1px_8px] bg-repeat-y',
        className
      )}
      {...props}
    />
  )
}

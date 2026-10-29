import { cn } from './utils'

// 移植自 AniBT UI badge：标签 / 贴纸 / 计数、状态徽章、状态点
const BADGE_VARIANT = {
  default: 'anibt-tag',
  res: 'anibt-tag anibt-tag-res',
  lang: 'anibt-tag anibt-tag-lang',
  success: 'anibt-tag anibt-tag-ok',
  warning: 'anibt-tag anibt-tag-warn',
  destructive: 'anibt-tag anibt-tag-danger',
  sticker: 'anibt-sticker',
  candy: 'anibt-sticker anibt-sticker-candy',
  water: 'anibt-sticker anibt-sticker-water',
  count: 'anibt-count',
  'count-quiet': 'anibt-count anibt-count-quiet',
}

/** 徽章；tilt 为贴纸倾斜角度（度） */
export function Badge({ variant = 'default', face = 'spec', size = 'default', tilt, className, style, ...props }) {
  const tiltStyle = tilt === undefined ? style : { ...style, '--anibt-tilt': `${tilt}deg` }
  return (
    <span
      data-variant={variant}
      className={cn(
        'inline-flex w-fit shrink-0 items-center justify-center gap-1 overflow-hidden [&>svg]:pointer-events-none [&>svg]:size-3',
        BADGE_VARIANT[variant] || BADGE_VARIANT.default,
        face === 'spec' && 'anibt-num',
        size === 'lg' && 'h-6 px-2.5',
        className
      )}
      style={tiltStyle}
      {...props}
    />
  )
}

/** 状态徽章：ok / run / fail / warn / mute / on */
export function StatusBadge({ tone = 'mute', className, ...props }) {
  return <span data-tone={tone} className={cn('anibt-status', `anibt-status-${tone}`, className)} {...props} />
}

/** 状态圆点：default / ok / warn / danger / time / hollow / mute */
export function StatusDot({ variant = 'default', className, ...props }) {
  return <span aria-hidden="true" className={cn('anibt-dot', variant !== 'default' && `anibt-dot-${variant}`, className)} {...props} />
}

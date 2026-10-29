import { cn } from './utils'

// 移植自 AniBT UI progress：轨道 + 指示条，value 为 null 时显示不确定动画
const TRACK_SIZE = { default: 'h-2.5', compact: 'h-1.5', lg: 'h-3.5' }
const TONE = {
  water: 'anibt-water shadow-[inset_0_1px_0_rgba(255,255,255,0.45)]',
  candy: 'anibt-candy shadow-[inset_0_1px_0_rgba(255,255,255,0.45)]',
  ok: 'bg-anibt-ok-fill shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]',
  danger: 'bg-anibt-danger-fill',
}

/**
 * AniBT 进度条
 * @param {number|null} value 当前值，null 表示不确定进度
 * @param {number} max 最大值
 * @param {'default'|'compact'|'lg'} size 尺寸
 * @param {'water'|'candy'|'ok'|'danger'} tone 色调
 */
export function Progress({ value = null, max = 100, size = 'default', tone = 'water', label, showValue = false, format, className, trackClassName, ...props }) {
  const indeterminate = value === null || value === undefined
  const percent = indeterminate ? 0 : Math.min(100, Math.max(0, (value / max) * 100))
  const track = (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={indeterminate ? undefined : value}
      data-slot="progress"
      data-tone={tone}
      className={cn('relative w-full overflow-hidden rounded-full bg-ab-muted shadow-[inset_0_0_0_1px_var(--ab-border)]', TRACK_SIZE[size], label || showValue ? trackClassName : className)}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className={cn(
          'h-full w-full rounded-full transition-transform duration-500 ease-anibt-out motion-reduce:transition-none',
          TONE[tone] || TONE.water,
          indeterminate && 'w-2/5 animate-anibt-indeterminate motion-reduce:animate-none'
        )}
        style={indeterminate ? undefined : { transform: `translateX(-${100 - percent}%)` }}
      />
    </div>
  )
  if (!label && !showValue) return track
  const asPercent = format ? format === 'percent' : max === 100
  const text = indeterminate ? '—' : asPercent ? `${Math.round(percent)}%` : `${value} / ${max}`
  return (
    <div data-slot="progress-group" className={cn('flex w-full flex-wrap items-baseline gap-x-3 gap-y-1.5', className)}>
      {label ? <span className="text-[13px] font-semibold text-anibt-ink2">{label}</span> : null}
      {showValue ? <span className="anibt-num ml-auto text-[12px] font-semibold text-ab-muted-foreground">{text}</span> : null}
      {track}
    </div>
  )
}

import { Spinner } from './Basic'
import { cn } from './utils'

// 移植自 AniBT UI button
const DISABLED_FLAT = 'disabled:not-aria-busy:bg-ab-muted'
const VARIANT = {
  default: 'anibt-btn-primary',
  secondary: `anibt-btn-raised ${DISABLED_FLAT}`,
  outline: `anibt-btn-raised ${DISABLED_FLAT}`,
  ghost: 'anibt-btn-ghost',
  destructive: `anibt-btn-danger ${DISABLED_FLAT}`,
  link: 'bg-transparent text-ab-accent-foreground hover:underline hover:underline-offset-4 h-auto px-0',
}
const SIZE = {
  default: 'h-8 gap-2 px-4',
  xs: 'h-6 gap-1 px-2 text-[12px]',
  sm: 'h-7 gap-1.5 px-3 text-[12px]',
  lg: 'h-9 gap-2 px-5 text-[14px]',
  icon: 'size-8',
  'icon-sm': 'size-7',
  'icon-lg': 'size-9',
}

/** AniBT 按钮；loading 时显示加载指示并禁用 */
export function Button({ variant = 'default', size = 'default', type = 'button', loading = false, disabled, className, children, ...props }) {
  return (
    <button
      type={type}
      data-variant={variant}
      aria-busy={loading || undefined}
      disabled={Boolean(disabled || loading)}
      className={cn(
        'relative inline-flex shrink-0 cursor-pointer items-center justify-center rounded-[10px] text-[13px] leading-none font-semibold whitespace-nowrap outline-hidden select-none aria-busy:cursor-wait disabled:pointer-events-none disabled:not-aria-busy:text-ab-muted-foreground disabled:not-aria-busy:shadow-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ab-ring focus-visible:outline-solid',
        VARIANT[variant] || VARIANT.default,
        variant === 'link' ? '' : SIZE[size],
        className
      )}
      {...props}
    >
      {loading ? <Spinner className="size-4" /> : null}
      {children}
    </button>
  )
}

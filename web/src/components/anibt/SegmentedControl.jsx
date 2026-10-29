import { useId, useRef } from 'react'
import { cn } from './utils'

/**
 * 分段控件（移植自 AniBT UI segmented-control，用原生 radiogroup 实现键盘导航）
 * @param {{value:string,label:string,icon?:React.ReactNode,disabled?:boolean}[]} options 选项
 */
export function SegmentedControl({ options, value, onValueChange, label, size = 'default', block = false, className, ...props }) {
  const labelId = useId()
  const refs = useRef([])
  const enabled = options.filter(o => !o.disabled)

  // 方向键在可用选项之间循环切换，与 radiogroup 语义一致
  const onKeyDown = e => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
    e.preventDefault()
    const idx = enabled.findIndex(o => o.value === value)
    const step = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1
    const next = enabled[(idx + step + enabled.length) % enabled.length]
    if (!next) return
    onValueChange?.(next.value)
    refs.current[options.indexOf(next)]?.focus()
  }

  const track = (
    <div
      role="radiogroup"
      aria-labelledby={label ? labelId : undefined}
      onKeyDown={onKeyDown}
      className={cn(
        'max-w-full items-center gap-0.5 rounded-[10px] bg-ab-muted p-1 shadow-[inset_0_0_0_1px_var(--ab-border)]',
        block ? 'grid w-full auto-cols-fr grid-flow-col' : 'inline-flex w-fit',
        !label && className
      )}
      {...props}
    >
      {options.map((option, i) => {
        const checked = option.value === value
        return (
          <button
            key={option.value}
            ref={el => (refs.current[i] = el)}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked || (!value && i === 0) ? 0 : -1}
            disabled={option.disabled}
            data-state={checked ? 'checked' : 'unchecked'}
            onClick={() => onValueChange?.(option.value)}
            className={cn(
              'inline-flex min-w-0 cursor-pointer touch-manipulation items-center justify-center gap-1 rounded-[6px] border-0 bg-transparent text-[12px] leading-none font-semibold whitespace-nowrap text-anibt-ink2 outline-hidden transition-[color,background-color,box-shadow] duration-200 ease-anibt-out',
              'hover:text-ab-foreground',
              'data-[state=checked]:bg-ab-card data-[state=checked]:text-ab-foreground data-[state=checked]:shadow-(--anibt-shadow-sticker)',
              'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ab-ring focus-visible:outline-solid',
              'disabled:cursor-not-allowed disabled:text-ab-muted-foreground',
              size === 'compact' ? 'h-6' : 'h-9 sm:h-7',
              'px-2.5 sm:px-2'
            )}
          >
            {option.icon ? <span aria-hidden="true" className="inline-flex">{option.icon}</span> : null}
            <span className="truncate">{option.label}</span>
            {option.count !== undefined ? <span className="anibt-num text-[11px] text-ab-muted-foreground">{option.count}</span> : null}
          </button>
        )
      })}
    </div>
  )
  if (!label) return track
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <span id={labelId} className="text-[13px] leading-5 font-semibold text-anibt-ink2">{label}</span>
      {track}
    </div>
  )
}

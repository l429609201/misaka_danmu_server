import { cn } from './utils'

// 移植自 AniBT UI card：panel 为纸面卡片，well 为下沉面板

/** 卡片容器 */
export function Card({ variant = 'panel', density = 'default', className, ...props }) {
  return (
    <div
      data-variant={variant}
      data-density={density}
      className={cn(
        'group/card flex flex-col gap-(--card-gap) py-(--card-py) [--card-gap:0.75rem] [--card-py:1rem] data-[density=compact]:[--card-gap:0.5rem] data-[density=compact]:[--card-py:0.75rem]',
        variant === 'well' ? 'anibt-well' : 'anibt-sheet',
        className
      )}
      {...props}
    />
  )
}

/** 卡片头部；stitched 时底部加缝线 */
export function CardHeader({ stitched = false, className, ...props }) {
  return (
    <div
      className={cn(
        'grid auto-rows-min items-start gap-1 px-4 group-data-[density=compact]/card:px-3 has-data-[slot=card-action]:grid-cols-[1fr_auto]',
        stitched && 'anibt-stitch-under pb-3 group-data-[density=compact]/card:pb-2',
        className
      )}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }) {
  return <div className={cn('text-[16px] leading-[1.4] font-extrabold text-ab-card-foreground', className)} {...props} />
}

export function CardDescription({ className, ...props }) {
  return <div className={cn('text-[12px] leading-normal text-ab-muted-foreground', className)} {...props} />
}

export function CardAction({ className, ...props }) {
  return <div data-slot="card-action" className={cn('col-start-2 row-span-2 row-start-1 flex items-center gap-2 self-start justify-self-end', className)} {...props} />
}

export function CardContent({ className, ...props }) {
  return <div className={cn('px-4 text-[14px] group-data-[density=compact]/card:px-3', className)} {...props} />
}

export function CardFooter({ stitched = false, className, ...props }) {
  return (
    <div
      className={cn('flex items-center gap-2 px-4 group-data-[density=compact]/card:px-3', stitched && 'anibt-stitch-over pt-3 group-data-[density=compact]/card:pt-2', className)}
      {...props}
    />
  )
}

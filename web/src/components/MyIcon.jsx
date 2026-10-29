import classNames from 'classnames'
import ovaSvg from '@/assets/icons/ova.svg?raw'
import varietySvg from '@/assets/icons/variety.svg?raw'

// iconfont 字体里没有的图标，用本地 SVG 补充；SVG 统一 currentColor 描边，可跟随 color 着色
const SVG_ICONS = {
  ova: ovaSvg,
  variety: varietySvg,
}

export const MyIcon = ({ size, className, color, icon }) => {
  const style = {
    width: `${size}px`,
    height: `${size}px`,
    fontSize: `${size}px`,
    color: color
      ? color.startsWith('--')
        ? `var(${color})`
        : `${color}`
      : undefined,
  }
  const svg = SVG_ICONS[icon]
  if (svg) {
    return (
      <i
        className={classNames('my-svg-icon leading-none inline-block', className)}
        style={style}
        // SVG 为仓库内静态资源，不含用户输入
        dangerouslySetInnerHTML={{ __html: svg }}
      ></i>
    )
  }
  return (
    <i
      className={classNames(
        'iconfont leading-none inline-block',
        `icon-${icon}`,
        className
      )}
      style={style}
    ></i>
  )
}

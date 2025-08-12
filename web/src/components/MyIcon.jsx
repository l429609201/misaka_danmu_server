import { useNavigate } from 'react-router-dom'
import { RoutePaths } from '../general/RoutePaths.jsx'
import classNames from 'classnames'

export const MyIcon = ({ size, className, color, icon }) => {
  return (
    <i
      className={classNames(
        'iconfont leading-none inline-block',
        `icon-${icon}`,
        className
      )}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        fontSize: `${size}px`,
        color: color
          ? color.startsWith('--')
            ? `var(${color})`
            : `${color}`
          : undefined,
      }}
    ></i>
  )
}

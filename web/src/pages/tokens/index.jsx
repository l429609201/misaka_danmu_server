import { Token } from './components/Token'
import { Domain } from './components/Domain'
import { Ua } from './components/Ua'

export const Tokens = () => {
  return (
    <>
      <Token />
      <Domain />
      <Ua />
      <p>
        本项目参考了
        <a
          href="https://api.dandanplay.net/swagger/index.html"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          dandanplayapi
        </a>
        ，同时增加了使用访问令牌管理弹幕api,支持
        <a
          href="https://t.me/yamby_release"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          yamby
        </a>
        、
        <a
          href="https://play.google.com/store/search?q=hills&c=apps"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          hills
        </a>
        、
        <a
          href="https://apps.microsoft.com/detail/9NB0H051M4V4"
          target="_blank"
          className="text-primary"
          rel="noopener noreferrer"
        >
          小幻影视
        </a>
        。
      </p>
    </>
  )
}

import AutoMatchSetting from '../setting/components/AutoMatchSetting'

/**
 * AI 独立一级页面：原「设置 → AI」拆分而来，
 * 子页（连接配置/自动匹配/识别增强/御坂助手/御坂技能/使用统计）由 ?key= 切换。
 */
export const AIPage = () => (
  <div className="my-6">
    <AutoMatchSetting />
  </div>
)

export default AIPage

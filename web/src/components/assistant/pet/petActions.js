/**
 * 看板娘动作表
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 agentPetActions.ts。
 * 集中定义：所有情绪状态、每个状态对应的立绘图片、气泡文案、是否为瞬时状态。
 * 新增/修改表情只需改这里，渲染器与状态机都不用动。
 *
 * 图片降级策略：某些状态图可能尚未准备好（如 talking/avatar/empty），
 * 此处用 import 兜底——缺失的图统一回退到 idle 图，保证运行不报错。
 */

// 已就绪的图片（存在于 assets/assistant/ 下）
import idleImg from '@/assets/assistant/misaka-idle.png'
import happyImg from '@/assets/assistant/misaka-happy.png'
import sadImg from '@/assets/assistant/misaka-sad.png'
import surprisedImg from '@/assets/assistant/misaka-surprised.png'
import thinkingImg from '@/assets/assistant/misaka-thinking.png'
import talkingImg from '@/assets/assistant/misaka-talking.png'
import avatarImg from '@/assets/assistant/misaka-avatar.png'

// idle 的分层素材：本体（已把眼珠涂成眼白）+ 独立眼睛层，用于"眼睛跟随鼠标 + 眨眼"
import idleBodyImg from '@/assets/assistant/parts/人物本体.png'
import idleEyesImg from '@/assets/assistant/parts/眼睛.png'

/**
 * idle 分层配置（在 eye-demo.html 中实测标定）
 * bodyImg/eyesImg：两张分层素材
 * eyeWidthRatio：眼睛小图宽 / 本体图宽 = 510 / 1537，用于按舞台尺寸等比换算眼睛渲染宽
 * eyeAspect：眼睛小图 高/宽 = 148 / 510
 * cx/cy：眼睛中心相对舞台的百分比定位（demo 实测：34.5 / 34.5）
 * followAmp：跟随鼠标的最大位移（px）。Q版小尺寸下 4 太夸张，降到 1.5 只微微瞟
 */
export const IDLE_LAYERS = {
  bodyImg: idleBodyImg,
  eyesImg: idleEyesImg,
  eyeWidthRatio: 510 / 1537,
  eyeAspect: 148 / 510,
  cx: 34.5,
  cy: 34.5,
  followAmp: 1.5,
}

/** 所有合法情绪状态（渲染器/状态机的唯一真源） */
export const PET_STATES = ['idle', 'thinking', 'happy', 'sad', 'surprised', 'talking']

/** 默认状态 */
export const DEFAULT_STATE = 'idle'

/**
 * 瞬时状态：切到这些状态后会在一段时间后自动回落到 idle。
 * happy（成功）、surprised（报错/惊讶）、sad（失败）都是"表演完就恢复"的表情。
 */
export const TRANSIENT_STATES = ['happy', 'sad', 'surprised']

/**
 * 状态 -> 视觉/文案配置
 * img：立绘图片；bubble：idle 时可展示的气泡文案；label：状态中文名（用于面板状态栏）
 */
// label/bubble 改为 i18n key，运行时用 t() 取；无 t 时回退中文默认值
export const PET_ACTIONS = {
  idle: { img: idleImg, bubbleKey: 'petIdleBubble', labelKey: 'petIdle', label: '待命中' },
  thinking: { img: thinkingImg, bubbleKey: 'petThinkingBubble', labelKey: 'petThinking', label: '思考中' },
  happy: { img: happyImg, bubbleKey: 'petHappyBubble', labelKey: 'petHappy', label: '完成' },
  sad: { img: sadImg, bubbleKey: 'petSadBubble', labelKey: 'petSad', label: '失败' },
  surprised: { img: surprisedImg, bubbleKey: 'petSurprisedBubble', labelKey: 'petSurprised', label: '异常' },
  talking: { img: talkingImg, bubbleKey: '', labelKey: 'petTalking', label: '回复中' },
}

/** 头像图（面板顶部小圆头像用） */
export const AVATAR_IMG = avatarImg

/** 便捷取图：拿不到时回退默认状态图 */
export function getPetImage(state) {
  return (PET_ACTIONS[state] || PET_ACTIONS[DEFAULT_STATE]).img
}

/** 便捷取状态标签（传入 t 则返回 i18n 文案，否则回退中文默认） */
export function getPetLabel(state, t) {
  const action = PET_ACTIONS[state] || PET_ACTIONS[DEFAULT_STATE]
  if (t && action.labelKey) return t(`assistant.${action.labelKey}`)
  return action.label
}

/** 便捷取状态气泡文案（传入 t 则返回 i18n 文案，否则空） */
export function getPetBubble(state, t) {
  const action = PET_ACTIONS[state] || PET_ACTIONS[DEFAULT_STATE]
  if (t && action.bubbleKey) return t(`assistant.${action.bubbleKey}`)
  return ''
}

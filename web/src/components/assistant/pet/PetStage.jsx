/**
 * 形象舞台（PetStage）
 * ------------------------------------------------------------
 * 参考 MoviePilot 的 AgentPetStage.vue。
 * 职责：作为看板娘形象的容器，负责"伪动效"（呼吸浮动、说话时轻微弹动），
 * 并把当前 state 交给可插拔渲染器绘制。切换 Live2D 时只需换掉这里的渲染器。
 */
import { ImageRenderer } from './renderers/ImageRenderer'
import { LayeredRenderer } from './renderers/LayeredRenderer'

/**
 * @param {string} state 当前情绪状态
 * @param {number} size 渲染尺寸
 * @param {boolean} floating 是否启用呼吸浮动（右下角悬浮时开，面板内可关）
 */
export function PetStage({ state = 'idle', size = 120, floating = true }) {
  // talking 时叠加"说话弹动"，其余状态用"呼吸浮动"
  const animClass = state === 'talking' ? 'pet-anim-talk' : floating ? 'pet-anim-breathe' : ''

  return (
    <div className={`assistant-pet-stage ${animClass}`}>
      {/* 渲染器可插拔：idle 用分层渲染（眼睛跟随+眨眼），其余情绪用静态图渲染器 */}
      {state === 'idle' ? (
        <LayeredRenderer size={size} />
      ) : (
        <ImageRenderer state={state} size={size} />
      )}
    </div>
  )
}

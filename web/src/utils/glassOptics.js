/**
 * 液态玻璃光学参数（移植自 MoviePilot-Frontend `src/utils/glassOptics.ts` 的标准 CSS 档）。
 *
 * why：MP 的形变/流动/采样平移三项只作用于其 WebGL 实时渲染器，本项目不引入 WebGL，
 * 因此只移植标准档实际生效的部分：三种材质 × 通透度 / 透射亮度 / 反射亮度，
 * 以及把它们换算成 CSS 变量的响应曲线。
 */

export const GLASS_STRENGTH_MIN = 0
export const GLASS_STRENGTH_MAX = 100
export const GLASS_STRENGTH_DEFAULT = 50
const GLASS_REFERENCE_STRENGTH = 70

export const GLASS_APPEARANCES = ['clear', 'tinted', 'frosted']
export const GLASS_PARAM_KEYS = ['transparency', 'transmission', 'reflection']

// MP 标准档（css:natural）的预设值
const GLASS_NATURAL_PRESETS = {
  clear:   { transparency: 52, transmission: 67, reflection: 42 },
  tinted:  { transparency: 37, transmission: 65, reflection: 46 },
  frosted: { transparency: 43, transmission: 60, reflection: 37 },
}

// 响应曲线锚点：在 0/20/50/70/85/100 之间平滑插值
const GLASS_RESPONSE_STOPS = [0, 20, 50, 70, 85, 100]
const GLASS_SURFACE_DENSITY = {
  clear:   [1, 0.88, 0.62, 0.42, 0.26, 0.18],
  tinted:  [1, 0.92, 0.72, 0.52, 0.39, 0.3],
  frosted: [1, 0.9, 0.7, 0.52, 0.36, 0.22],
}
const GLASS_TINT_DENSITY = [1, 0.9, 0.65, 0.48, 0.36, 0.28]
const GLASS_CSS_FROST_BLUR_SURFACE = [64, 52, 36, 24, 15, 8]

// 本项目原液态玻璃样式对应的基线：密度 0.62、模糊 36px 时倍率为 1（即原效果）
const BASE_SURFACE_DENSITY = 0.62
const BASE_FROST_BLUR = 36
// why：默认（透明·预设）≈ 原液态玻璃效果；磨砂靠更重的扩散呈现厚度
const APPEARANCE_BLUR_FACTOR = { clear: 1, tinted: 1, frosted: 1.4 }

/**
 * 把滑块输入收敛到 0–100 的整数，非法值回落到默认。
 * @param {unknown} value 原始值
 * @param {number} [fallback] 非法时的回落值
 * @returns {number}
 */
export function normalizeGlassStrength(value, fallback = GLASS_STRENGTH_DEFAULT) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  return Math.min(GLASS_STRENGTH_MAX, Math.max(GLASS_STRENGTH_MIN, Math.round(value)))
}

/**
 * 返回某材质的标准预设副本，调用方可安全修改。
 * @param {string} appearance 材质
 * @returns {{transparency: number, transmission: number, reflection: number}}
 */
export function getGlassPreset(appearance) {
  return { ...(GLASS_NATURAL_PRESETS[appearance] || GLASS_NATURAL_PRESETS.clear) }
}

/** 相邻锚点之间做 smoothstep 插值，避免滑块经过锚点时出现折线。 */
function interpolate(value, anchors) {
  const normalized = normalizeGlassStrength(value)
  const upper = GLASS_RESPONSE_STOPS.findIndex(stop => normalized <= stop)
  if (upper <= 0) return anchors[0]
  const lower = upper - 1
  const t = (normalized - GLASS_RESPONSE_STOPS[lower]) / (GLASS_RESPONSE_STOPS[upper] - GLASS_RESPONSE_STOPS[lower])
  const smooth = t * t * (3 - 2 * t)
  return anchors[lower] + (anchors[upper] - anchors[lower]) * smooth
}

/** 透射亮度：70 为参考点（=1），低区间压暗，高区间窄幅提亮。 */
function getTransmissionBrightness(value) {
  const normalized = normalizeGlassStrength(value)
  const transmission = normalized <= GLASS_REFERENCE_STRENGTH
    ? normalized / GLASS_REFERENCE_STRENGTH
    : 1 + 0.3 * ((normalized - GLASS_REFERENCE_STRENGTH) / (GLASS_STRENGTH_MAX - GLASS_REFERENCE_STRENGTH)) ** 1.2
  if (transmission <= 1) return 0.84 + 0.16 * transmission ** 1.05
  return 1 + 0.08 * ((transmission - 1) / 0.3) ** 1.1
}

/**
 * 把材质与三项参数换算成 index.css 消费的 CSS 变量。
 * @param {{appearance: string, transparency: number, transmission: number, reflection: number}} params
 * @returns {Record<string, string>} CSS 变量名 → 值
 */
export function getGlassCssVariables({ appearance, transparency, transmission, reflection }) {
  const surfaceDensity = interpolate(transparency, GLASS_SURFACE_DENSITY[appearance] || GLASS_SURFACE_DENSITY.clear)
  const blur = interpolate(transparency, GLASS_CSS_FROST_BLUR_SURFACE)
  const blurFactor = APPEARANCE_BLUR_FACTOR[appearance] || 1
  return {
    '--glass-opacity-scale': (surfaceDensity / BASE_SURFACE_DENSITY).toFixed(3),
    '--glass-blur-scale': ((blur / BASE_FROST_BLUR) * blurFactor).toFixed(3),
    '--glass-tint-density': interpolate(transparency, GLASS_TINT_DENSITY).toFixed(3),
    '--glass-transmission-brightness': getTransmissionBrightness(transmission).toFixed(3),
    '--glass-reflection': (normalizeGlassStrength(reflection) / GLASS_STRENGTH_MAX).toFixed(3),
  }
}

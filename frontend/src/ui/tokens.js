/* Ant Design needs JS token values. The actual palette lives in
 * design-tokens.css; these references keep the component layer on the same
 * semantic source instead of introducing a second visual system.
 */

/* antd 需要能解析的主色字符串来派生 hover、选中背景等衍生色；CSS var 引用无法参与
 * 计算（会退化成深色默认）。启动时把 var 解析成实际值，tokens.css 仍是唯一来源。 */
const CSS_VAR = /^var\((--[\w-]+)\)$/;

export function resolveColor(value) {
  const match = typeof value === "string" ? value.match(CSS_VAR) : null;
  if (!match) return value;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || value;
}

export const palette = {
  canvas: "var(--color-canvas)",
  sidebar: "var(--color-sidebar)",
  surface: "var(--color-surface)",
  raised: "var(--color-surface-raised)",
  muted: "var(--color-surface-muted)",
  well: "var(--color-well)",
  text: "var(--color-text)",
  textSecondary: "var(--color-text-secondary)",
  textFaint: "var(--color-text-faint)",
  border: "var(--color-border)",
  borderStrong: "var(--color-border-strong)",
  brand: "var(--color-brand)",
  brandStrong: "var(--color-brand-strong)",
  brandSoft: "var(--color-brand-soft)",
  code: "var(--color-code)",
  codeSoft: "var(--color-code-soft)",
  business: "var(--color-business)",
  businessSoft: "var(--color-business-soft)",
  requirement: "var(--color-requirement)",
  requirementSoft: "var(--color-requirement-soft)",
  danger: "var(--color-danger)",
  dangerSoft: "var(--color-danger-soft)",
  tag: "var(--color-text-secondary)",
  tagSoft: "var(--color-surface-muted)",
  shadow1: "var(--shadow-1)",
  shadow2: "var(--shadow-2)",
  focus: "var(--component-focus-ring)",
};

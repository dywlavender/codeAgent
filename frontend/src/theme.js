/*
 * Code Atlas · Atelier 配色
 * 与 design/mockups/theme-atelier.html 保持一致：暖纸色画布、青绿品牌色、
 * 三源证据（代码/业务/需求）各有专属色相。
 */

export const palette = {
  canvas: "#F4F2ED",
  sidebar: "#EBE7E0",
  surface: "#FCFBF9",
  raised: "#FFFFFF",
  muted: "#EDEAE4",
  well: "#F7F5F1",

  text: "#211E1A",
  textSecondary: "#7E786E",
  textFaint: "#A8A297",
  border: "#E0DCD5",
  borderStrong: "#C9C4BA",

  brand: "#1B6B65",
  brandStrong: "#104440",
  brandSoft: "#E6EFEC",

  code: "#2F6382",
  codeSoft: "#E9F0F5",
  business: "#1B6B65",
  businessSoft: "#E6EFEC",
  requirement: "#8A5F28",
  requirementSoft: "#F6EEE0",
  danger: "#A53831",
  dangerSoft: "#F8E9E7",
  tag: "#7E786E",
  tagSoft: "#EDEAE4",

  shadow1: "0 1px 2px rgba(33,30,26,.05), 0 0 0 1px rgba(33,30,26,.045)",
  shadow2: "0 2px 4px rgba(33,30,26,.04), 0 10px 28px rgba(33,30,26,.09)",
  focus: "0 0 0 3px rgba(27,107,101,.16)",
};

export const SOURCES = {
  CODE: { label: "代码", color: palette.code, soft: palette.codeSoft },
  BUSINESS: { label: "业务知识", color: palette.business, soft: palette.businessSoft },
  REQUIREMENT: { label: "需求依据", color: palette.requirement, soft: palette.requirementSoft },
};

export function sourceMeta(type) {
  return SOURCES[type] || { label: type || "未知", color: palette.tag, soft: palette.tagSoft };
}

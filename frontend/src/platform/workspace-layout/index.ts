/**
 * P01 · 工作区布局导出入口。
 */
export { default as WorkspaceLayout } from './WorkspaceLayout.vue'

export {
  buildWorkspaceLayout,
  validateLayoutContract,
  PLATFORM_REGION_ROLES,
  REQUIRED_CORE_REGIONS,
  COLLAPSE_BEHAVIOURS,
  type WorkspaceLayoutShape,
  type RegionSpecLite,
  type ResponsiveRuleLite,
  type PlatformRegionRole,
  type CollapseBehaviour,
} from './layoutContract'

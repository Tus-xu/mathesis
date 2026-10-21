/**
 * P01 · App Shell 导出入口。
 */
export { default as WorkspaceShell } from './WorkspaceShell.vue'

export {
  deriveWorkspaceChoices,
  isCapabilityPresent,
  registerWorkspaceHome,
  resolveWorkspaceHome,
  PLATFORM_WORKSPACES,
  type WorkspaceChoice,
  type WorkspaceKey,
  type WorkspaceHomeRegistration,
} from './workspaceManifest'

export {
  detectSecurityVersionDrift,
  requiresShellInvalidation,
  type SecuritySnapshot,
  type SecurityDriftVerdict,
} from './securityVersion'

export {
  resolvePlatformNavigation,
  resolveTargetWorkspace,
  DENIED_PATH,
  type PlatformNavigationInput,
  type PlatformNavigationResult,
} from './navigation'

export {
  resolveLegacyUrl,
  isLegacyUrl,
  LEGACY_URL_ALIASES,
} from './legacyUrls'

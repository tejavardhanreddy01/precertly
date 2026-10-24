import { createContext, useContext, type ComponentType } from "react";

/**
 * What the live module hands the app. In a recorded-only build there is no such module and the app
 * is given none; the live pages, their route and their links then do not exist. The type is all
 * the app imports from the live code, so nothing live is bundled unless the build asks for it.
 */
export interface LiveFeature {
  Screen: ComponentType<{ caseId: string | undefined }>;
}

export const LiveContext = createContext(false);

/** True when this build has live mode (the Live nav item, the Run live link and the route). */
export function useLiveEnabled(): boolean {
  return useContext(LiveContext);
}

// Build-time constants from vite.config.ts (`define`). With live mode off they are the literals
// false and "", so code behind `if (__LIVE_ENABLED__)` is removed from the build.
declare const __LIVE_ENABLED__: boolean;
declare const __LIVE_API_ORIGIN__: string;
declare const __LIVE_HOSTED_UI_ORIGIN__: string;
declare const __LIVE_CLIENT_ID__: string;

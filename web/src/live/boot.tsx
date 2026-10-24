// The live module's entry. main.tsx imports it only when the build has live mode (the
// __LIVE_ENABLED__ constant), so a recorded-only build has no copy of any of this. It builds the one
// session and the one origin-guarded client for the page and starts nothing: no request is made
// here. The only work at load is reading a sign-in return, which does nothing for an ordinary
// address.
import { createApiClient } from "../lib/live/http";
import { browserSessionDeps, LiveSession } from "../lib/live/session";
import type { LiveFeature } from "../lib/live-feature";
import { LiveScreen } from "./LiveScreen";

const session = new LiveSession(
  browserSessionDeps({ hostedUiOrigin: __LIVE_HOSTED_UI_ORIGIN__, clientId: __LIVE_CLIENT_ID__ }),
);
const client = createApiClient({
  apiOrigin: __LIVE_API_ORIGIN__,
  session,
  fetch: window.fetch.bind(window),
});

// Cleans the address and, when the address is a sign-in return, exchanges the code once. The first
// lines of it run now, before the app renders, so a code never sits in the address bar.
void session.handleCallback();

// Back from the hosted UI can restore this page from the back/forward cache, mid-sign-in.
window.addEventListener("pageshow", (event) => session.pageShown(event.persisted));

export const liveFeature: LiveFeature = {
  Screen: ({ caseId }) => <LiveScreen caseId={caseId} session={session} client={client} />,
};

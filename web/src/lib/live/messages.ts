// Every sentence the live pages show about a failure is written here. Nothing the server or the
// identity provider sends as error text is ever displayed (it could carry a host or a statement);
// only the run's own fixed `message` from the API is, and only after it has passed the guards.

export const MESSAGES = {
  signInCancelled: "Sign-in was not completed. You can try again.",
  signInUnverified: "Sign-in could not be verified, so it was stopped. Please try again.",
  signInFailed: "Sign-in failed. Please try again.",
  signInUnavailable: "Sign-in needs session storage, which this browser is not allowing.",
  signedOutNotice: "You are signed out.",
  expired: "Your sign-in has expired.",
  unexpectedResponse: "The live environment sent an unexpected response, so the run was stopped.",
  notAvailableCase: "This case is not available for a live run.",
  anotherRunInProgress: "Another live run is in progress. Try again when it has finished.",
  runGone: "This run is no longer available. The live environment may have been stopped.",
  connectionLost:
    "The connection to the live environment was lost. The run may still be finishing on the server, but its result cannot be recovered: results are not stored, and this page keeps the run id in memory only.",
  rateLimited: "The live environment is busy. Waiting a moment before asking again.",
  couldNotStart: "The run could not be started.",
  down: "The live environment is not running. The recorded runs on this site are unchanged.",
} as const;

export const NOTICES = {
  synthetic: "All data is synthetic; no real patient is involved.",
} as const;

/** The stages of a run, in order, with the words shown for each. */
export const STAGE_LABELS = {
  waking_database: "Waking the database (about a minute and a quarter from cold)",
  checking_limits: "Checking the daily limits",
  ingesting: "Ingesting the chart",
  judging: "Judging the criteria",
} as const;

/** Short names for the stages, for the timings after a run. */
export const STAGE_NAMES = {
  waking_database: "Waking the database",
  checking_limits: "Checking the daily limits",
  ingesting: "Ingesting the chart",
  judging: "Judging the criteria",
} as const;

/** Shown if a result's own notice came back empty; the server's notice is shown whenever it sends one. */
export const NOTICE_FALLBACK =
  "Single unregistered run, not part of the eval, no accuracy claim. Results are not stored.";

import { IconAlert } from "./icons";

export function ErrorScreen({ title, message }: { title: string; message: string }) {
  return (
    <section
      role="alert"
      className="mx-auto mt-4 max-w-text rounded-lg border border-danger-deep bg-surface p-5 shadow-pop"
    >
      <h1 className="flex items-center gap-2 text-xl font-bold text-notmet-fg">
        <IconAlert size={20} className="shrink-0 text-danger" />
        {title}
      </h1>
      <p className="mt-2">{message}</p>
      <p className="mt-2 text-sm text-ink-3">
        The site shows recorded files only. Nothing here runs a model or contacts a server other than the one that
        served this page.
      </p>
      <p className="mt-3">
        <a className="font-medium text-accent underline underline-offset-2" href="#/cases">
          Back to the case list
        </a>
      </p>
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p role="status" className="p-4 text-ink-3">
      Loading {what}…
    </p>
  );
}

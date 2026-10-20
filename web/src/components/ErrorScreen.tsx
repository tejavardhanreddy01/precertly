export function ErrorScreen({ title, message }: { title: string; message: string }) {
  return (
    <section role="alert" className="mx-auto max-w-2xl rounded border-2 border-red-900 bg-white p-4">
      <h1 className="text-xl font-bold text-red-950">{title}</h1>
      <p className="mt-2">{message}</p>
      <p className="mt-2 text-sm text-slate-700">
        The site shows recorded files only. Nothing here runs a model or contacts a server other than the one that
        served this page.
      </p>
      <p className="mt-3">
        <a className="underline" href="#/cases">
          Back to the case list
        </a>
      </p>
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p role="status" className="p-4 text-slate-700">
      Loading {what}…
    </p>
  );
}

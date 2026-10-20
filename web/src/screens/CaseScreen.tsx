import { CaseView } from "../components/CaseView";
import { ErrorScreen, Loading } from "../components/ErrorScreen";
import { useLoaded, useStore } from "../lib/data";
import { useIndex } from "../lib/index-context";
import type { Route } from "../lib/router";
import { useTitle } from "../lib/title";

export function CaseScreen({ id, route }: { id: string; route: Route }) {
  const store = useStore();
  const { cases } = useIndex();
  const row = cases.find((c) => c.id === id);
  const loaded = useLoaded(`case:${id}`, () => store.case(id));
  useTitle(row ? `Case ${id}` : "Case not found");

  if (!row) {
    return <ErrorScreen title="Case not found" message={`There is no case called "${id}" in the recorded data.`} />;
  }
  if (loaded.status === "loading") return <Loading what={`case ${id}`} />;
  if (loaded.status === "error") {
    return <ErrorScreen title={`Case ${id} could not be loaded`} message={loaded.message} />;
  }
  return <CaseView file={loaded.value} row={row} route={route} />;
}

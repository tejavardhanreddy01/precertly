import { useEffect, useMemo } from "react";
import { ErrorScreen, Loading } from "./components/ErrorScreen";
import { Layout } from "./components/Layout";
import { DataStore, StoreContext, useLoaded, useStore } from "./lib/data";
import { IndexContext } from "./lib/index-context";
import { isKnownRoute, parseHash, useHash } from "./lib/router";
import { useTitle } from "./lib/title";
import { About } from "./screens/About";
import { CaseScreen } from "./screens/CaseScreen";
import { CasesList } from "./screens/CasesList";
import { Landing } from "./screens/Landing";
import { PolicyIndex, PolicyScreen } from "./screens/PolicyScreen";

function NotFound() {
  useTitle("Page not found");
  return (
    <ErrorScreen
      title="Page not found"
      message="There is nothing at this address. Use the links at the top to find a case, a policy or the method."
    />
  );
}

function Routes() {
  const route = parseHash(useHash());
  const [first, second] = route.path;
  useEffect(() => {
    window.scrollTo?.({ top: 0 });
  }, [first, second]);

  if (!isKnownRoute(route.path)) return <NotFound />;
  if (first === undefined) return <Landing />;
  if (first === "cases") return second ? <CaseScreen key={second} id={second} route={route} /> : <CasesList />;
  if (first === "policies") return second ? <PolicyScreen key={second} id={second} route={route} /> : <PolicyIndex />;
  return <About />;
}

function Shell() {
  const store = useStore();
  const path = parseHash(useHash()).path;
  // A case is a workspace that fills the viewport; every other page scrolls.
  const workspace = path[0] === "cases" && path.length === 2;
  const loaded = useLoaded("index", () => store.index());

  if (loaded.status === "loading") {
    return (
      <Layout ready={false}>
        <Loading what="the recorded data" />
      </Layout>
    );
  }
  if (loaded.status === "error") {
    return (
      <Layout ready={false}>
        <ErrorScreen title="The recorded data could not be loaded" message={loaded.message} />
      </Layout>
    );
  }
  return (
    <IndexContext.Provider value={loaded.value}>
      <Layout workspace={workspace}>
        <Routes />
      </Layout>
    </IndexContext.Provider>
  );
}

export function App({ store }: { store?: DataStore }) {
  const value = useMemo(() => store ?? new DataStore(), [store]);
  return (
    <StoreContext.Provider value={value}>
      <Shell />
    </StoreContext.Provider>
  );
}

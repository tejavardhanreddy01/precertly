import { useEffect, useMemo } from "react";
import { ErrorScreen, Loading } from "./components/ErrorScreen";
import { Layout } from "./components/Layout";
import { DataStore, StoreContext, useLoaded, useStore } from "./lib/data";
import { IndexContext } from "./lib/index-context";
import { parseHash, useHash } from "./lib/router";
import { About } from "./screens/About";
import { CaseScreen } from "./screens/CaseScreen";
import { CasesList } from "./screens/CasesList";
import { Landing } from "./screens/Landing";
import { PolicyIndex, PolicyScreen } from "./screens/PolicyScreen";

function NotFound() {
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

  if (route.path.length === 0) return <Landing />;
  if (first === "cases" && route.path.length === 1) return <CasesList />;
  if (first === "cases" && second && route.path.length === 2) {
    return <CaseScreen key={second} id={second} route={route} />;
  }
  if (first === "policies" && route.path.length === 1) return <PolicyIndex />;
  if (first === "policies" && second && route.path.length === 2) {
    return <PolicyScreen key={second} id={second} route={route} />;
  }
  if (first === "about" && route.path.length === 1) return <About />;
  return <NotFound />;
}

function Shell() {
  const store = useStore();
  const loaded = useLoaded("index", () => store.index());

  if (loaded.status === "loading") {
    return (
      <Layout withNotices={false}>
        <Loading what="the recorded data" />
      </Layout>
    );
  }
  if (loaded.status === "error") {
    return (
      <Layout withNotices={false}>
        <ErrorScreen title="The recorded data could not be loaded" message={loaded.message} />
      </Layout>
    );
  }
  return (
    <IndexContext.Provider value={loaded.value}>
      <Layout>
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

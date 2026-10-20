import { useEffect } from "react";
import { tourHref } from "../content/tour";
import { Loading } from "../components/ErrorScreen";
import { replaceHash } from "../lib/router";

/** The root route: sends the reader to the first step of the tour. */
export function Landing() {
  useEffect(() => {
    replaceHash(tourHref(1));
  }, []);
  return (
    <div>
      <Loading what="the tour" />
      <p>
        <a className="underline" href={tourHref(1)}>
          Take the 4-step tour
        </a>
      </p>
    </div>
  );
}

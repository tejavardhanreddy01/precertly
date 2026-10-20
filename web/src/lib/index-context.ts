import { createContext, useContext } from "react";
import type { Index } from "./types";

export const IndexContext = createContext<Index | null>(null);

export function useIndex(): Index {
  const index = useContext(IndexContext);
  if (!index) throw new Error("IndexContext is missing");
  return index;
}

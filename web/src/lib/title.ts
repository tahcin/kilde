import { useEffect } from "react";
const HOME = "Kilde: Norwegian companies, with the source of every fact";
/** Sets the tab title; null restores the home title. */
export function useTitle(title: string | null) {
  useEffect(() => { document.title = title ? `${title} · Kilde` : HOME; }, [title]);
}

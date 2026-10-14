import { Link } from "react-router";
import { useTitle } from "../lib/title";

export default function NotFound() {
  useTitle("Page not found");
  return (
    <div className="wrap py-28 text-center">
      <h1 className="display text-[2.4rem] text-ink">This page does not exist.</h1>
      <p className="mt-3 text-ink-2">The address may be mistyped, or the company is not among the profiles published here.</p>
      <Link to="/" className="mt-7 inline-flex rounded-full bg-brand px-5 py-2.5 text-[15px] font-medium text-on hover:bg-brand-2">Search companies</Link>
    </div>
  );
}

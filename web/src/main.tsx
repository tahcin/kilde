import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { MotionConfig } from "motion/react";
import { Shell } from "./components/Shell";
import { CompareProvider } from "./lib/compare";
import { AskProvider } from "./components/AskLauncher";
import { loadIndex } from "./lib/data";
import Home from "./pages/Home";
import NotFound from "./pages/NotFound";
import "./index.css";

const Company = lazy(() => import("./pages/Company"));
const Compare = lazy(() => import("./pages/Compare"));
const How = lazy(() => import("./pages/How"));
const wait = <div className="h-[80vh]" />;

// A fresh load starts at the top; back and forward still return to where you were.
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

// Search needs the index on every page: start fetching it now.
void loadIndex().catch(() => { /* pages show their own state */ });

const router = createBrowserRouter([
  {
    path: "/", element: <Shell />, errorElement: <NotFound />, children: [
      { index: true, element: <Home /> },
      { path: "c/:org", element: <Suspense fallback={wait}><Company /></Suspense> },
      { path: "compare", element: <Suspense fallback={wait}><Compare /></Suspense> },
      { path: "how", element: <Suspense fallback={wait}><How /></Suspense> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <CompareProvider><AskProvider><RouterProvider router={router} /></AskProvider></CompareProvider>
    </MotionConfig>
  </StrictMode>,
);

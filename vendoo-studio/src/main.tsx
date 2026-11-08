import { installErrorReports } from "./api/errorReports";
import React from "react";
import ReactDOM from "react-dom/client";
import { focusManager, QueryClientProvider } from "@tanstack/react-query";
import { createStudioQueryClient } from "./api/queryClient";
import { App } from "./app/App";
import { AnchoredToastProvider, ToastProvider } from "./components/ui/toast";
import { installSoftwareKeyboard } from "./lib/softwareKeyboard";
import { markMacApp } from "./lib/macApp";
import { WorkspaceRuntimeProvider, desktopRuntime } from "./app/runtime";
import { MacAccountButton } from "./app/MacAccountButton";
import { applyTheme } from "./theme";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { installChunkReload } from "./lib/chunkReload";
import { installUnsavedWorkGuard } from "./lib/unsavedWork";
import "./styles/app.css";

installErrorReports();
applyTheme();

markMacApp();
installSoftwareKeyboard(window);
installChunkReload();
installUnsavedWorkGuard(window);

// Polls slow down while Studio sits behind another window (see pollMs), and the
// desktop webview never reports itself hidden, so also refetch on window focus.
focusManager.setEventListener((onFocus) => {
  const listener = () => onFocus();
  window.addEventListener("visibilitychange", listener, false);
  window.addEventListener("focus", listener, false);
  return () => {
    window.removeEventListener("visibilitychange", listener);
    window.removeEventListener("focus", listener);
  };
});

const queryClient = createStudioQueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkspaceRuntimeProvider value={{ ...desktopRuntime, accountControls: <MacAccountButton /> }}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <AnchoredToastProvider>
            <ErrorBoundary label="Anteroom" className="h-dvh">
              <App />
            </ErrorBoundary>
          </AnchoredToastProvider>
        </ToastProvider>
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>
  </React.StrictMode>
);

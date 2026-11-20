import { reportError } from "../api/errorReports";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCwIcon } from "lucide-react";
import { Button } from "./ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";
import { isChunkLoadError } from "../lib/chunkReload";
import { commitUnsavedWork } from "../lib/unsavedWork";

type Props = {
  /** What failed, in the seller's words: "This listing", "Chat", "This page", "Anteroom". */
  label: string;
  /** A change (another listing, another page) clears the error and tries again. */
  resetKey?: unknown;
  className?: string;
  children: ReactNode;
};

type State = { error: Error | null; resetKey: unknown };

function reloadPage() {
  commitUnsavedWork();
  window.location.reload();
}

/**
 * Keeps one broken screen from blanking the whole app. The rest of the
 * workspace stays usable; the failed part offers Try again and Reload.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (Object.is(props.resetKey, state.resetKey)) return null;
    return { error: null, resetKey: props.resetKey };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    reportError("render", error);
    console.error(`[ErrorBoundary] ${this.props.label} failed`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const update = isChunkLoadError(error);
    return (
      <Empty role="alert" size="compact" className={this.props.className}>
        <EmptyHeader>
          <EmptyTitle>{update ? "Anteroom was updated" : `${this.props.label} couldn’t be shown`}</EmptyTitle>
          <EmptyDescription>
            {update ? "Reload to use the latest version." : "Your saved work is safe. Try again, or reload Anteroom."}
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="flex-row justify-center">
          {!update && (
            <Button size="sm" variant="outline" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
          )}
          <Button size="sm" onClick={reloadPage}>
            <RotateCwIcon />
            Reload
          </Button>
        </EmptyContent>
      </Empty>
    );
  }
}

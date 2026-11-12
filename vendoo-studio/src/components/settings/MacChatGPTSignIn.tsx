import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogInIcon } from "lucide-react";
import type { MacChatGPTBridge, MacChatGPTSignIn as SignInState } from "../../lib/macApp";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const WATCH_MS = 2000;

function checked(result: SignInState, fallback: string) {
  if (!result.ok) throw new Error(result.error || fallback);
  return result;
}

/**
 * Signs this Mac into ChatGPT from the account window. The Mac app runs the
 * sign-in and keeps the tokens in its Keychain; the account sees ChatGPT as
 * ready once this Mac's worker reports it.
 */
export function MacChatGPTSignIn({ bridge }: { bridge: MacChatGPTBridge }) {
  const queryClient = useQueryClient();
  const { data: local, error: statusError } = useQuery({
    queryKey: ["mac-chatgpt-sign-in"],
    queryFn: async () => checked(await bridge.chatgpt_sign_in_status(), "Anteroom on this Mac did not answer."),
    refetchInterval: (query) => (query.state.data?.pending || query.state.data?.signed_in ? WATCH_MS : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["mac-chatgpt-sign-in"] });
  const start = useMutation({
    mutationFn: async () => checked(await bridge.chatgpt_sign_in(), "ChatGPT sign-in could not start."),
    onSettled: refresh,
  });
  const cancel = useMutation({
    mutationFn: async () => checked(await bridge.chatgpt_sign_in_cancel(), "Anteroom on this Mac did not answer."),
    onSettled: refresh,
  });

  const signedIn = Boolean(local?.signed_in);
  const pending = !signedIn && Boolean(local?.pending);
  // The account learns about the sign-in from this Mac's worker; check it
  // often until it does, instead of waiting for the idle poll.
  useEffect(() => {
    if (!signedIn && !pending) return;
    const id = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
    }, WATCH_MS);
    return () => window.clearInterval(id);
  }, [signedIn, pending, queryClient]);

  const error = (start.error ?? cancel.error ?? statusError)?.message ?? (pending ? null : local?.error);

  return (
    <>
      <SettingsSection title="Account">
        <SettingsRow
          title="Sign in with ChatGPT"
          description={
            signedIn
              ? "Signed in on this Mac. Connecting it to your account…"
              : "Uses your ChatGPT plan on this Mac to write listings here, on the web and on iPhone. You approve Anteroom in your browser, and the sign-in stays on this Mac."
          }
          control={
            signedIn ? (
              <Spinner size="sm" tone="muted" />
            ) : pending ? (
              <Button size="xs" variant="ghost-muted" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                Cancel
              </Button>
            ) : (
              <Button size="xs" variant="outline" onClick={() => start.mutate()} disabled={start.isPending}>
                {start.isPending ? <Spinner /> : <LogInIcon />}
                {start.isPending ? "Starting" : error ? "Retry sign-in" : "Sign in"}
              </Button>
            )
          }
        >
          {pending ? (
            <p className="flex max-w-xl items-center gap-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-sm text-muted-foreground">
              <Spinner size="sm" tone="muted" />
              Approve Anteroom in your browser, and leave token sharing on.
            </p>
          ) : error ? (
            <SettingsNote tone="error">{error}</SettingsNote>
          ) : null}
        </SettingsRow>
      </SettingsSection>
      <SettingsNote>Keep this Mac awake and Anteroom open while generating from the web or iPhone.</SettingsNote>
    </>
  );
}

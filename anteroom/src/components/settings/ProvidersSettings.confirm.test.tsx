// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../../app/runtime";
import { api } from "../../api/client";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../../ui/confirmDialog";
import { ProvidersSettings } from "./ProvidersSettings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  completeConfirmDialogClose();
  vi.restoreAllMocks();
});

function mountProviders(primary: "openai" | "chatgpt" | "mimo" | "cursor") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  });
  client.setQueryData(["settings-provider"], {
    provider: primary,
    primary,
    fallback: "none",
    photo_provider: "same",
    configured: true,
    masked_key: "sk-mimo…1234",
    cursor_configured: true,
    masked_cursor_key: "cur…5678",
    chatgpt: { signed_in: true, email: "seller@example.com", plan: "Plus", pending: false, error: null },
  });
  client.setQueryData(["ai-keys"], {
    providers: [
      {
        id: "openai",
        label: "OpenAI",
        configured: true,
        masked_key: "sk-open…9999",
        sign_in: false,
        key_url: "https://platform.openai.com/api-keys",
      },
    ],
  });
  client.setQueryData(["settings-brave"], { configured: true, masked_key: "BSA…1111" });
  client.setQueryData(["settings-photoroom"], { configured: true, masked_key: "sk_pr_…2222", source: "keychain", available: true });
  client.setQueryData(["settings-chatgpt-models"], { models: ["gpt-5"], vision_model: "gpt-5", listing_model: "gpt-5", reasoning_effort: "low" });
  client.setQueryData(["settings-cursor-models"], { models: ["auto"], vision_model: "auto", listing_model: "auto", reasoning: null });
  client.setQueryData(["ai-key-models", "openai"], { models: ["gpt-5"], labels: {}, vision_model: "gpt-5", listing_model: "gpt-5", reasoning: null });

  const unregister = registerConfirmDialogHost();
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <WorkspaceRuntimeProvider value={desktopRuntime}>
        <QueryClientProvider client={client}>
          <ProvidersSettings />
        </QueryClientProvider>
      </WorkspaceRuntimeProvider>,
    );
  });
  return {
    host,
    unmount: async () => {
      unregister();
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    },
  };
}

async function confirmAndVerify(
  trigger: HTMLButtonElement,
  confirmLabel: string,
  mutationSpy: ReturnType<typeof vi.spyOn>,
) {
  // First click opens confirmDialog; cancelling should not mutate.
  await act(async () => {
    trigger.click();
  });
  expect(mutationSpy).not.toHaveBeenCalled();
  let dialog = readConfirmDialogState();
  expect(dialog.status).toBe("confirming");
  expect(dialog.status === "confirming" && dialog.confirmLabel).toBe(confirmLabel);
  expect(dialog.status === "confirming" && dialog.variant).toBe("destructive");
  await act(async () => {
    respondToConfirmDialog(false);
    completeConfirmDialogClose();
  });
  expect(mutationSpy).not.toHaveBeenCalled();

  // Confirming runs the mutation once.
  await act(async () => {
    trigger.click();
  });
  dialog = readConfirmDialogState();
  expect(dialog.status).toBe("confirming");
  await act(async () => {
    respondToConfirmDialog(true);
    completeConfirmDialogClose();
  });
  await vi.waitFor(() => {
    expect(mutationSpy).toHaveBeenCalledTimes(1);
  });
}

describe("ProvidersSettings removal confirmations", () => {
  it("confirms before removing Brave Search, Photoroom, and OpenAI API keys", async () => {
    const deleteBrave = vi.spyOn(api.settings, "deleteBrave").mockResolvedValue({
      ok: true,
      configured: false,
      masked_key: null,
    });
    const deletePhotoroom = vi.spyOn(api.settings, "deletePhotoroom").mockResolvedValue({
      configured: false,
      masked_key: null,
      source: null,
      available: false,
    });
    const deleteAiKey = vi.spyOn(api.settings, "deleteAIKey").mockResolvedValue({ ok: true });
    const view = mountProviders("openai");
    try {
      const braveBtn = view.host.querySelector<HTMLButtonElement>("#brave-api-key button:last-of-type");
      expect(braveBtn?.textContent).toContain("Remove");
      await confirmAndVerify(braveBtn!, "Remove key", deleteBrave);

      const photoroomBtn = view.host.querySelector<HTMLButtonElement>("#photoroom-api-key button:last-of-type");
      expect(photoroomBtn?.textContent).toContain("Remove");
      await confirmAndVerify(photoroomBtn!, "Remove key", deletePhotoroom);

      const removeButtons = [...view.host.querySelectorAll<HTMLButtonElement>("button")].filter(
        (b) => b.textContent?.trim() === "Remove" && !b.closest("#brave") && !b.closest("#photoroom"),
      );
      expect(removeButtons.length).toBeGreaterThan(0);
      await confirmAndVerify(removeButtons[0]!, "Remove key", deleteAiKey);
    } finally {
      await view.unmount();
    }
  });

  it("confirms before signing out of ChatGPT", async () => {
    const logoutSpy = vi.spyOn(api.settings, "chatgptLogout").mockResolvedValue({ ok: true });
    const view = mountProviders("chatgpt");
    try {
      const signOutBtn = [...view.host.querySelectorAll<HTMLButtonElement>("button")].find(
        (b) => b.textContent?.trim() === "Sign out",
      );
      expect(signOutBtn).toBeDefined();
      await confirmAndVerify(signOutBtn!, "Sign out", logoutSpy);
    } finally {
      await view.unmount();
    }
  });

  it("confirms before removing MiMo API key", async () => {
    const deleteMimo = vi.spyOn(api.settings, "deleteKey").mockResolvedValue({ ok: true });
    const view = mountProviders("mimo");
    try {
      const removeButtons = [...view.host.querySelectorAll<HTMLButtonElement>("button")].filter(
        (b) => b.textContent?.trim() === "Remove" && !b.closest("#brave") && !b.closest("#photoroom"),
      );
      expect(removeButtons.length).toBeGreaterThan(0);
      await confirmAndVerify(removeButtons[0]!, "Remove key", deleteMimo);
    } finally {
      await view.unmount();
    }
  });

  it("confirms before removing Cursor API key", async () => {
    const deleteCursor = vi.spyOn(api.settings, "deleteCursor").mockResolvedValue({
      ok: true,
      configured: false,
      masked_key: null,
    });
    const view = mountProviders("cursor");
    try {
      const removeButtons = [...view.host.querySelectorAll<HTMLButtonElement>("button")].filter(
        (b) => b.textContent?.trim() === "Remove" && !b.closest("#brave") && !b.closest("#photoroom"),
      );
      expect(removeButtons.length).toBeGreaterThan(0);
      await confirmAndVerify(removeButtons[0]!, "Remove key", deleteCursor);
    } finally {
      await view.unmount();
    }
  });
});

import { createRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../../app/runtime";
import { ChatComposer } from "./ChatComposer";

function renderComposer(runtime: WorkspaceRuntime) {
  const client = new QueryClient();
  client.setQueryData(["settings-provider"], {
    provider: "openrouter",
    primary: "openrouter",
    fallback: "none",
    photo_provider: "same",
    configured: true,
    vision_model: "google/gemini-3.8-flash",
    listing_model: "google/gemini-3.8-flash",
    chatgpt: { signed_in: false },
    claude: { installed: false, signed_in: false },
  });
  client.setQueryData(["ai-keys"], {
    providers: [
      {
        id: "openrouter",
        label: "OpenRouter",
        masked_key: "Configured",
        listing_model: "google/gemini-3.8-flash",
        vision_model: "google/gemini-3.8-flash",
      },
    ],
  });
  client.setQueryData(["ai-key-models", "openrouter"], {
    models: ["google/gemini-3.8-flash"],
    labels: { "google/gemini-3.8-flash": "Gemini 3.8 Flash" },
    listing_model: "google/gemini-3.8-flash",
    vision_model: "google/gemini-3.8-flash",
  });
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={runtime}>
      <QueryClientProvider client={client}>
        <ChatComposer
          composerRef={createRef()}
          input=""
          onInputChange={vi.fn()}
          placeholder="Ask anything"
          images={{
            images: [],
            error: "",
            setError: vi.fn(),
            addFiles: vi.fn(() => false),
            remove: vi.fn(),
            clear: vi.fn(),
            prepare: vi.fn(async () => []),
          }}
          busy={false}
          onSend={vi.fn()}
        />
      </QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

describe("ChatComposer", () => {
  it("omits model and reasoning controls when managed_ai is enabled", () => {
    const html = renderComposer({
      ...desktopRuntime,
      hosted: true,
      features: new Set([...desktopRuntime.features, "managed_ai"]),
    });
    expect(html).toContain('data-chat-composer-controls="left"');
    expect(html).not.toContain("gemini-3.8-flash");
    expect(html).not.toContain("OpenRouter");
  });

  it("keeps the model picker on desktop", () => {
    const html = renderComposer(desktopRuntime);
    expect(html).toContain("Gemini 3.8 Flash");
  });
});

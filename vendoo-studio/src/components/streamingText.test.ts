import { describe, expect, it } from "vitest";
import { stableStreamingText, streamingDisplayText } from "./streamingText";

describe("stableStreamingText", () => {
  it("holds an unfinished paragraph", () => {
    expect(stableStreamingText("Still writing this paragraph")).toBe("");
  });

  it("reveals completed paragraphs and holds the active one", () => {
    expect(stableStreamingText("First paragraph.\n\nSecond para")).toBe("First paragraph.\n\n");
  });

  it("reveals tight list items one item behind", () => {
    expect(stableStreamingText("- First\n- Second\n- Thi")).toBe("- First\n- Second\n");
  });

  it("does not split at blank lines inside a code fence", () => {
    expect(stableStreamingText("```ts\nconst a = 1;\n\nconst b = 2;\n")).toBe("");
  });

  it("reveals a closed code fence", () => {
    const block = "```ts\nconst a = 1;\n```\n";
    expect(stableStreamingText(`${block}\nTail`)).toBe(`${block}\n`);
  });
});

describe("streamingDisplayText", () => {
  it("shows the paragraph being written up to its last whole word", () => {
    expect(streamingDisplayText("Still writing this parag")).toBe("Still writing this");
    expect(streamingDisplayText("First.\n\nSecond para")).toBe("First.\n\nSecond");
  });

  it("closes bold, italic, inline code and strikethrough left open", () => {
    expect(streamingDisplayText("This is **very important and")).toBe("This is **very important**");
    expect(streamingDisplayText("Try *this one now")).toBe("Try *this one*");
    expect(streamingDisplayText("Run `npm run build now")).toBe("Run `npm run build`");
    expect(streamingDisplayText("Was ~~old price here")).toBe("Was ~~old price~~");
  });

  it("drops an opener that has nothing after it yet", () => {
    expect(streamingDisplayText("Price is ** next")).toBe("Price is");
  });

  it("leaves closed markup and list markers alone", () => {
    expect(streamingDisplayText("**Done** and *also* fine here")).toBe("**Done** and *also* fine");
    expect(streamingDisplayText("* first item\n* second item go")).toBe("* first item\n* second item");
  });

  it("holds back a link until its target closes", () => {
    expect(streamingDisplayText("See [the listing](https://exa now")).toBe("See");
    expect(streamingDisplayText("See [the listing](https://example.com) now ok")).toBe(
      "See [the listing](https://example.com) now",
    );
  });

  it("shows whole lines of an open code fence", () => {
    expect(streamingDisplayText("Intro.\n\n```ts\nconst a = 1;\nconst b")).toBe("Intro.\n\n```ts\nconst a = 1;\n");
  });

  it("waits for a whole table", () => {
    expect(streamingDisplayText("Intro.\n\n| Size | Price |\n| --- | --- |\n| M | 2")).toBe("Intro.\n\n");
  });
});

import { describe, expect, it } from "vitest";
import { rehypeStreamWords, STREAM_WORD_CLASS } from "./streamWords";

type Node = { type: string; tagName?: string; value?: string; properties?: Record<string, unknown>; children?: Node[] };

const text = (value: string): Node => ({ type: "text", value });
const el = (tagName: string, ...children: Node[]): Node => ({ type: "element", tagName, properties: {}, children });
const root = (...children: Node[]): Node => ({ type: "root", children });

function run(tree: Node): Node {
  rehypeStreamWords()(tree as never);
  return tree;
}

/** A tree written back out with each faded word in brackets. */
function outline(node: Node): string {
  if (node.type === "text") return node.value ?? "";
  const inner = (node.children ?? []).map(outline).join("");
  if (node.type !== "element") return inner;
  const classes = node.properties?.className;
  if (node.tagName === "span" && Array.isArray(classes) && classes.includes(STREAM_WORD_CLASS)) return `[${inner}]`;
  return `<${node.tagName}>${inner}</${node.tagName}>`;
}

describe("rehypeStreamWords", () => {
  it("wraps each word of the block being written, with the space after it", () => {
    const tree = run(root(el("p", text("Two words"))));
    expect(outline(tree)).toBe("<p>[Two ][words]</p>");
  });

  it("leaves finished blocks alone", () => {
    const tree = run(root(el("p", text("Done here.")), text("\n"), el("p", text("Being written"))));
    expect(outline(tree)).toBe("<p>Done here.</p>\n<p>[Being ][written]</p>");
  });

  it("reaches into bold, links and list items", () => {
    const tree = run(root(el("ul", el("li", text("A "), el("strong", text("bold claim")), text(" and "), el("a", text("a link"))))));
    expect(outline(tree)).toBe("<ul><li>[A ]<strong>[bold ][claim]</strong> [and ]<a>[a ][link]</a></li></ul>");
  });

  it("keeps a word's place when more text arrives after it", () => {
    const before = run(root(el("p", text("One two"))));
    const after = run(root(el("p", text("One two three"))));
    const words = (tree: Node) => tree.children![0].children!.map((child) => child.children![0].value);
    expect(words(before)).toEqual(["One ", "two"]);
    expect(words(after)).toEqual(["One ", "two ", "three"]);
  });

  it("does not touch code, where spacing is part of the text", () => {
    const tree = run(root(el("p", text("Run "), el("code", text("npm  run build")))));
    expect(outline(tree)).toBe("<p>[Run ]<code>npm  run build</code></p>");
    const block = run(root(el("pre", el("code", text("a = 1\nb = 2\n")))));
    expect(outline(block)).toBe("<pre><code>a = 1\nb = 2\n</code></pre>");
  });

  it("leaves a whole table alone", () => {
    const tree = run(root(el("table", el("tbody", el("tr", el("td", text("eBay 8")))))));
    expect(outline(tree)).toBe("<table><tbody><tr><td>eBay 8</td></tr></tbody></table>");
  });

  it("skips trailing whitespace when finding the block being written", () => {
    const tree = run(root(el("p", text("Last one")), text("\n")));
    expect(outline(tree)).toBe("<p>[Last ][one]</p>\n");
  });
});

// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { tableToHtml, tableToTsv } from "./MarkdownBlocks";

function table(html: string): Element {
  const host = document.createElement("div");
  host.innerHTML = html;
  return host.querySelector("table")!;
}

const SALES = table(`
  <table>
    <thead><tr><th>Marketplace</th><th>Orders</th><th>Revenue</th></tr></thead>
    <tbody>
      <tr><td>eBay</td><td>8</td><td>$371.20</td></tr>
      <tr><td><strong>Poshmark</strong> &amp; Depop</td><td>4</td><td>$168.00</td></tr>
    </tbody>
  </table>`);

describe("copying a table", () => {
  it("gives one line a row and one tab a value, so a spreadsheet fills cells", () => {
    expect(tableToTsv(SALES)).toBe("Marketplace\tOrders\tRevenue\neBay\t8\t$371.20\nPoshmark & Depop\t4\t$168.00");
  });

  it("keeps a value on its own row when it wraps or holds a tab", () => {
    const wrapped = table("<table><tbody><tr><td>Levi's\n  501\tjeans</td><td>41</td></tr></tbody></table>");
    expect(tableToTsv(wrapped)).toBe("Levi's 501 jeans\t41");
  });

  it("keeps a value that reads as a formula as text, and leaves figures alone", () => {
    const row = (...cells: string[]) => table(`<table><tbody><tr>${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr></tbody></table>`);
    // Figures and a lone dash are what they look like.
    expect(tableToTsv(row("-12.50", "+5%", "-$4.00", "-1,204", "+.5", "-", "—", "$-3")))
      .toBe("-12.50\t+5%\t-$4.00\t-1,204\t+.5\t-\t—\t$-3");
    // Anything else a spreadsheet would run gets an apostrophe, a signed number with more after it included.
    expect(tableToTsv(row('=HYPERLINK("https://example.com")', "@buyer", "+cmd", "-2+3+cmd|' /C calc'!A0", "+1+HYPERLINK(1)", " =1+1")))
      .toBe("'=HYPERLINK(\"https://example.com\")\t'@buyer\t'+cmd\t'-2+3+cmd|' /C calc'!A0\t'+1+HYPERLINK(1)\t'=1+1");
    expect(tableToHtml(row("=1+1", "-12.50"))).toBe("<table><tr><td>'=1+1</td><td>-12.50</td></tr></table>");
  });

  it("gives a plain HTML table with its header row, escaped", () => {
    expect(tableToHtml(SALES)).toBe(
      "<table><tr><th>Marketplace</th><th>Orders</th><th>Revenue</th></tr>"
        + "<tr><td>eBay</td><td>8</td><td>$371.20</td></tr>"
        + "<tr><td>Poshmark &amp; Depop</td><td>4</td><td>$168.00</td></tr></table>",
    );
    expect(tableToHtml(table("<table><tbody><tr><td>a < b</td></tr></tbody></table>"))).toBe("<table><tr><td>a &lt; b</td></tr></table>");
  });
});

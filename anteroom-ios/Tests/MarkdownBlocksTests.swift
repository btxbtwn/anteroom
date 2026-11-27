import Foundation

/// The business assistant keeps an answer's paragraphs, lists and code apart.
@main
struct MarkdownBlocksTests {
    static func main() {
        let blocks = MarkdownBlock.parse("""
        ## Top brands
        Your **best** sellers:

        - Levi's: $120
        - Free People
          (dresses)
        1. Raise prices
        2) Relist stale items

        ```sql
        select brand, sum(price)
        from sales
        ```
        > Based on 12 sales.
        Last line.
        """)
        precondition(blocks == [
            .heading(level: 2, text: "Top brands"),
            .paragraph("Your **best** sellers:"),
            .bullet("Levi's: $120"),
            .bullet("Free People (dresses)"),
            .numbered(number: "1", text: "Raise prices"),
            .numbered(number: "2", text: "Relist stale items"),
            .code("select brand, sum(price)\nfrom sales"),
            .quote("Based on 12 sales."),
            .paragraph("Last line."),
        ], "\(blocks)")
        precondition(MarkdownBlock.parse("One line\nsame paragraph") == [.paragraph("One line\nsame paragraph")])
        precondition(MarkdownBlock.parse("```\nunclosed") == [.code("unclosed")])

        // The assistant is told to compare things in a table; it is drawn as one, not as rows of pipes.
        let table = MarkdownBlock.parse("""
        Last week by marketplace:

        | Marketplace | Orders | Revenue |
        |:--|:-:|--:|
        | eBay | 8 | $371.20 |
        | Poshmark \\| Depop | 4 |
        Up from $488.
        """)
        precondition(table == [
            .paragraph("Last week by marketplace:"),
            .table(header: ["Marketplace", "Orders", "Revenue"],
                   alignments: [.leading, .center, .trailing],
                   rows: [["eBay", "8", "$371.20"], ["Poshmark | Depop", "4", ""]]),
            .paragraph("Up from $488."),
        ], "\(table)")
        // Without outer pipes, as some models write it.
        precondition(MarkdownBlock.parse("Brand | Sold\n--- | ---\nLevi's | 3") == [
            .table(header: ["Brand", "Sold"], alignments: [.leading, .leading], rows: [["Levi's", "3"]]),
        ])
        // A line with a pipe and no divider row under it is still a sentence.
        precondition(MarkdownBlock.parse("Size M | L are both listed.") == [.paragraph("Size M | L are both listed.")])
        precondition(MarkdownBlock.parse("| Marketplace | Orders |") == [.paragraph("| Marketplace | Orders |")])
        print("Markdown block tests passed")
    }
}

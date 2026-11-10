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
        print("Markdown block tests passed")
    }
}

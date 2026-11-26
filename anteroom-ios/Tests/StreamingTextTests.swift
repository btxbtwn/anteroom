import Foundation

/// A streaming answer shows finished blocks and the block being written up to
/// its last whole word, with open markup closed. The same cases as the web's
/// `streamingText.test.ts`, so both show a reply the same way while it is written.
@main
struct StreamingTextTests {
    static func expect(_ input: String, _ shown: String, line: UInt = #line) {
        let got = StreamingText.display(input)
        precondition(got == shown, "line \(line): \(input.debugDescription) showed \(got.debugDescription), expected \(shown.debugDescription)")
    }

    static func main() {
        // The paragraph being written, up to its last whole word.
        expect("Still writing this parag", "Still writing this")
        expect("First.\n\nSecond para", "First.\n\nSecond")
        expect("Word", "")

        // Bold, italic, inline code and strikethrough left open are closed.
        expect("This is **very important and", "This is **very important**")
        expect("Try *this one now", "Try *this one*")
        expect("Run `npm run build now", "Run `npm run build`")
        expect("Was ~~old price here", "Was ~~old price~~")

        // An opener with nothing after it yet is dropped.
        expect("Price is ** next", "Price is")

        // Closed markup and list markers are left alone.
        expect("**Done** and *also* fine here", "**Done** and *also* fine")
        expect("* first item\n* second item go", "* first item\n* second item")
        expect("- First\n- Second\n- Thi", "- First\n- Second\n-")

        // A link waits for its target.
        expect("See [the listing](https://exa now", "See")
        expect("See [the listing](https://example.com) now ok", "See [the listing](https://example.com) now")
        expect("Open [the listing] next", "Open")

        // An open code fence shows whole lines; a closed one is finished.
        expect("Intro.\n\n```ts\nconst a = 1;\nconst b", "Intro.\n\n```ts\nconst a = 1;\n")
        expect("```ts\nconst a = 1;\n\nconst b = 2;\n```\n\nTail end", "```ts\nconst a = 1;\n\nconst b = 2;\n```\n\nTail")

        // A table waits until it is whole.
        expect("Totals:\n\n| Market | Orders |\n|---|---|\n| eBay | 8", "Totals:\n\n")
        expect("Totals:\n\n| Market | Orders |\n|---|---|\n| eBay | 8 |\n\nDone now", "Totals:\n\n| Market | Orders |\n|---|---|\n| eBay | 8 |\n\nDone")

        // Windows line endings behave like any other.
        expect("First.\r\n\r\nSecond para", "First.\n\nSecond")

        // The reveal moves at least one character, faster the more is waiting, never past the end.
        precondition(RevealPace.step(shown: 10, total: 11, elapsedMs: 1) == 11)
        precondition(RevealPace.step(shown: 0, total: 5, elapsedMs: 0) == 1)
        let small = RevealPace.step(shown: 0, total: 40, elapsedMs: 32)
        let large = RevealPace.step(shown: 0, total: 4000, elapsedMs: 32)
        precondition(small > 0 && small < 40)
        precondition(large > small * 10 && large < 4000)
        precondition(RevealPace.step(shown: 0, total: 40, elapsedMs: 60_000) == 40)
        // A shorter text is a new answer.
        precondition(RevealPace.step(shown: 50, total: 20, elapsedMs: 32) == 20)
        // A large chunk is caught up with in under a second.
        var shown = 0
        var elapsed = 0.0
        while Double(shown) < 1200 * 0.95 {
            shown = RevealPace.step(shown: shown, total: 1200, elapsedMs: 32)
            elapsed += 32
        }
        precondition(elapsed > 200 && elapsed < 1000, "\(elapsed)")

        print("Streaming text tests passed")
    }
}

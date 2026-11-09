import Foundation

/// Listing chat hides raw listing JSON the way assistantDisplay.ts does on web.
@main
struct AssistantDisplayTests {
    static func main() {
        precondition(AssistantDisplay.text("Hello seller.") == "Hello seller.")

        let listing = #"""
        {"title":"Blue Levi's tee","description":"Soft cotton.","price":18}
        """#
        precondition(AssistantDisplay.text(listing) == "Listing generated.")

        let fenced = #"""
        Here is the draft.
        ```json
        {"title":"Blue Levi's tee","description":"Soft cotton.","price":18}
        ```
        """#
        precondition(AssistantDisplay.text(fenced) == "Here is the draft.")

        let patch = #"[{"op":"replace","path":"/title","value":"Navy tee"},{"op":"replace","path":"/ebay_specifics/primaryColor","value":"Blue"}]"#
        precondition(
            AssistantDisplay.text(patch)
                == "Updated the listing:\n- Title: Navy tee\n- eBay Primary color: Blue"
        )

        let missing = #"{"missing_fields":[{"marketplace":"ebay","field":"Brand","value":"Levi's"},{"marketplace":"general","field":"Size","value":"M"}]}"#
        precondition(
            AssistantDisplay.text(missing)
                == "Saved to the listing JSON:\n- eBay / Brand: Levi's\n- General / Size: M"
        )

        let withMessage = #"{"message":"Filled the brand from the tag.","title":"x","price":1}"#
        precondition(AssistantDisplay.text(withMessage) == "Filled the brand from the tag.")

        precondition(AssistantDisplay.text("{\"partial\":") == "")
        precondition(AssistantDisplay.isListingJson(listing))
        precondition(AssistantDisplay.extractJson(fenced)?.contains("\"title\"") == true)

        print("Assistant display tests passed")
    }
}

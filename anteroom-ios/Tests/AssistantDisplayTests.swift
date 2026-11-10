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

        // The sandbox's generated reply, as the audit saw it on screen.
        let generated = AssistantReply(#"""
        ```json
        {
          "title": "Sandbox Levi's M Blue Cotton Crew Neck T-Shirt",
          "description": "Blue cotton crew neck T-shirt in good used condition.",
          "price": 25, "brand": "Levi's", "size": "M", "condition": "Good",
          "category_path": "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts",
          "primaryColor": "Blue", "quantity": 1, "secondaryColor": "",
          "ebay_specifics": {"type": "T-Shirt", "department": "Men"},
          "styleTags": []
        }
        ```
        """#)
        precondition(generated.isListing)
        precondition(generated.text == "Title, description and 8 item details filled.", generated.text)
        precondition(!generated.text.contains("{") && !generated.text.contains("```"))

        // Bare JSON with only a title and a price.
        let bare = AssistantReply(#"{"title":"Tee","price":10}"#)
        precondition(bare.isListing && bare.text == "Title and 1 item detail filled.", bare.text)

        // Prose around the JSON keeps the prose only.
        let prose = AssistantReply("Here is your listing.\n```json\n{\"title\":\"Tee\",\"price\":10}\n```")
        precondition(!prose.isListing && prose.text == "Here is your listing.", prose.text)

        // A chat edit arrives as a JSON patch and reads as the change.
        let replyPatch = AssistantReply(#"[{"op":"replace","path":"/ebay_specifics/primaryColor","value":"Navy"}]"#)
        precondition(replyPatch.text == "Updated the listing — eBay Primary color: Navy.", replyPatch.text)

        // A reply with a message says the message.
        precondition(AssistantReply(#"{"message":"Done."}"#).text == "Done.")

        // Ordinary text is untouched.
        precondition(AssistantReply("Try a shorter title.").text == "Try a shorter title.")
        print("Assistant display tests passed")
    }
}

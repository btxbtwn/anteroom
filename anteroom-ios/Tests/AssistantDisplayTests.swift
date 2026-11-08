import Foundation

/// The listing assistant shows a generated listing as a sentence about what was
/// filled, the way the web's assistantDisplay.ts does, and never its JSON.
@main
struct AssistantDisplayTests {
    static func main() {
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
        let patch = AssistantReply(#"[{"op":"replace","path":"/ebay_specifics/primaryColor","value":"Navy"}]"#)
        precondition(patch.text == "Updated the listing — eBay Primary color: Navy.", patch.text)

        // A reply with a message says the message.
        precondition(AssistantReply(#"{"message":"Done."}"#).text == "Done.")

        // Ordinary text is untouched.
        precondition(AssistantReply("Try a shorter title.").text == "Try a shorter title.")
        print("Assistant display tests passed")
    }
}

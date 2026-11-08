import Foundation

@main
struct MarketplaceSignInReturnTests {
    static func main() {
        let back = MarketplaceSignInReturn(url: URL(string: "anteroom://marketplace-signin?marketplace=ebay&state=s1&claim=c1")!)
        precondition(back?.marketplace == "ebay" && back?.state == "s1" && back?.claim == "c1" && back?.name == "eBay")
        precondition(MarketplaceSignInReturn(url: URL(string: "anteroom://marketplace-signin?marketplace=etsy&state=s&claim=c")!)?.name == "Etsy")
        for link in [
            "anteroom://login-callback?code=abc",
            "anteroom://marketplace-signin?marketplace=ebay&state=s1",
            "anteroom://marketplace-signin?marketplace=ebay&state=&claim=c1",
            "anteroom://marketplace-signin?marketplace=poshmark&state=s1&claim=c1",
            "https://marketplace-signin/?marketplace=ebay&state=s1&claim=c1",
        ] {
            precondition(MarketplaceSignInReturn(url: URL(string: link)!) == nil, link)
        }
        print("Marketplace sign-in return tests passed")
    }
}

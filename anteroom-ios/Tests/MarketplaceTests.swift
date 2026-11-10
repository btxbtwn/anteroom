import Foundation

/// eBay records read the way the web's ebay/*.tsx reads them, fields in the
/// server's order, and the dashboard totals follow dashboardSummary.ts.
@main
struct MarketplaceTests {
    static func main() throws {
        let page = try EbayPage.decode(Data(#"""
        {"rows":[
          {"id":"12-345","title":"Levi's tee","subtitle":"Sold \"today\"","status":"IN_PROGRESS","amount":"21.50",
           "fields":{"Sale":"25.00","Item cost":"3.50","Profit":"18.00","url":"https://www.ebay.com/itm/1","listingId":"1"},
           "children":[{"id":"li-1","title":"Line {1}","subtitle":"","status":"","amount":null,
                        "fields":{"zeta":"1","alpha":null},"children":[]}]},
          {"id":"12-346","title":"Jacket","subtitle":"","status":"","amount":"0",
           "fields":{"Sale":"10","Item cost":"10","Profit":"-0.5"},"children":[]}],
         "total":40,"nextOffset":25}
        """#.utf8))
        precondition(page.total == 40 && page.nextOffset == 25 && page.rows.count == 2)
        let first = page.rows[0]
        precondition(first.fields.map(\.key) == ["Sale", "Item cost", "Profit", "url", "listingId"], "fields keep the server's order")
        precondition(first.children[0].fields.map(\.key) == ["zeta", "alpha"] && first.children[0].fields[1].value == nil)
        precondition(first.subtitle == "Sold \"today\"" && first.amount == "21.50" && page.rows[0].children[0].amount == nil)
        precondition(first.field("Profit") == "18.00")

        // Totals cover the loaded orders only; a missing figure is not zero.
        let summary = EbayWorkspaceFormat.summary(page.rows)
        precondition(summary.earnings == 21.5 && summary.costs == 13.5 && summary.profit == 17.5)
        let missing = try EbayPage.decode(Data(#"{"rows":[{"id":"o","amount":"50","fields":{"Item cost":null,"Profit":null}}]}"#.utf8))
        let partial = EbayWorkspaceFormat.summary(missing.rows)
        precondition(partial.earnings == 50 && partial.costs == nil && partial.profit == nil)
        precondition(EbayWorkspaceFormat.summary([]).earnings == nil)
        precondition(EbayWorkspaceFormat.money(0) == "$0.00" && EbayWorkspaceFormat.money(-5) == "-$5.00")
        precondition(EbayWorkspaceFormat.money(1234.5) == "$1,234.50" && EbayWorkspaceFormat.money(nil) == "Not available")
        precondition(EbayWorkspaceFormat.coverage(shown: 10, total: 40) == "First 10 of 40 orders · totals cover these orders only")
        precondition(EbayWorkspaceFormat.coverage(shown: 1, total: 1) == "First 1 of 1 order · totals cover these orders only")
        precondition(EbayWorkspaceFormat.count(total: 1, shown: 1) == "1 result" && EbayWorkspaceFormat.count(total: nil, shown: 3) == "3 shown")
        precondition(EbayWorkspaceFormat.pageSize("finances") == 10 && EbayWorkspaceFormat.pageSize("orders") == 25)

        precondition(EbayWorkspaceFormat.label("listingId") == "listing ID" && EbayWorkspaceFormat.label("Item cost") == "Item cost")
        precondition(EbayWorkspaceFormat.label("transactionId") == "transaction ID" && EbayWorkspaceFormat.label("sku") == "sku")
        precondition(EbayWorkspaceFormat.status("IN_PROGRESS") == "in progress")
        precondition(EbayWorkspaceFormat.ebayLink("https://www.ebay.com/itm/1") != nil && EbayWorkspaceFormat.ebayLink("https://ebay.com/x") != nil)
        precondition(EbayWorkspaceFormat.ebayLink("http://www.ebay.com/itm/1") == nil && EbayWorkspaceFormat.ebayLink("https://ebay.com.evil.test/") == nil)
        precondition(EbayWorkspaceFormat.features.count == 8)

        // Key order survives nesting, escapes and arrays.
        let orders = JSONKeyOrder.objects(named: "fields", in: Data(#"""
        {"a":[{"fields":{"b":"x,y","a":{"z":1,"y":2},"c":[1,{"q":0}],"\"q\"":"}"}},{"fields":{}}],"fields":{"k":1}}
        """#.utf8))
        precondition(orders == [["b", "a", "c", "\"q\""], [], ["k"]], "\(orders)")

        let connections = try JSONDecoder().decode(DirectConnections.self, from: Data(#"""
        {"accounts":{"ebay":{"connected":true,"label":"seller1","connectedAt":1761955200,"pending":false,"authorizeUrl":null,"error":null},
                     "etsy":{"connected":false,"label":null,"connectedAt":null,"pending":true,"authorizeUrl":"https://www.etsy.com/oauth","error":"x"}},
         "relay":{"running":true}}
        """#.utf8))
        precondition(connections.accounts["ebay"]?.connected == true && connections.accounts["ebay"]?.label == "seller1")
        precondition(connections.accounts["etsy"]?.pending == true && connections.accounts["etsy"]?.authorizeURL == "https://www.etsy.com/oauth")
        print("Marketplace tests passed")
    }
}

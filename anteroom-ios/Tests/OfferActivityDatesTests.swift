import Foundation

@main
struct OfferActivityDatesTests {
    static func main() {
        let date = ISO8601DateFormatter().date(from: "2026-11-16T01:30:00Z")!
        for locale in ["ar_SA", "fa_IR", "th_TH", "en_US"] {
            // Simulate the seller's calendar/locale while preserving local days.
            var calendar = Calendar(identifier: .islamicUmmAlQura)
            calendar.locale = Locale(identifier: locale)
            calendar.timeZone = TimeZone(identifier: "America/New_York")!
            let day = OfferActivityDates.day(date, calendar: calendar)
            precondition(day == "2026-11-15", "API date changed under \(locale): \(day)")
            precondition(day.utf8.allSatisfy { (48...57).contains($0) || $0 == 45 })
            calendar.timeZone = TimeZone(secondsFromGMT: 0)!
            precondition(OfferActivityDates.day(date, calendar: calendar) == "2026-11-16")
        }
        // Demonstrate the unsafe formatter's localized numerals, independent of
        // the process locale, so this test does not silently test English only.
        let localized = DateFormatter()
        localized.locale = Locale(identifier: "ar_SA@numbers=arab")
        localized.calendar = Calendar(identifier: .gregorian)
        localized.timeZone = TimeZone(secondsFromGMT: 0)!
        localized.dateFormat = "yyyy-MM-dd"
        precondition(localized.string(from: date) != "2026-11-16")
        print("Offer activity API date checks passed")
    }
}

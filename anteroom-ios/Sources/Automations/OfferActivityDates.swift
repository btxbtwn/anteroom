import Foundation

/// API dates have a fixed format even when the seller uses a non-Latin locale.
enum OfferActivityDates {
    static func day(_ date: Date, calendar: Calendar) -> String {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = calendar.timeZone
        format.calendar = gregorian
        format.timeZone = calendar.timeZone
        format.dateFormat = "yyyy-MM-dd"
        return format.string(from: date)
    }
}

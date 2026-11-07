import UIKit
import XCTest
@testable import Anteroom

@MainActor
final class PhotoUploadTests: XCTestCase {
    private struct Refused: LocalizedError {
        var errorDescription: String? { "The server is busy." }
    }

    func testEveryFailureIsCountedAndKeptForRetry() async {
        var uploaded: [Int] = []
        var finished = 0
        let photos = (1...10).map { Data([UInt8($0)]) }
        let report = await PhotoUploadReport.run(
            photos,
            prepare: { $0 },
            upload: { data -> Int in
                let number = Int(data[0])
                if number == 3 || number == 7 { throw Refused() }
                return number
            },
            uploaded: { uploaded.append($0) },
            finished: { finished += 1 })
        XCTAssertEqual(uploaded, [1, 2, 4, 5, 6, 8, 9, 10], "A failure doesn't stop the photos after it")
        XCTAssertEqual(finished, 10)
        XCTAssertEqual(report.uploaded, 8)
        XCTAssertEqual(report.failed, [Data([3]), Data([7])], "The prepared bytes are kept for Retry")
        XCTAssertEqual(report.message, "2 of 10 photos didn't upload. The server is busy.")
    }

    func testUnreadablePhotosAreCountedApart() async {
        let report = await PhotoUploadReport.run(
            [Data([1]), Data([2]), Data([3])],
            prepare: { $0 == Data([2]) ? nil : $0 },
            upload: { $0.count },
            uploaded: { _ in })
        XCTAssertEqual(report.uploaded, 2)
        XCTAssertTrue(report.failed.isEmpty)
        XCTAssertEqual(report.unreadable, 1)
        XCTAssertEqual(report.message, "1 photo couldn't be opened.")
    }

    func testAllUploadedSaysNothing() async {
        let report = await PhotoUploadReport.run([Data([1])], prepare: { $0 }, upload: { $0.count }, uploaded: { _ in })
        XCTAssertNil(report.message)
        XCTAssertEqual(report.problems, 0)
    }

    func testASingleFailedPhoto() async {
        let report = await PhotoUploadReport.run([Data([1])], prepare: { $0 },
                                                 upload: { _ -> Int in throw Refused() }, uploaded: { _ in })
        XCTAssertEqual(report.message, "The photo didn't upload. The server is busy.")
    }

    func testCameraPhotosShrinkToTheUploadSize() {
        XCTAssertEqual(PhotoPreparer.fitted(CGSize(width: 4032, height: 3024), maxPixels: 2400), CGSize(width: 2400, height: 1800))
        XCTAssertEqual(PhotoPreparer.fitted(CGSize(width: 3024, height: 4032), maxPixels: 2400), CGSize(width: 1800, height: 2400))
        XCTAssertEqual(PhotoPreparer.fitted(CGSize(width: 800, height: 600), maxPixels: 2400), CGSize(width: 800, height: 600))
        let image = UIGraphicsImageRenderer(size: CGSize(width: 4000, height: 3000),
                                            format: { let format = UIGraphicsImageRendererFormat(); format.scale = 1; return format }())
            .image { context in
                UIColor.red.setFill()
                context.fill(CGRect(x: 0, y: 0, width: 4000, height: 3000))
            }
        let decoded = PhotoPreparer.jpeg(from: image).flatMap { UIImage(data: $0) }
        XCTAssertEqual(decoded?.cgImage?.width, 2400)
        XCTAssertEqual(decoded?.cgImage?.height, 1800)
    }
}

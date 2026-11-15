import Foundation
import Vision
import AppKit
// Prints: file \t y(top=0..1) \t x \t text   for each recognised line
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    for obs in (req.results ?? []) {
        guard let t = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        print("\(path)\t\(String(format: "%.4f", 1 - b.maxY))\t\(String(format: "%.4f", b.minX))\t\(t.string)")
    }
}

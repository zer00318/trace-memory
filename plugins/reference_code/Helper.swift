import Foundation

private let input = FileHandle.standardInput.readDataToEndOfFile()
guard
    let object = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
    object["api"] as? Int == 1,
    let text = object["text"] as? String
else {
    FileHandle.standardError.write(Data("invalid claims-v1 input\n".utf8))
    exit(64)
}

let pattern = #"\b(?:class|struct|enum|func|def)\s+([A-Za-z_][A-Za-z0-9_]*)"#
let regex = try! NSRegularExpression(pattern: pattern)
let whole = NSRange(text.startIndex..<text.endIndex, in: text)
var symbols: [String] = []
for match in regex.matches(in: text, range: whole) {
    guard match.numberOfRanges > 1,
          let range = Range(match.range(at: 1), in: text) else { continue }
    let symbol = String(text[range])
    if !symbols.contains(symbol) { symbols.append(symbol) }
    if symbols.count == 12 { break }
}

var claims: [[String: Any]] = []
if !symbols.isEmpty {
    claims.append([
        "text": "Code symbols: " + symbols.joined(separator: ", "),
        "confidence": 0.98,
        "structured": ["symbols": symbols, "reader": "syntax-keywords-v1"],
    ])
}
let output: [String: Any] = [
    "schema": "trace.helper.claims.v1",
    "claims": claims,
]
let encoded = try! JSONSerialization.data(withJSONObject: output,
                                           options: [.sortedKeys])
FileHandle.standardOutput.write(encoded)

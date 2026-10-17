import Foundation

/// One bubble in the conversation.
struct ChatMessage: Identifiable, Codable, Equatable {
    enum Role: String, Codable { case user, assistant }

    var id = UUID()
    let role: Role
    var content: String
    var error: String?
}

/// What the home chat service streams back for one question (see web/app.py).
enum ChatEvent: Equatable {
    case status(String)
    case text(String)
    case error(String)
    case done

    /// Parses one line of the newline-delimited JSON stream; nil for blank or unknown lines.
    static func parse(_ line: String) -> ChatEvent? {
        struct Wire: Decodable { let type: String; let text: String? }
        guard let data = line.data(using: .utf8),
              let wire = try? JSONDecoder().decode(Wire.self, from: data) else { return nil }
        switch wire.type {
        case "status": return .status(wire.text ?? "")
        case "text": return .text(wire.text ?? "")
        case "error": return .error(wire.text ?? "Something went wrong.")
        case "done": return .done
        default: return nil
        }
    }
}

/// Talks to the home chat service on percula. The Claude API key lives there, never in this app.
struct ChatClient {
    let baseURL: URL

    func ask(_ history: [ChatMessage]) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: baseURL.appending(path: "api/chat"))
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    // Tools can take a while (the forecast calls out to Open-Meteo); this is the wait between chunks
                    request.timeoutInterval = 180
                    let turns = history
                        .filter { !$0.content.isEmpty }
                        .map { ["role": $0.role.rawValue, "content": $0.content] }
                    request.httpBody = try JSONSerialization.data(withJSONObject: ["messages": turns])

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                        throw URLError(.badServerResponse)
                    }
                    for try await line in bytes.lines {
                        if let event = ChatEvent.parse(line) {
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

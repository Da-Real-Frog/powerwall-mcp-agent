import Foundation
import SwiftUI

@MainActor
final class ChatModel: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var status: String?
    @Published private(set) var busy = false

    private var current: Task<Void, Never>?
    private static let storeKey = "conversation"
    private static let keep = 40

    init() {
        if let data = UserDefaults.standard.data(forKey: Self.storeKey),
           let saved = try? JSONDecoder().decode([ChatMessage].self, from: data) {
            messages = saved
        }
    }

    func send(_ question: String, server: String) {
        let text = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        guard let url = URL(string: server) else {
            messages.append(ChatMessage(role: .assistant, content: "", error: "The server address in Settings isn't valid."))
            return
        }

        messages.append(ChatMessage(role: .user, content: text))
        let history = messages
        let reply = ChatMessage(role: .assistant, content: "")
        messages.append(reply)
        busy = true
        status = "Thinking…"

        current = Task {
            do {
                for try await event in ChatClient(baseURL: url).ask(history) {
                    switch event {
                    case .text(let piece):
                        update(reply.id) { $0.content += piece }
                        status = nil
                    case .status(let label):
                        update(reply.id) {
                            if !$0.content.isEmpty, !$0.content.hasSuffix("\n") { $0.content += "\n\n" }
                        }
                        status = label
                    case .error(let message):
                        update(reply.id) { $0.error = message }
                    case .done:
                        break
                    }
                }
                // A cancelled stream just ends, without throwing
                if Task.isCancelled { update(reply.id) { $0.error = "Stopped." } }
            } catch is CancellationError {
                update(reply.id) { $0.error = "Stopped." }
            } catch let error as URLError where error.code == .cancelled {
                update(reply.id) { $0.error = "Stopped." }
            } catch {
                update(reply.id) { $0.error = "I couldn't reach the house's assistant. Are you on the home Wi-Fi?" }
            }
            update(reply.id) { $0.content = $0.content.trimmingCharacters(in: .whitespacesAndNewlines) }
            status = nil
            busy = false
            save()
        }
    }

    /// Changes one message in place; does nothing if "New chat" removed it meanwhile.
    private func update(_ id: UUID, _ change: (inout ChatMessage) -> Void) {
        guard let i = messages.firstIndex(where: { $0.id == id }) else { return }
        change(&messages[i])
    }

    func stop() {
        current?.cancel()
    }

    func newChat() {
        stop()
        messages = []
        save()
    }

    private func save() {
        let recent = Array(messages.suffix(Self.keep))
        if let data = try? JSONEncoder().encode(recent) {
            UserDefaults.standard.set(data, forKey: Self.storeKey)
        }
    }
}

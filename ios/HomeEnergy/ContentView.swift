import SwiftUI

struct ContentView: View {
    @StateObject private var chat = ChatModel()
    @AppStorage("server") private var server = "http://percula.local:8080"
    @State private var draft = ""
    @State private var showingSettings = false
    @FocusState private var typing: Bool

    private let suggestions = [
        "How full are the batteries?",
        "How much sun did we get today?",
        "Is everything working OK?",
        "Are we ready if the power goes out?",
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                conversation
                Divider()
                inputBar
            }
            .navigationTitle("Home Energy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showingSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("New chat") { chat.newChat() }
                        .disabled(chat.messages.isEmpty)
                }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView(server: $server)
            }
        }
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if chat.messages.isEmpty {
                        welcome
                    }
                    ForEach(chat.messages) { message in
                        Bubble(message: message,
                               status: message.id == chat.messages.last?.id ? chat.status : nil)
                            .id(message.id)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: chat.messages) { _, _ in
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
        }
    }

    private var welcome: some View {
        VStack(spacing: 12) {
            Text("☀️").font(.system(size: 48))
            Text("Hi! Ask me about the house's power.")
                .font(.title2.bold())
                .multilineTextAlignment(.center)
            Text("The batteries, the solar panels, the weather or the grid. Just type a question.")
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            VStack(spacing: 8) {
                ForEach(suggestions, id: \.self) { question in
                    Button(question) { chat.send(question, server: server) }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                }
            }
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 32)
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask a question…", text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .focused($typing)
                .submitLabel(.send)
                .onSubmit(submit)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20))
            if chat.busy {
                Button { chat.stop() } label: {
                    Image(systemName: "stop.circle.fill").font(.system(size: 34))
                }
                .accessibilityLabel("Stop")
            } else {
                Button { submit() } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.system(size: 34))
                }
                .tint(.orange)
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func submit() {
        let question = draft
        draft = ""
        chat.send(question, server: server)
    }
}

struct Bubble: View {
    let message: ChatMessage
    let status: String?

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            VStack(alignment: .leading, spacing: 6) {
                if !message.content.isEmpty {
                    Text(message.role == .user ? AttributedString(message.content) : Self.render(message.content))
                        .textSelection(.enabled)
                }
                if let status {
                    Text(status).font(.subheadline).italic().foregroundStyle(.secondary)
                }
                if let error = message.error {
                    Text(error).font(.subheadline).foregroundStyle(.red)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .foregroundStyle(message.role == .user ? Color.white : Color.primary)
            .background(message.role == .user ? Color.blue : Color(.secondarySystemBackground),
                        in: RoundedRectangle(cornerRadius: 18))
            if message.role == .assistant { Spacer(minLength: 40) }
        }
    }

    /// The answers use **bold** and "- " bullets only; show bullets as • and keep line breaks.
    static func render(_ text: String) -> AttributedString {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).map { line -> String in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            for marker in ["- ", "* "] where trimmed.hasPrefix(marker) {
                return "•  " + trimmed.dropFirst(marker.count)
            }
            return trimmed.hasPrefix("#") ? trimmed.drop(while: { $0 == "#" || $0 == " " }).description : String(line)
        }
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        let joined = lines.joined(separator: "\n")
        return (try? AttributedString(markdown: joined, options: options)) ?? AttributedString(joined)
    }
}

struct SettingsView: View {
    @Binding var server: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://percula.local:8080", text: $server)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Home server")
                } footer: {
                    Text("Only works on the home Wi-Fi. If percula.local doesn't work, use percula's IP address, "
                         + "like http://192.168.1.20:8080.")
                }
                Section {
                    Button("Reset to default") { server = "http://percula.local:8080" }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}

#Preview {
    ContentView()
}

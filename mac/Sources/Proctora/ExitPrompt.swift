import AppKit
import SwiftUI

final class ExitPromptModel: ObservableObject {
    @Published var code = ""
    @Published var checking = false
    @Published var error: String?
    @Published var offerOffline = false
    var onSubmit: (String) -> Void = { _ in }
    var onCancel: () -> Void = {}
    var onOffline: () -> Void = {}
}

struct ExitPromptView: View {
    @ObservedObject var model: ExitPromptModel
    @FocusState private var focused: Bool
    private let accent = Color(red: 0.145, green: 0.388, blue: 0.922)

    var body: some View {
        VStack(spacing: 14) {
            Text("Invigilator exit")
                .font(.system(size: 17, weight: .semibold))
            Text(Config.isDevBuild ? "Development test: enter exactly 0000, then choose Exit exam. Instructor codes have 6 digits." : "Ask your invigilator or instructor for the 6-digit exit code, then choose Exit exam.")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
            TextField("", text: $model.code)
                .textFieldStyle(.plain)
                .font(.system(size: 28, weight: .medium, design: .monospaced))
                .multilineTextAlignment(.center)
                .focused($focused)
                .disabled(model.checking)
                .padding(.vertical, 8)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.black.opacity(0.04)))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .stroke(model.error == nil ? Color.black.opacity(0.12) : Color.red.opacity(0.7), lineWidth: 1))
                .accessibilityLabel(Config.isDevBuild ? "Exit code: 4 digits for development test or 6 digits from instructor" : "Invigilator exit code, 6 digits")
                .onChange(of: model.code) { new in
                    let clean = ExitCodeInput.digitsOnly(new)
                    if clean != new { model.code = clean; return }
                    if model.error != nil && !clean.isEmpty { model.error = nil; model.offerOffline = false }
                    // Submit only on the explicit button or Return, never mid-typing.
                }
            VStack(spacing: 2) {
                if let error = model.error {
                    Text(error)
                        .font(.system(size: 12))
                        .foregroundColor(.red)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if model.offerOffline {
                    Button("Use the offline code") { model.onOffline() }
                        .buttonStyle(.link)
                        .font(.system(size: 11))
                }
            }
            .frame(minHeight: 30, alignment: .top)
            HStack(spacing: 10) {
                Spacer()
                Button("Cancel") { model.onCancel() }
                    .keyboardShortcut(.cancelAction)
                    .disabled(false)
                Button(action: submit) {
                    HStack(spacing: 6) {
                        if model.checking { ProgressView().controlSize(.small) }
                        Text("Exit exam")
                    }
                    .frame(minWidth: 84)
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .tint(accent)
                .disabled(model.checking || !ExitCodeInput.canSubmit(model.code, allowShort: Config.isDevBuild))
            }
        }
        .padding(24)
        .frame(width: 360)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white))
        .environment(\.colorScheme, .light)
        .padding(20)
        .shadow(color: Color.black.opacity(0.18), radius: 14, x: 0, y: 4)
        .onAppear { focused = true }
    }

    private func submit() {
        guard !model.checking, ExitCodeInput.canSubmit(model.code, allowShort: Config.isDevBuild) else { return }
        model.onSubmit(model.code)
    }
}

private final class ExitPromptPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) {
        (contentView as? NSHostingView<ExitPromptView>)?.rootView.model.onCancel()
    }
}

enum ExitPrompt {
    static func makePanel(model: ExitPromptModel) -> NSPanel {
        let host = NSHostingView(rootView: ExitPromptView(model: model))
        let panel = ExitPromptPanel(contentRect: NSRect(origin: .zero, size: host.fittingSize),
                                    styleMask: [.borderless], backing: .buffered, defer: false)
        panel.contentView = host
        panel.setContentSize(host.fittingSize)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        return panel
    }
}

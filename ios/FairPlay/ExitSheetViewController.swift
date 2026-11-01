import UIKit
import FairPlayCore

/// The invigilator exit prompt: one card with a 6-digit field. ExamViewController checks
/// the code and answers through `finish(result:waitSeconds:)`.
final class ExitSheetViewController: UIViewController, UITextFieldDelegate {

    var onSubmit: ((String) -> Void)?
    var onOffline: (() -> Void)?
    var onCancel: (() -> Void)?
    var blockedUntil = Date.distantPast
    var extraAccepts: ((String) -> Bool)?

    private let field = UITextField()
    private let errorLabel = UILabel()
    private let offlineButton = UIButton(type: .system)
    private let cancelButton = UIButton(type: .system)
    private let exitButton = UIButton(type: .system)
    private let spinner = UIActivityIndicatorView(style: .medium)
    private var busy = false
    private var ticker: Timer?
    private var lastResult: Message?

    private typealias Message = ExitSheetLogic.Message

    private static let accent = UIColor(red: 0.145, green: 0.388, blue: 0.922, alpha: 1)
    private static let red = UIColor(red: 0.75, green: 0.15, blue: 0.15, alpha: 1)
    private static let ink = UIColor(red: 0.11, green: 0.15, blue: 0.20, alpha: 1)

    init() {
        super.init(nibName: nil, bundle: nil)
        modalPresentationStyle = .overFullScreen
        modalTransitionStyle = .crossDissolve
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor.black.withAlphaComponent(0.35)
        view.accessibilityViewIsModal = true

        let card = UIView()
        card.backgroundColor = .white
        card.layer.cornerRadius = 14
        card.layer.shadowColor = UIColor.black.cgColor
        card.layer.shadowOpacity = 0.18
        card.layer.shadowRadius = 24
        card.layer.shadowOffset = CGSize(width: 0, height: 8)
        card.translatesAutoresizingMaskIntoConstraints = false
        card.accessibilityIdentifier = "exitSheet"
        view.addSubview(card)

        let title = UILabel()
        title.text = "Invigilator exit"
        title.font = UIFontMetrics(forTextStyle: .title2).scaledFont(for: .systemFont(ofSize: 22, weight: .semibold))
        title.textColor = Self.ink
        title.textAlignment = .center
        title.numberOfLines = 0
        title.adjustsFontForContentSizeCategory = true
        title.accessibilityTraits = .header

        let line = UILabel()
        line.text = "Ask your invigilator or instructor for the exit code."
        line.font = UIFontMetrics(forTextStyle: .body).scaledFont(for: .systemFont(ofSize: 16))
        line.textColor = UIColor(white: 0.35, alpha: 1)
        line.textAlignment = .center
        line.numberOfLines = 0
        line.adjustsFontForContentSizeCategory = true

        field.font = UIFontMetrics(forTextStyle: .largeTitle).scaledFont(for: .monospacedSystemFont(ofSize: 34, weight: .semibold))
        field.adjustsFontForContentSizeCategory = true
        field.textAlignment = .center
        field.textColor = Self.ink
        field.keyboardType = .asciiCapableNumberPad
        field.placeholder = "000000"
        field.autocorrectionType = .no
        field.returnKeyType = .go
        field.backgroundColor = UIColor(red: 0.957, green: 0.961, blue: 0.973, alpha: 1)
        field.layer.cornerRadius = 10
        field.delegate = self
        field.accessibilityIdentifier = "invigilatorCode"
        field.accessibilityLabel = "Exit code, 6 digits"
        field.addTarget(self, action: #selector(textChanged), for: .editingChanged)
        field.heightAnchor.constraint(greaterThanOrEqualToConstant: 64).isActive = true

        errorLabel.font = UIFontMetrics(forTextStyle: .footnote).scaledFont(for: .systemFont(ofSize: 14))
        errorLabel.textColor = Self.red
        errorLabel.textAlignment = .center
        errorLabel.numberOfLines = 0
        errorLabel.adjustsFontForContentSizeCategory = true
        errorLabel.isHidden = true
        errorLabel.accessibilityIdentifier = "exitError"

        offlineButton.setTitle("Use the offline code", for: .normal)
        offlineButton.titleLabel?.font = UIFontMetrics(forTextStyle: .footnote).scaledFont(for: .systemFont(ofSize: 14, weight: .medium))
        offlineButton.titleLabel?.adjustsFontForContentSizeCategory = true
        offlineButton.tintColor = Self.accent
        offlineButton.isHidden = true
        offlineButton.addTarget(self, action: #selector(offlineTapped), for: .touchUpInside)

        var plain = UIButton.Configuration.gray()
        plain.baseForegroundColor = Self.ink
        plain.cornerStyle = .medium
        plain.title = "Cancel"
        plain.titleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { var c = $0; c.font = UIFont.systemFont(ofSize: 17, weight: .medium); return c }
        cancelButton.configuration = plain
        cancelButton.accessibilityLabel = "Cancel"
        cancelButton.addTarget(self, action: #selector(cancelTapped), for: .touchUpInside)

        var primary = UIButton.Configuration.filled()
        primary.baseBackgroundColor = Self.accent
        primary.baseForegroundColor = .white
        primary.cornerStyle = .medium
        primary.title = "Exit exam"
        primary.titleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { var c = $0; c.font = UIFont.systemFont(ofSize: 17, weight: .semibold); return c }
        exitButton.configuration = primary
        exitButton.accessibilityIdentifier = "exitExam"
        exitButton.addTarget(self, action: #selector(submit), for: .touchUpInside)
        exitButton.configurationUpdateHandler = { b in
            var c = b.configuration
            c?.baseBackgroundColor = b.isEnabled ? Self.accent : Self.accent.withAlphaComponent(0.35)
            b.configuration = c
        }

        spinner.hidesWhenStopped = true
        spinner.translatesAutoresizingMaskIntoConstraints = false
        exitButton.addSubview(spinner)
        NSLayoutConstraint.activate([spinner.centerYAnchor.constraint(equalTo: exitButton.centerYAnchor),
                                     spinner.trailingAnchor.constraint(equalTo: exitButton.trailingAnchor, constant: -14)])
        spinner.color = .white

        let buttons = UIStackView(arrangedSubviews: [cancelButton, exitButton])
        buttons.axis = .horizontal
        buttons.spacing = 12
        buttons.distribution = .fillEqually
        cancelButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true
        exitButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true

        let stack = UIStackView(arrangedSubviews: [title, line, field, errorLabel, offlineButton, buttons])
        stack.axis = .vertical
        stack.spacing = 14
        stack.setCustomSpacing(20, after: line)
        stack.setCustomSpacing(22, after: offlineButton)
        stack.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(stack)

        let center = card.centerYAnchor.constraint(equalTo: view.centerYAnchor)
        center.priority = .defaultHigh
        let width = card.widthAnchor.constraint(equalToConstant: 440)
        width.priority = .defaultHigh
        NSLayoutConstraint.activate([
            card.centerXAnchor.constraint(equalTo: view.centerXAnchor), center, width,
            card.widthAnchor.constraint(lessThanOrEqualTo: view.widthAnchor, constant: -40),
            card.topAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            card.bottomAnchor.constraint(lessThanOrEqualTo: view.keyboardLayoutGuide.topAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: card.topAnchor, constant: 28),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -24),
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -28),
        ])
        refresh()
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        field.becomeFirstResponder()
        UIAccessibility.post(notification: .screenChanged, argument: field)
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        ticker?.invalidate(); ticker = nil
    }

    private var waitLeft: Int { Int(blockedUntil.timeIntervalSinceNow.rounded(.up)) }

    private func refresh() {
        guard isViewLoaded else { return }
        let wait = waitLeft
        if wait > 0 {
            show(ExitSheetLogic.message(for: .tooMany, waitSeconds: wait))
            if ticker == nil {
                ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                    MainActor.assumeIsolated { self?.refresh() }
                }
            }
        } else {
            ticker?.invalidate(); ticker = nil
            if lastResult == nil || lastResult?.text.hasPrefix("Too many") == true { show(nil) }
        }
        let ok = ExitSheetLogic.canSubmit(field.text ?? "", busy: busy, waitSeconds: wait)
            || (!busy && wait <= 0 && extraAccepts?(field.text ?? "") == true)
        exitButton.isEnabled = ok
        exitButton.accessibilityValue = busy ? "Checking" : nil
        cancelButton.isEnabled = !busy
        field.isEnabled = !busy
        offlineButton.isEnabled = !busy
        busy ? spinner.startAnimating() : spinner.stopAnimating()
    }

    private func show(_ m: Message?) {
        lastResult = m
        let changed = errorLabel.text != m?.text
        errorLabel.text = m?.text
        errorLabel.isHidden = m == nil
        offlineButton.isHidden = !(m?.offersOffline ?? false)
        if changed, let t = m?.text { UIAccessibility.post(notification: .announcement, argument: t) }
    }

    func finish(result: Signing.ExitResult, waitSeconds: Int) {
        busy = false
        if result == .tooMany { blockedUntil = Date().addingTimeInterval(TimeInterval(max(1, waitSeconds))) }
        show(ExitSheetLogic.message(for: result, waitSeconds: waitSeconds))
        if result == .wrong { field.text = "" }
        refresh()
        if !field.isFirstResponder { field.becomeFirstResponder() }
    }

    func showNotice(_ text: String) {
        busy = false
        show(Message(text: text, offersOffline: false))
        refresh()
    }

    @objc private func textChanged() {
        let clean = ExitSheetLogic.digitsOnly(field.text ?? "")
        if field.text != clean { field.text = clean }
        if lastResult != nil, waitLeft <= 0 { show(nil) }
        refresh()
    }

    @objc private func submit() {
        guard exitButton.isEnabled else { return }
        busy = true
        show(nil)
        refresh()
        onSubmit?(field.text ?? "")
    }

    @objc private func cancelTapped() { onCancel?() }
    @objc private func offlineTapped() { onOffline?() }

    func textFieldShouldReturn(_ textField: UITextField) -> Bool {
        submit()
        return false
    }

    override var keyCommands: [UIKeyCommand]? {
        let esc = UIKeyCommand(input: UIKeyCommand.inputEscape, modifierFlags: [], action: #selector(cancelTapped))
        esc.wantsPriorityOverSystemBehavior = true
        return [esc]
    }
}

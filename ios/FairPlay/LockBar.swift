import UIKit

/// The native strip at the top while locked (the status bar is hidden then): lock state,
/// the website exam's title / countdown / "Finish exam" (CONTRACT.md slice 3), time and
/// battery. Pressing and holding it for 3 s opens the invigilator exit.
final class LockBar: UIView {

    static let height: CGFloat = 48

    var onInvigilator: (() -> Void)?
    var onFinish: (() -> Void)?

    private let badge = UILabel()
    private let titleLabel = UILabel()
    private let countdown = UILabel()
    private let finish = UIButton(type: .system)
    private let clock = UILabel()
    private var clockTimer: Timer?

    private static let ink = UIColor(red: 0.11, green: 0.15, blue: 0.20, alpha: 1)
    private static let green = UIColor(red: 0.086, green: 0.475, blue: 0.298, alpha: 1)
    private static let amber = UIColor(red: 0.71, green: 0.34, blue: 0.04, alpha: 1)

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .white
        let line = UIView()
        line.backgroundColor = UIColor(red: 0.886, green: 0.902, blue: 0.925, alpha: 1)
        line.translatesAutoresizingMaskIntoConstraints = false
        addSubview(line)

        badge.font = .systemFont(ofSize: 13, weight: .semibold)
        badge.textColor = LockBar.green
        badge.accessibilityIdentifier = "lockBadge"
        titleLabel.font = .systemFont(ofSize: 15, weight: .semibold)
        titleLabel.textColor = LockBar.ink
        titleLabel.lineBreakMode = .byTruncatingTail
        titleLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        countdown.font = .monospacedDigitSystemFont(ofSize: 17, weight: .semibold)
        countdown.textColor = LockBar.ink
        finish.setTitle("Finish exam", for: .normal)
        finish.titleLabel?.font = .systemFont(ofSize: 15, weight: .semibold)
        finish.addTarget(self, action: #selector(finishTapped), for: .touchUpInside)
        clock.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
        clock.textColor = UIColor(white: 0.4, alpha: 1)

        let spacer = UIView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        let stack = UIStackView(arrangedSubviews: [badge, titleLabel, spacer, countdown, finish, clock])
        stack.axis = .horizontal
        stack.alignment = .center
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: safeAreaLayoutGuide.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: safeAreaLayoutGuide.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: topAnchor),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -1),
            line.leadingAnchor.constraint(equalTo: leadingAnchor), line.trailingAnchor.constraint(equalTo: trailingAnchor),
            line.bottomAnchor.constraint(equalTo: bottomAnchor), line.heightAnchor.constraint(equalToConstant: 1),
        ])
        let hold = UILongPressGestureRecognizer(target: self, action: #selector(held(_:)))
        hold.minimumPressDuration = 3
        addGestureRecognizer(hold)
        accessibilityIdentifier = "lockBar"
        setExternal(title: nil)
        setLockText("Locked", soft: false)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    func setLockText(_ text: String, soft: Bool) {
        badge.text = "🔒 " + text
        badge.textColor = soft ? LockBar.amber : LockBar.green
    }

    /// Website exam: title + countdown + Finish. nil = questions exam (bar shows the lock only).
    func setExternal(title: String?) {
        titleLabel.text = title ?? "FairPlay exam"
        countdown.isHidden = title == nil
        finish.isHidden = title == nil
    }

    func setCountdown(_ text: String, warn: Bool) {
        countdown.text = text
        countdown.textColor = warn ? LockBar.amber : LockBar.ink
    }

    func startClock() {
        clockTimer?.invalidate()
        UIDevice.current.isBatteryMonitoringEnabled = true
        let t = Timer(timeInterval: 15, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tickClock() }
        }
        RunLoop.main.add(t, forMode: .common)
        clockTimer = t
        tickClock()
    }

    func stopClock() { clockTimer?.invalidate(); clockTimer = nil }

    private func tickClock() {
        let time = Date().formatted(date: .omitted, time: .shortened)
        let level = UIDevice.current.batteryLevel
        let battery = level >= 0 ? "  🔋 \(Int((level * 100).rounded()))%" : ""
        let charging = UIDevice.current.batteryState == .charging ? "⚡︎" : ""
        clock.text = time + battery + charging
    }

    @objc private func held(_ g: UILongPressGestureRecognizer) {
        if g.state == .began { onInvigilator?() }
    }

    @objc private func finishTapped() { onFinish?() }
}

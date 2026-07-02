import AppKit

/// Draws the Dock tile while an operation is running: the app icon plus a pulsing
/// amber dot in the lower-left corner. The pulse mirrors the dashboard's own
/// "working" animation (`@keyframes pulse` in dashboard/styles.css): opacity
/// 0.35→1 and scale 1→1.4 over a 1.4s period.
///
/// The tile only re-renders when `NSDockTile.display()` is called, so `DockActivity`
/// drives a timer that redraws while active; this view just renders the current phase.
final class DockTileView: NSView {

    /// Warm amber matching the dashboard's `--busy-ink` (#7B5A1B), brightened a touch
    /// so the dot reads at Dock size. Tune here if it's too loud/quiet.
    private static let dotColor = NSColor(srgbRed: 0.78, green: 0.49, blue: 0.10, alpha: 1.0)

    private static let period: Double = 1.4   // seconds, matches the web animation

    /// When the current run started, used to derive the pulse phase. `nil` == idle.
    var startDate: Date?

    override func draw(_ dirtyRect: NSRect) {
        // Base app icon fills the tile.
        NSApp.applicationIconImage?.draw(in: bounds)

        guard let start = startDate else { return }

        // phase in [0,1], starting near 0 (dim/small) and easing up and back.
        let t = Date().timeIntervalSince(start)
        let phase = (sin(2 * .pi * t / Self.period - .pi / 2) + 1) / 2
        let alpha = 0.35 + 0.65 * phase
        let scale = 1.0 + 0.4 * phase

        let side = min(bounds.width, bounds.height)
        let baseRadius = side * 0.10
        let radius = baseRadius * scale
        // Lower-left, inset so it clears the tile edge and leaves the top-right free
        // for a future badge.
        let center = NSPoint(x: bounds.minX + side * 0.20,
                             y: bounds.minY + side * 0.20)
        let rect = NSRect(x: center.x - radius, y: center.y - radius,
                          width: radius * 2, height: radius * 2)

        let dot = NSBezierPath(ovalIn: rect)
        Self.dotColor.withAlphaComponent(alpha).setFill()
        dot.fill()
        // Thin contrasting outline so the dot reads on any icon background.
        NSColor.black.withAlphaComponent(0.25 * alpha).setStroke()
        dot.lineWidth = max(1, side * 0.01)
        dot.stroke()
    }
}

/// Owns the Dock activity indicator: polls the bridge's `/busy` endpoint and, while an
/// operation is running, shows an animated pulsing dot on the Dock icon. Main-thread only.
final class DockActivity {
    static let shared = DockActivity()

    private let tileView = DockTileView()
    private var animationTimer: Timer?
    private var pollTimer: Timer?
    private var running = false

    private var busyURL: URL {
        BridgeController.shared.dashboardURL.appendingPathComponent("busy")
    }

    private init() {}

    // MARK: - Polling

    /// Begins polling `/busy` (~1.2s). Safe to call more than once.
    func startPolling() {
        guard pollTimer == nil else { return }
        let timer = Timer(timeInterval: 1.2, repeats: true) { [weak self] _ in
            self?.poll()
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
        poll() // fire immediately so the Dock reflects state without a 1.2s lag
    }

    func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
        setRunning(false)
    }

    private func poll() {
        var req = URLRequest(url: busyURL)
        req.timeoutInterval = 1.0
        URLSession.shared.dataTask(with: req) { [weak self] data, resp, _ in
            guard let self,
                  let http = resp as? HTTPURLResponse, http.statusCode == 200,
                  let data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let isRunning = obj["running"] as? Bool
            else { return } // swallow errors → treat as "no change" (e.g. bridge restarting)
            let pending = obj["pending"] as? Int ?? 0
            DispatchQueue.main.async {
                self.setRunning(isRunning)
                self.setPending(pending)
            }
        }.resume()
    }

    /// The red count badge (top-right) for raw files awaiting ingestion. Drawn by the
    /// system over any custom `contentView`, so it coexists with the lower-left dot.
    func setPending(_ count: Int) {
        NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil
    }

    // MARK: - Dock tile

    /// Toggles the animated Dock dot. No-op when the state is unchanged, so the pulse
    /// start-time isn't reset on every poll.
    func setRunning(_ isRunning: Bool) {
        guard isRunning != running else { return }
        running = isRunning

        let tile = NSApp.dockTile
        if isRunning {
            tileView.frame = NSRect(x: 0, y: 0, width: 128, height: 128)
            tileView.startDate = Date()
            tile.contentView = tileView

            let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { _ in
                NSApp.dockTile.display()
            }
            RunLoop.main.add(timer, forMode: .common)
            animationTimer = timer
        } else {
            animationTimer?.invalidate()
            animationTimer = nil
            tileView.startDate = nil
            tile.contentView = nil // restore the plain icon (and any future badgeLabel)
        }
        tile.display()
    }
}

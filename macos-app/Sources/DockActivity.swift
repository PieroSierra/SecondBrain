import AppKit

/// Draws the Dock tile while an operation is running: the app icon plus a pulsing
/// dot in the lower-left corner. The pulse mirrors the dashboard's own "working"
/// animation (`@keyframes pulse` in dashboard/styles.css): the fill travels from
/// ink to the accent while the dot scales 0.7→1, over a 1.4s period. Opacity is
/// constant — the colour carries the pulse, so the dot never fades to invisible.
///
/// The tile only re-renders when `NSDockTile.display()` is called, so `DockActivity`
/// drives a timer that redraws while active; this view just renders the current phase.
final class DockTileView: NSView {

    /// Trough and peak of the pulse, matching the dashboard's `--ink` (#191B1F)
    /// and `--clay` (#DB2777). Keep these in step with styles.css `:root`.
    private static let dotInk   = NSColor(srgbRed: 0x19 / 255.0, green: 0x1B / 255.0,
                                          blue: 0x1F / 255.0, alpha: 1.0)
    private static let dotClay  = NSColor(srgbRed: 0xDB / 255.0, green: 0x27 / 255.0,
                                          blue: 0x77 / 255.0, alpha: 1.0)

    private static let period: Double = 1.4   // seconds, matches the web animation

    /// When the current run started, used to derive the pulse phase. `nil` == idle.
    var startDate: Date?

    override func draw(_ dirtyRect: NSRect) {
        // Base app icon fills the tile.
        NSApp.applicationIconImage?.draw(in: bounds)

        guard let start = startDate else { return }

        // phase in [0,1], starting near 0 (small/ink) and easing up and back.
        let t = Date().timeIntervalSince(start)
        let phase = (sin(2 * .pi * t / Self.period - .pi / 2) + 1) / 2
        let scale = 0.7 + 0.3 * phase   // peaks at 1 = native size, as in CSS

        let side = min(bounds.width, bounds.height)
        // Sized so the peak (scale 1) matches the previous dot's largest state.
        let baseRadius = side * 0.14
        let radius = baseRadius * scale
        // Lower-left, inset so it clears the tile edge and leaves the top-right free
        // for a future badge.
        let center = NSPoint(x: bounds.minX + side * 0.20,
                             y: bounds.minY + side * 0.20)
        let rect = NSRect(x: center.x - radius, y: center.y - radius,
                          width: radius * 2, height: radius * 2)

        let dot = NSBezierPath(ovalIn: rect)
        // Lerp ink → accent in sRGB, matching the CSS keyframe's colour travel.
        (Self.dotInk.blended(withFraction: phase, of: Self.dotClay) ?? Self.dotClay).setFill()
        dot.fill()
        // Light outline, not dark: the dot is near-black at the trough, so a black
        // ring would disappear into it. White separates it at both ends of the pulse.
        NSColor.white.withAlphaComponent(0.55).setStroke()
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

import AppKit

/// Supervises the Python bridge (`dashboard/bridge.py`) as a child process.
///
/// Responsibilities, and nothing more:
///   • adopt an already-running bridge, or spawn one via a login shell (for PATH),
///   • poll `/healthz` until the dashboard is reachable,
///   • kill the bridge on quit (SIGTERM → SIGKILL) — but never a bridge we didn't
///     start ourselves.
final class BridgeController {
    static let shared = BridgeController()

    private var process: Process?
    /// True when we adopted a bridge someone else started (e.g. `./run.sh`). We must
    /// not kill it on quit.
    private var externallyOwned = false

    private let logURL: URL
    private let logQueue = DispatchQueue(label: "com.secondbrain.bridge.log")

    var port: Int { Preferences.port }
    var dashboardURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    private var healthURL: URL { URL(string: "http://127.0.0.1:\(port)/healthz")! }

    private init() {
        let dir = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SecondBrain", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        logURL = dir.appendingPathComponent("bridge.log")
    }

    // MARK: - Lifecycle

    /// Starts (or adopts) the bridge. `ready` fires on the main queue once the
    /// dashboard responds; `failed` fires on the main queue with a human message.
    func start(ready: @escaping () -> Void, failed: @escaping (String) -> Void) {
        // Probe off the main thread so launch never stalls the UI.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            if self.probeHealth(timeout: 0.6) {
                self.externallyOwned = true
                DispatchQueue.main.async(execute: ready)
                return
            }
            // Spawning may need to prompt for the vault → back to main.
            DispatchQueue.main.async { self.spawn(ready: ready, failed: failed) }
        }
    }

    /// Main thread. Resolves the vault (may show a picker), launches the bridge, then
    /// polls for readiness on a background queue.
    private func spawn(ready: @escaping () -> Void, failed: @escaping (String) -> Void) {
        guard let vault = Preferences.resolveVault() else {
            failed("No Second Brain vault was selected.")
            return
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        // An INTERACTIVE login shell (`-il`) so the child inherits the user's real PATH.
        // A GUI app launches from launchd with a bare PATH; `python3` (/opt/homebrew/bin)
        // and `claude` (~/.local/bin) are only on PATH once the shell's rc files run.
        // `-l` alone is not enough: many setups (including this vault's owner) add their
        // PATH in ~/.zshrc, which zsh sources only for INTERACTIVE shells — hence `-i`.
        // The bridge execs its agent via execvp (shell=False), so shell *aliases* don't
        // apply; only PATH matters, and an interactive shell builds the full one.
        // The vault path and port are passed as $0/$1 to avoid any shell quoting.
        p.arguments = [
            "-ilc",
            #"exec python3 "$0/dashboard/bridge.py" --port "$1" --no-open"#,
            vault.path,
            String(port),
        ]
        p.currentDirectoryURL = vault

        // Select the agent engine. Only inject when the user has made an explicit
        // menu choice; otherwise leave it unset so the bridge honors the vault's
        // .env / its own default. A real env var beats .env (the loader uses
        // setdefault), so this override always wins when present.
        if let engine = Preferences.engineChoice, Preferences.isValidEngine(engine) {
            var env = ProcessInfo.processInfo.environment
            env["AGENT_ENGINE"] = engine
            p.environment = env
        }

        // Tee stdout+stderr to a log file so startup failures are diagnosable.
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: logURL) {
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            pipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
                let data = fh.availableData
                guard !data.isEmpty else { return }
                self?.logQueue.async { try? handle.write(contentsOf: data) }
            }
        }

        do {
            try p.run()
        } catch {
            failed("Couldn't launch the bridge:\n\(error.localizedDescription)")
            return
        }
        process = p
        externallyOwned = false

        // Poll /healthz up to ~15s.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let deadline = Date().addingTimeInterval(15)
            while Date() < deadline {
                if !p.isRunning {
                    let tail = self.logTail()
                    DispatchQueue.main.async {
                        failed("The bridge exited during startup.\n\n\(tail)")
                    }
                    return
                }
                if self.probeHealth(timeout: 0.4) {
                    DispatchQueue.main.async(execute: ready)
                    return
                }
                Thread.sleep(forTimeInterval: 0.2)
            }
            let tail = self.logTail()
            DispatchQueue.main.async {
                failed("The bridge didn't become ready in time.\n\n\(tail)")
            }
        }
    }

    /// Stops the bridge if we own it. Blocks briefly so the port is freed before quit.
    ///
    /// SIGTERM (not SIGINT) is the primary signal: the bridge only installs a
    /// KeyboardInterrupt/SIGINT handler, and that handler does not fire reliably when
    /// the process runs detached from a terminal (verified under Python 3.14). SIGTERM's
    /// default disposition terminates Python immediately and the OS releases the socket
    /// in ~50 ms; the bridge sets SO_REUSEADDR, so a relaunch rebinds the port at once.
    func stop() {
        guard !externallyOwned, let p = process, p.isRunning else { return }
        let pid = p.processIdentifier

        p.terminate() // SIGTERM — fast, reliable
        if waitForExit(p, timeout: 1.5) { process = nil; return }

        kill(pid, SIGKILL) // last resort
        process = nil
    }

    /// Restarts the bridge under a new engine. Persists the choice, frees the port
    /// (stopping our own bridge or reclaiming a foreign one so the switch actually
    /// takes effect), then spawns fresh with the new engine injected. `ready`/`failed`
    /// fire on the main queue.
    func switchEngine(to engine: String, ready: @escaping () -> Void,
                      failed: @escaping (String) -> Void) {
        Preferences.engineChoice = engine
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            if let p = self.process, p.isRunning {
                p.terminate()
                if !self.waitForExit(p, timeout: 1.5) { kill(p.processIdentifier, SIGKILL) }
                self.process = nil
            } else if self.externallyOwned {
                self.reclaimPort()
            }
            self.externallyOwned = false
            // spawn() launches quickly and does its own background readiness poll.
            DispatchQueue.main.async { self.spawn(ready: ready, failed: failed) }
        }
    }

    // MARK: - Helpers

    /// SIGTERMs whatever is listening on our port (mirrors run.sh). Background only.
    private func reclaimPort() {
        let lsof = Process()
        lsof.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        lsof.arguments = ["-ti", "tcp:\(port)", "-sTCP:LISTEN"]
        let out = Pipe()
        lsof.standardOutput = out
        lsof.standardError = Pipe()
        guard (try? lsof.run()) != nil else { return }
        lsof.waitUntilExit()
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(),
                          encoding: .utf8) ?? ""
        for line in text.split(whereSeparator: { $0 == "\n" }) {
            if let pid = Int32(line.trimmingCharacters(in: .whitespaces)) { kill(pid, SIGTERM) }
        }
        // Wait for the socket to drain before the caller rebinds.
        let deadline = Date().addingTimeInterval(2)
        while Date() < deadline, probeHealth(timeout: 0.2) { Thread.sleep(forTimeInterval: 0.1) }
    }

    private func waitForExit(_ p: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if !p.isRunning { return true }
            Thread.sleep(forTimeInterval: 0.05)
        }
        return !p.isRunning
    }

    /// Synchronous GET /healthz. Background threads only. Returns true on HTTP 200.
    private func probeHealth(timeout: TimeInterval) -> Bool {
        var req = URLRequest(url: healthURL)
        req.timeoutInterval = timeout
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        let task = URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 { ok = true }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + timeout + 0.3)
        return ok
    }

    private func logTail(_ maxBytes: Int = 2000) -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "" }
        return String(data: data.suffix(maxBytes), encoding: .utf8) ?? ""
    }
}

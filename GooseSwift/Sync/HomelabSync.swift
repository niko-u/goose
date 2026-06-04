//
//  HomelabSync.swift
//  GooseSwift
//
//  Posts readings + workouts to a self-hosted goose.compute.casa instance.
//  Authenticates via HMAC-SHA256 over the JSON body using a shared secret
//  stored in the iOS Keychain. Fire-and-forget — never blocks UI.
//
//  Drop this file into the GooseSwift target. Call HomelabSync.shared.syncReading(...)
//  from wherever WHOOP packet-derived scores land (e.g. runPacketScores in
//  HealthDataStore+Snapshots.swift, after packetScoreReports is updated).
//

import CryptoKit
import Foundation
import Security

// MARK: - Reading payload

struct GooseReading: Codable {
  var device_id: String
  var recorded_at: String           // ISO8601, e.g. "2026-06-02T18:00:00Z"
  var recovery_pct: Int?
  var hrv_ms: Double?
  var rhr_bpm: Int?
  var strain: Double?
  var sleep_hours: Double?
  var sleep_performance_pct: Int?
  var skin_temp_c: Double?
}

struct GooseWorkout: Codable {
  var device_id: String
  var started_at: String
  var ended_at: String?
  var strain: Double?
  var avg_hr: Int?
  var max_hr: Int?
  var kilojoules: Double?
  var activity_label: String?
}

// MARK: - Keychain helper

enum HomelabKeychain {
  static let service = "casa.compute.goose"

  static func set(_ value: String, key: String) {
    let data = Data(value.utf8)
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: key,
    ]
    SecItemDelete(query as CFDictionary)
    var add = query
    add[kSecValueData as String] = data
    add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
    SecItemAdd(add as CFDictionary, nil)
  }

  static func get(_ key: String) -> String? {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: key,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne,
    ]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess, let data = item as? Data, let s = String(data: data, encoding: .utf8) else {
      return nil
    }
    return s
  }

  static func clear(_ key: String) {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: key,
    ]
    SecItemDelete(query as CFDictionary)
  }
}

// MARK: - Configuration store

final class HomelabSyncConfig: ObservableObject {
  static let shared = HomelabSyncConfig()

  private let urlKey = "homelab_url"
  private let secretKey = "homelab_secret"
  private let enabledKey = "homelab_enabled"

  @Published var url: String {
    didSet { UserDefaults.standard.set(url, forKey: urlKey) }
  }

  @Published var enabled: Bool {
    didSet { UserDefaults.standard.set(enabled, forKey: enabledKey) }
  }

  /// HMAC secret; lives in Keychain, never in UserDefaults.
  var secret: String {
    get { HomelabKeychain.get(secretKey) ?? "" }
    set { HomelabKeychain.set(newValue, key: secretKey) }
  }

  private init() {
    self.url = UserDefaults.standard.string(forKey: urlKey) ?? "https://goose.compute.casa"
    self.enabled = UserDefaults.standard.bool(forKey: enabledKey)
    bootstrapFromBundleIfNeeded()
  }

  /// One-shot bootstrap: if there's no saved secret yet and the app bundle ships
  /// a HomelabSync.plist, hydrate config from it. Lets the user wire up sync by
  /// just dropping a plist into the Xcode project — no Settings UI required.
  private func bootstrapFromBundleIfNeeded() {
    guard HomelabKeychain.get(secretKey) == nil else { return }
    guard let plistURL = Bundle.main.url(forResource: "HomelabSync", withExtension: "plist"),
          let data = try? Data(contentsOf: plistURL),
          let plist = try? PropertyListSerialization.propertyList(from: data, format: nil),
          let dict = plist as? [String: Any] else { return }

    if let urlString = dict["url"] as? String, !urlString.isEmpty {
      self.url = urlString
    }
    var didSetSecret = false
    if let secretString = dict["secret"] as? String, !secretString.isEmpty {
      self.secret = secretString
      didSetSecret = true
    }
    if let enabledFlag = dict["enabled"] as? Bool {
      self.enabled = enabledFlag
    } else if didSetSecret {
      self.enabled = true
    }
  }

  var deviceID: String {
    let key = "homelab_device_id"
    if let existing = UserDefaults.standard.string(forKey: key) { return existing }
    let new = "goose-ios-" + UUID().uuidString.prefix(8)
    UserDefaults.standard.set(String(new), forKey: key)
    return String(new)
  }
}

// MARK: - Sync engine

final class HomelabSync {
  static let shared = HomelabSync()
  private init() {}

  private let session: URLSession = {
    let cfg = URLSessionConfiguration.default
    cfg.timeoutIntervalForRequest = 8
    cfg.waitsForConnectivity = true
    return URLSession(configuration: cfg)
  }()

  private let iso: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
  }()

  /// Fire-and-forget POST of a single reading. Safe to call from any thread.
  func syncReading(_ reading: GooseReading) {
    let cfg = HomelabSyncConfig.shared
    guard cfg.enabled, !cfg.url.isEmpty, !cfg.secret.isEmpty else { return }
    send(path: "/api/ingest/reading", body: reading, cfg: cfg)
  }

  func syncWorkout(_ workout: GooseWorkout) {
    let cfg = HomelabSyncConfig.shared
    guard cfg.enabled, !cfg.url.isEmpty, !cfg.secret.isEmpty else { return }
    send(path: "/api/ingest/workout", body: workout, cfg: cfg)
  }

  /// Convenience: sync the most recent packet-derived scores from HealthDataStore.
  /// Caller passes individual numeric fields so this layer stays decoupled
  /// from Goose's internal model types.
  func syncCurrentScores(
    recovery: Int? = nil,
    hrv: Double? = nil,
    rhr: Int? = nil,
    strain: Double? = nil,
    sleepHours: Double? = nil,
    sleepPerformance: Int? = nil,
    skinTempC: Double? = nil
  ) {
    let cfg = HomelabSyncConfig.shared
    let reading = GooseReading(
      device_id: cfg.deviceID,
      recorded_at: iso.string(from: Date()),
      recovery_pct: recovery,
      hrv_ms: hrv,
      rhr_bpm: rhr,
      strain: strain,
      sleep_hours: sleepHours,
      sleep_performance_pct: sleepPerformance,
      skin_temp_c: skinTempC
    )
    syncReading(reading)
  }

  // MARK: - Internal

  private func send<T: Encodable>(path: String, body: T, cfg: HomelabSyncConfig) {
    guard let base = URL(string: cfg.url), let url = URL(string: path, relativeTo: base) else { return }
    let encoder = JSONEncoder()
    // The server re-serialises with stable key order via JSON.stringify; to avoid
    // signature mismatch from differing field order, we encode with sorted keys.
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(body) else { return }

    // HMAC-SHA256 over the exact bytes we send
    let key = SymmetricKey(data: Data(cfg.secret.utf8))
    let mac = HMAC<SHA256>.authenticationCode(for: data, using: key)
    let sigHex = mac.map { String(format: "%02x", $0) }.joined()

    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.setValue(sigHex, forHTTPHeaderField: "X-Goose-Signature")
    req.httpBody = data

    let task = session.uploadTask(with: req, from: data) { _, response, error in
      if let error = error {
        NSLog("[HomelabSync] failed: \(error.localizedDescription)")
      } else if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
        NSLog("[HomelabSync] HTTP \(http.statusCode)")
      }
    }
    task.resume()
  }
}

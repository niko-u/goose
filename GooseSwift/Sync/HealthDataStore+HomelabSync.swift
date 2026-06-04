//
//  HealthDataStore+HomelabSync.swift
//  GooseSwift
//
//  Bridges Goose's internal HealthDataStore to HomelabSync. Adds one method:
//  `syncLatestScoresToHomelab()` — call it from wherever Goose finishes
//  recomputing scores (e.g. end of `runPacketScores()` in
//  HealthDataStore+Snapshots.swift) once you've verified the app works on
//  device. No existing Goose source needs to change to compile this file;
//  it only adds a method.
//

import Foundation

extension HealthDataStore {

  /// Pulls the latest recovery / strain / sleep / HRV / RHR numbers from
  /// `packetScoreReports`, packages them as a GooseReading, and fires
  /// HomelabSync.shared.syncReading(...). Fire-and-forget.
  @MainActor
  func syncLatestScoresToHomelab() {
    let cfg = HomelabSyncConfig.shared
    guard cfg.enabled, !cfg.secret.isEmpty else { return }

    // Pluck values out of the packet score reports using the same helpers Goose
    // uses internally (Self.map / Self.doubleValue / Self.intValue).
    let recovery0to100 = Self.doubleValue(
      Self.map(packetScoreReports["recovery"], "score_result", "output")?["score_0_to_100"]
    )
    let strain0to21 = Self.doubleValue(
      Self.map(packetScoreReports["strain"], "score_result", "output")?["score_0_to_21"]
    )
    let sleepHours = Self.doubleValue(
      Self.map(packetScoreReports["sleep"], "score_result", "output")?["sleep_hours"]
    )
    let sleepPerf = Self.doubleValue(
      Self.map(packetScoreReports["sleep"], "score_result", "output")?["sleep_performance_pct"]
    )

    // Pull HRV + RHR from the values UserDefaults that HealthDataStore caches.
    let hrv = UserDefaults.standard.double(forKey: Self.liveHRVRMSSDDefaultsKey)
    let rhr = UserDefaults.standard.double(forKey: Self.restingHeartRateEstimateBPMDefaultsKey)

    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime]

    let reading = GooseReading(
      device_id: cfg.deviceID,
      recorded_at: iso.string(from: Date()),
      recovery_pct: recovery0to100 != nil ? Int(recovery0to100!.rounded()) : nil,
      hrv_ms: hrv > 0 ? hrv : nil,
      rhr_bpm: rhr > 0 ? Int(rhr.rounded()) : nil,
      strain: strain0to21,
      sleep_hours: sleepHours,
      sleep_performance_pct: sleepPerf != nil ? Int(sleepPerf!.rounded()) : nil,
      skin_temp_c: nil
    )

    // Only send if we actually have at least one meaningful value.
    let anyValue =
      reading.recovery_pct != nil ||
      reading.hrv_ms != nil ||
      reading.strain != nil ||
      reading.sleep_hours != nil
    guard anyValue else { return }

    HomelabSync.shared.syncReading(reading)
  }
}

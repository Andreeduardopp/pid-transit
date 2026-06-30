# GTFS import benchmark driver — runs Import::Gtfs synchronously (inline,
# bypassing the delayed_job enqueue) and prints the operation wall time the
# harness records as T_exec, cross-checkable against this in-process timer.
#
# Usage: rails runner /bench/import.rb [workbench_id] [feed_path]
wb_id    = (ARGV[0].presence || File.read('/out/workbench_id')).to_i
feed     = ARGV[1].presence || '/feed/porto.zip'
wb       = Workbench.find(wb_id)

# Each import rep must start from a clean slate: a fresh import of the same feed
# produces a referential with the same validity period, which chouette rejects as
# overlapping an existing one. Purge prior referentials (drops their tenant
# schemas) and prior import records so every rep is independent.
wb.referentials.find_each do |r|
  r.update_column(:ready, false) rescue nil
  r.destroy
end
wb.imports.destroy_all rescue nil

# Persisted (create!) like the specs — the line-saving path needs the import id /
# resources. The after_commit :import_async only enqueues a delayed_job row; no
# worker exists, so the real work runs only via our explicit import_without_status.
import = Import::Gtfs.create!(
  workbench:  wb,
  local_file: File.open(feed),
  creator:    'bench',
  name:       "bench-import-#{Process.pid}",
)

# Use the real wrapped #import (status + processor.around + AR cache + referential
# activation) — the exact path the delayed_job worker runs, minus the enqueue.
t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
import.import
t1 = Process.clock_gettime(Process::CLOCK_MONOTONIC)

ref = import.referential
File.write('/out/referential_id', ref.id.to_s) if ref

puts "OP_SECONDS=#{(t1 - t0).round(3)}"
puts "REFERENTIAL_ID=#{ref&.id}"
puts "IMPORT_STATUS=#{import.status.inspect}"

if ref
  ref.switch do
    puts "VEHICLE_JOURNEYS=#{Chouette::VehicleJourney.count}"
    puts "JOURNEY_PATTERNS=#{Chouette::JourneyPattern.count}"
    puts "VJAS_PASSING_TIMES=#{Chouette::VehicleJourneyAtStop.count}"
    puts "STOP_POINTS=#{Chouette::StopPoint.count}"
    puts "ROUTES=#{Chouette::Route.count}"
  end
end
puts "IMPORT_OK"

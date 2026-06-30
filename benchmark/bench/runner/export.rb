# NeTEx export benchmark driver — runs Export::NetexGeneric via chouette's real
# SYNCHRONOUS path: create!(synchronous: true) triggers after_commit :launch_worker
# which, in synchronous mode, runs the export inline in the correct persisted
# context (so status ends 'successful'). We time the create! call, which is the
# export work plus a trivial insert.
#
# Usage: rails runner /bench/export.rb [referential_id] [profile]
#   profile: 'european' (EPIP, zipped) | 'none' (plain single XML) — default european
ref_id  = (ARGV[0].presence || File.read('/out/referential_id')).to_i
profile = ARGV[1].presence || 'european'
ref     = Referential.find(ref_id)
wb      = ref.workbench
wg      = wb&.workgroup || Workgroup.first

t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
export = Export::NetexGeneric.create!(
  referential: ref,
  workgroup:   wg,
  workbench:   wb,
  name:        "bench-export-#{Process.pid}",
  creator:     'bench',
  synchronous: true,
  setup: {
    profile: profile,
    scope_setup: { type: 'Export::Setup::Scope::Referential' },
  },
)
t1 = Process.clock_gettime(Process::CLOCK_MONOTONIC)

export.reload
puts "OP_SECONDS=#{(t1 - t0).round(3)}"
puts "EXPORT_STATUS=#{export.status.inspect}"

path = (export.file&.path rescue nil)
if path && File.exist?(path)
  dest = "/out/chouette_netex_#{profile}#{File.extname(path)}"
  FileUtils.cp(path, dest)
  puts "ARTIFACT=#{dest}"
  puts "ARTIFACT_BYTES=#{File.size(dest)}"
else
  puts "ARTIFACT_PATH=#{path.inspect} (not found)"
end
puts "EXPORT_OK"

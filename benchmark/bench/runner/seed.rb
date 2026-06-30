# Seed the Chouette domain graph: Organisation -> Workgroup -> Workbench, with
# the line/stop_area referentials using the generic 'netex' objectid format
# (the factory default 'stif_codifligne' is France-specific and fails to mint an
# objectid for a non-French agency code, which silently drops every line).
# Workbench and Workgroup SHARE the same referentials so the default providers
# (which derive from workgroup.*_referential_id) line up with what the import
# writes into (workbench.*_referential).
#
# FactoryBot lives in the :test bundler group; installed (only :production is
# skipped) but not auto-loaded under RAILS_ENV=development, so bootstrap it here.
require 'factory_bot'
begin; require 'faker'; rescue LoadError; end
FactoryBot.definition_file_paths = [Rails.root.join('spec', 'factories')]
FactoryBot.find_definitions

line_ref = FactoryBot.create(:line_referential, objectid_format: 'netex')
sa_ref   = FactoryBot.create(:stop_area_referential, objectid_format: 'netex')
org      = FactoryBot.create(:organisation)
wg = FactoryBot.create(:workgroup, owner: org,
                       line_referential: line_ref, stop_area_referential: sa_ref)
wb = FactoryBot.create(:workbench, organisation: org, workgroup: wg,
                       line_referential: line_ref, stop_area_referential: sa_ref)

# Persist the default providers the GTFS import needs.
[
  wb.default_line_provider,
  wb.default_stop_area_provider,
  wb.default_shape_provider,
  wb.default_fare_provider,
].each { |p| p.save! }

File.write('/out/workbench_id', wb.id.to_s)
puts "WORKBENCH_ID=#{wb.id}"
puts "WORKGROUP_ID=#{wb.workgroup_id}"
puts "ORGANISATION_ID=#{wb.organisation_id}"
puts "LINE_REF_FORMAT=#{wb.line_referential.objectid_format} SA_REF_FORMAT=#{wb.stop_area_referential.objectid_format}"
puts "WB==WG line_ref? #{wb.line_referential_id == wg.line_referential_id} sa_ref? #{wb.stop_area_referential_id == wg.stop_area_referential_id}"
puts "PROVIDER_LINE_REF=#{wb.default_line_provider.line_referential_id} (wb line_ref=#{wb.line_referential_id})"
puts "SEED_OK"

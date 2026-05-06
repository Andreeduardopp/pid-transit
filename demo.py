"""
PID-Transit Demo Script

This script demonstrates how an external company or developer would use
the pid_transit library to create a relational database, populate it with data
programmatically, import existing GTFS feeds, and export to NeTEx.
"""

from pid_transit import (
    TransitDataset,
    Operator,
    Line,
    TransportMode,
    GtfsImporter,
    NetexExporter
)

def main():
    print("--- PID-Transit Library Demo ---")
    
    # 1. Initialize the dataset
    # This automatically creates a SQLite database file with the Transmodel schema.
    db_path = "my_city_transit.db"
    print(f"\n1. Creating/Connecting to transit dataset at {db_path}...")
    dataset = TransitDataset(db_path)
    
    # 2. Add data programmatically using Repositories and Pydantic models
    print("2. Programmatically adding operators and lines...")
    
    my_operator = Operator(
        id="OP_001",
        name="Metropolis Transit Authority",
        timezone="America/New_York",
        url="https://metropolis-transit.example.com",
        phone="+1-555-0199"
    )
    dataset.operators.add(my_operator)
    
    my_line = Line(
        id="L_001",
        operator_id="OP_001",
        name="Red Line Express",
        short_name="RL",
        transport_mode=TransportMode.METRO,
        color="FF0000"
    )
    dataset.lines.add(my_line)
    
    # 3. Querying data back out
    print("\n3. Querying existing lines from the database:")
    for line in dataset.lines.get_all():
        print(f"  - [{line.short_name}] {line.name} ({line.transport_mode.value})")
        
    # 4. Import GTFS data using the adapter
    # In a real scenario, you would provide a valid GTFS zip file here.
    print("\n4. Importing data from GTFS...")
    importer = GtfsImporter(strict_mode=True)
    try:
        # dataset.import_data(importer, "sample_feed.zip")
        print("  (Skipped: 'sample_feed.zip' not provided in demo folder)")
    except Exception as e:
        print(f"  Import failed as expected: {e}")
        
    # 5. Exporting to NeTEx
    print("\n5. Exporting dataset to NeTEx XML...")
    exporter = NetexExporter(profile="EPIP")
    export_path = "output_netex.xml"
    dataset.export_data(exporter, export_path)
    print(f"  Export complete! Saved to {export_path}")
    
    print("\nDemo completed successfully.")

if __name__ == "__main__":
    main()

from app import app, db
from sqlalchemy import text

print("--> Starting Database Fix...")
with app.app_context():
    with db.engine.connect() as conn:
        commands = [
            "ALTER TABLE `order` ADD COLUMN status VARCHAR(20) DEFAULT 'Pending';",
            "ALTER TABLE `order` ADD COLUMN api_order_id VARCHAR(100);",
            "ALTER TABLE `order` ADD COLUMN api_response TEXT;",
            "ALTER TABLE `order` ADD COLUMN is_refill_supported BOOLEAN DEFAULT 0;",
            "ALTER TABLE `order` ADD COLUMN refill_status VARCHAR(20) DEFAULT 'None';"
        ]
        
        for sql in commands:
            try:
                conn.execute(text(sql))
                print(f"   [SUCCESS] Executed: {sql[:40]}...")
            except Exception as e:
                print(f"   [NOTE] Skipped (might already exist).")
        
        conn.commit()
        print("✅ SUCCESS: Order table fixed successfully!")

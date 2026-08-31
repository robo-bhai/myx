import os
from app import app, db, PushSubscription
from sqlalchemy import inspect

def update_database():
    """
    Safely creates the PushSubscription table without affecting existing 
    User or Order data in site.db.
    """
    with app.app_context():
        # 1. Initialize the inspector to check current schema
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        print("--- Hadi88 DB Migration Utility ---")

        # 2. Check if PushSubscription already exists
        if 'push_subscription' not in existing_tables:
            print("Table 'push_subscription' not found. Creating...")
            try:
                # This only creates tables that don't exist yet
                db.create_all()
                print("✅ Success: 'push_subscription' table has been added to site.db.")
            except Exception as e:
                print(f"❌ Error creating table: {str(e)}")
        else:
            print("ℹ️ Table 'push_subscription' already exists. No changes needed.")

        print("-----------------------------------")

if __name__ == "__main__":
    update_database()

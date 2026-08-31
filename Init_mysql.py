import os
import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app import app, db, User, Transaction, DepositRequest, Order, ExchangeRate

# --- CONFIGURATION FROM SCREENSHOT ---
MYSQL_HOST = "Panelpromax.mysql.pythonanywhere-services.com"
MYSQL_USER = "Panelpromax"
MYSQL_PASS = "Hadi1.5)"  # Replace with your actual password
MYSQL_DB = "Panelpromax$default"

# URIs
SQLITE_URI = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'site.db')
MYSQL_URI = f"mysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}/{MYSQL_DB}?charset=utf8mb4"

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Migration")

def migrate():
    # 1. Setup Engines
    sqlite_engine = create_engine(SQLITE_URI)
    mysql_engine = create_engine(MYSQL_URI)

    # Create MySQL Session
    MysqlSession = sessionmaker(bind=mysql_engine)
    mysql_session = MysqlSession()

    print("[*] Starting Migration: SQLite -> MySQL")

    with app.app_context():
        # 2. Fix Model Definition for MySQL Compatibility
        # In app.py, ExchangeRate.source used 'max_length' (Django style)
        # instead of 'db.String(20)'. MySQL requires a explicit type.
        from sqlalchemy import String
        ExchangeRate.__table__.columns['source'].type = String(20)

        # 3. Initialize MySQL Schema
        print("[*] Dropping and Recreating MySQL Tables (InnoDB)...")
        db.metadata.drop_all(mysql_engine)
        db.metadata.create_all(mysql_engine)
        print("[+] Schema created successfully.")

        # 4. Data Transfer Order (To satisfy Foreign Key constraints)
        models = [User, ExchangeRate, Transaction, DepositRequest, Order]

        for model in models:
            table_name = model.__tablename__
            print(f"[*] Migrating table: {table_name}...")

            # Fetch all from SQLite
            with sqlite_engine.connect() as conn:
                result = conn.execute(text(f"SELECT * FROM {table_name}"))
                rows = [dict(row._mapping) for row in result]

            if not rows:
                print(f"    - Table {table_name} is empty. Skipping.")
                continue

            # Insert into MySQL
            # We use the underlying table object to preserve IDs and avoid SQLAlchemy logic triggers
            try:
                mysql_engine.execute(model.__table__.insert(), rows)
                print(f"    - Successfully migrated {len(rows)} rows.")
            except Exception as e:
                print(f"    [!] Error in {table_name}: {e}")
                mysql_session.rollback()
                return

        mysql_session.commit()
        print("[###] Migration Completed Successfully.")

if __name__ == "__main__":
    migrate()

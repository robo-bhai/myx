import os
import logging
import sqlalchemy
from sqlalchemy import create_engine, text
from app import app, db, User, Transaction, DepositRequest, Order, ExchangeRate

# --- CONFIGURATION ---
MYSQL_HOST = "Panelpromax.mysql.pythonanywhere-services.com"
MYSQL_USER = "Panelpromax"
MYSQL_PASS = "YOUR_MYSQL_PASSWORD" # Use your actual password
MYSQL_DB = "Panelpromax$default"

SQLITE_URI = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'site.db')
MYSQL_URI = f"mysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}/{MYSQL_DB}?charset=utf8mb4"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Migration")

def migrate():
    sqlite_engine = create_engine(SQLITE_URI)
    mysql_engine = create_engine(MYSQL_URI)

    print("[*] Starting Migration: SQLite -> MySQL")

    with app.app_context():
        # Fix the max_length bug dynamically before table creation
        ExchangeRate.__table__.columns['source'].type = sqlalchemy.String(20)

        print("[*] Dropping and Recreating MySQL Tables (InnoDB)...")
        db.metadata.drop_all(mysql_engine)
        db.metadata.create_all(mysql_engine)
        print("[+] Schema created successfully.")

        # Order matters for foreign keys
        models = [User, ExchangeRate, Transaction, DepositRequest, Order]

        with mysql_engine.begin() as mysql_conn:
            for model in models:
                table_name = model.__tablename__
                print(f"[*] Migrating table: {table_name}...")

                # Fetch data from SQLite
                with sqlite_engine.connect() as sqlite_conn:
                    result = sqlite_conn.execute(text(f"SELECT * FROM {table_name}"))
                    rows = [dict(row._mapping) for row in result]

                if not rows:
                    print(f"    - Table {table_name} is empty. Skipping.")
                    continue

                # Insert into MySQL using the connection object (SQLAlchemy 2.0 fix)
                mysql_conn.execute(model.__table__.insert(), rows)
                print(f"    - Successfully migrated {len(rows)} rows.")

        print("[###] Migration Completed Successfully.")

if __name__ == "__main__":
    migrate()

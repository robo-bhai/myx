import os
import logging
from sqlalchemy import create_engine, inspect, text
from dotenv import load_dotenv

# Environment Variables Load Karein (.env file se)
load_dotenv()

# Logging Config Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_db_engine():
    """Environment variables se Database URI aur SSL settings connect karta hai."""
    # Secrets mappings (Support both MYSQL_* and DB_* environment variables)
    MYSQL_USER = os.environ.get("MYSQL_USER") or os.environ.get("DB_USER")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD")
    MYSQL_HOST = os.environ.get("MYSQL_HOST") or os.environ.get("DB_HOST")
    MYSQL_PORT = os.environ.get("MYSQL_PORT") or os.environ.get("DB_PORT", "13461")
    MYSQL_DB = os.environ.get("MYSQL_DB") or os.environ.get("DB_NAME")

    if not all([MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, MYSQL_DB]):
        logger.error(
            "❌ Database Environment Variables (MYSQL_USER/DB_USER, MYSQL_PASSWORD, "
            "MYSQL_HOST/DB_HOST, MYSQL_DB/DB_NAME) set nahi hain!"
        )
        sys.exit(1)

    db_uri = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

    connect_args = {}
    # Agar local host (127.0.0.1) nahi hai, toh Aiven/Cloud DB ke liye SSL require hoga
    if MYSQL_HOST != "127.0.0.1":
        connect_args["ssl"] = {"ssl_mode": "REQUIRED"}
        logger.info("🔒 Remote Cloud Connection Detected: SSL Mode REQUIRED")
    else:
        logger.info("🏠 Local Database Connection Detected (127.0.0.1)")

    engine = create_engine(
        db_uri,
        connect_args=connect_args,
        pool_recycle=280,
        pool_pre_ping=True
    )
    return engine


def add_column_if_not_exists(engine, table_name, column_name, column_type):
    """
    Check karta hai ke kya column already database mein majood hai.
    Agar nahi hai toh ADD COLUMN alter query chalata hai.
    """
    inspector = inspect(engine)
    
    if table_name not in inspector.get_table_names():
        logger.warning(f"⚠️ Table '{table_name}' database mein majood nahi hai! Skip ho raha hai.")
        return

    existing_columns = [col['name'] for col in inspector.get_columns(table_name)]

    if column_name in existing_columns:
        logger.info(f"✅ Table '{table_name}' -> Column '{column_name}' already exists.")
    else:
        logger.info(f"🛠️ Table '{table_name}' -> Column '{column_name}' missing hai. Add kar rahe hain...")
        alter_query = f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_type};"
        
        with engine.begin() as connection:
            connection.execute(text(alter_query))
        logger.info(f"✨ Successfully Added: Column '{column_name}' in '{table_name}'.")


def run_migrations():
    engine = get_db_engine()

    try:
        logger.info("🔄 Database Schema Migration Start kar rahe hain...")

        # ============================================================
        #                    REQUIRED COLUMNS SETUP
        # ============================================================
        
        # 1. User Table Updates
        add_column_if_not_exists(engine, "user", "uid", "INT UNIQUE NULL")
        add_column_if_not_exists(engine, "user", "referral_code", "VARCHAR(10) UNIQUE NULL")
        add_column_if_not_exists(engine, "user", "device_fingerprint", "VARCHAR(255) NULL")
        add_column_if_not_exists(engine, "user", "otp_code", "VARCHAR(6) NULL")
        add_column_if_not_exists(engine, "user", "verification_token", "VARCHAR(100) NULL")
        add_column_if_not_exists(engine, "user", "otp_expiry", "DATETIME NULL")
        add_column_if_not_exists(engine, "user", "sub_plan", "VARCHAR(20) NOT NULL DEFAULT 'none'")
        add_column_if_not_exists(engine, "user", "sub_expiry", "DATETIME NULL")
        add_column_if_not_exists(engine, "user", "is_sub_active", "TINYINT(1) NOT NULL DEFAULT 0")
        add_column_if_not_exists(engine, "user", "last_checkin", "DATETIME NULL")
        add_column_if_not_exists(engine, "user", "streak_count", "INT NOT NULL DEFAULT 0")

        # 2. FreeTrialLink Table Updates
        add_column_if_not_exists(engine, "freetrial_link", "device_fingerprint", "VARCHAR(255) NULL")

        # 3. Order Table Updates
        add_column_if_not_exists(engine, "order", "api_order_id", "VARCHAR(100) NULL")
        add_column_if_not_exists(engine, "order", "api_response", "TEXT NULL")

        logger.info("🎉 Database Migration Process Completed Successfully!")

    except Exception as e:
        logger.error(f"❌ Migration Process Fail ho gaya: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    run_migrations()


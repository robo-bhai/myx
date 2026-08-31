import os
import logging
from sqlalchemy import text
# We import the app and models directly to ensure the schema matches your code 100%
from app import app, db, ExchangeRate

# 1. Configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MySQLInit")

def init_mysql_db():
    """
    Connects to the MySQL database defined in .env,
    creates all tables, and seeds initial data.
    """
    
    # 2. Fetch Database URL
    db_url = os.environ.get('DATABASE_URL')
    
    if not db_url:
        logger.error("❌ DATABASE_URL is missing from environment variables.")
        print("Tip: Ensure your .env file has: DATABASE_URL=mysql+pymysql://user:pass@host/dbname")
        return
    
    if 'mysql' not in db_url:
        logger.warning(f"⚠️  Warning: The URL '{db_url}' does not look like a MySQL connection string.")

    # 3. Force Configuration Override
    # This ensures we use the MySQL URL even if app.py still has 'sqlite:///' hardcoded.
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    logger.info(f"Connecting to MySQL database...")

    # 4. Context Manager
    with app.app_context():
        try:
            # Test Connection
            db.session.execute(text('SELECT 1'))
            logger.info("✅ Database connection successful.")

            # Create Tables
            logger.info("Creating tables...")
            db.create_all()
            logger.info("✅ Tables created successfully.")

            # Seed Data
            seed_exchange_rates()

        except Exception as e:
            logger.error(f"❌ Database initialization failed: {str(e)}")
            logger.error("Tip: Make sure the database exists. This script creates TABLES, not the DB itself.")

def seed_exchange_rates():
    """
    Pre-populates the ExchangeRate table if it's empty.
    """
    try:
        # Check if table has data
        if not ExchangeRate.query.first():
            logger.info("Seeding initial exchange rates...")
            
            initial_rates = [
                {'target': 'PKR', 'rate': 300.0},
                {'target': 'INR', 'rate': 83.0},
                {'target': 'USD', 'rate': 1.0}
            ]

            for r in initial_rates:
                exists = ExchangeRate.query.filter_by(target_currency=r['target']).first()
                if not exists:
                    new_rate = ExchangeRate(
                        target_currency=r['target'],
                        rate=r['rate'],
                        source='manual'
                    )
                    db.session.add(new_rate)
            
            db.session.commit()
            logger.info("✅ Initial exchange rates seeded.")
        else:
            logger.info("ℹ️  Exchange rates already exist. Skipping seed.")
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Failed to seed exchange rates: {str(e)}")

if __name__ == "__main__":
    print("--- Starting MySQL Initialization ---")
    init_mysql_db()
    print("--- Process Complete ---")

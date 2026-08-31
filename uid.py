import random
from app import app, db, User
from sqlalchemy import text

def generate_unique_uid():
    """Generates a 6-digit ID and verifies it isn't already in the database."""
    while True:
        new_uid = random.randint(100000, 999999)
        if not User.query.filter_by(uid=new_uid).first():
            return new_uid

def run_migration():
    with app.app_context():
        print("Starting database migration...")
        
        # 1. Add the 'uid' column to the User table if it doesn't exist
        try:
            # We use a raw SQL execution for the ALTER TABLE command
            db.session.execute(text("ALTER TABLE user ADD COLUMN uid INTEGER"))
            db.session.commit()
            print("Step 1: Successfully added 'uid' column to User table.")
        except Exception as e:
            db.session.rollback()
            # If the column already exists, SQLite will throw an error; we can ignore this.
            print(f"Step 1 Note: Column 'uid' may already exist. Continuing...")

        # 2. Backfill existing users who have a NULL uid
        users_to_update = User.query.filter(User.uid == None).all()
        
        if not users_to_update:
            print("Step 2: No existing users found needing a UID.")
        else:
            print(f"Step 2: Assigning 6-digit UIDs to {len(users_to_update)} users...")
            for user in users_to_update:
                user.uid = generate_unique_uid()
                print(f" - User '{user.username}': Assigned UID {user.uid}")
            
            try:
                db.session.commit()
                print("Step 2: Success! All existing users updated.")
            except Exception as e:
                db.session.rollback()
                print(f"Step 2 Error: Could not save UIDs to database: {e}")

        # 3. Create a unique index for the new column
        try:
            db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_uid ON user(uid)"))
            db.session.commit()
            print("Step 3: Unique index created for 'uid' column.")
        except Exception as e:
            db.session.rollback()
            print(f"Step 3 Note: Could not create unique index: {e}")

        print("\nMigration Complete.")

if __name__ == "__main__":
    run_migration()

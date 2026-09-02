from app import app, db, User

with app.app_context():
    user = User.query.filter_by(username='bhattixx_vcpk').first()
    if user:
        user.is_admin = True
        db.session.commit()
        print("Success: bhattixx_vcpk is now an Admin!")
    else:
        print("Error: User 'bhattixx_vcpk' DB me nahi mila.")

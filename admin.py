# make_admin.py
from app import app, db, User

with app.app_context():
    # xman کو تلاش کریں
    user = User.query.filter_by(username="xnxx").first()
    
    if user:
        # ایڈمن بنا دیں
        user.is_admin = True
        db.session.commit()
        print(f"✅ {user.username} اب ایڈمن ہے!")
        print(f"📧 Email: {user.email}")
        print(f"💰 Balance: {user.balance}")
        print(f"🆔 User ID: {user.id}")
        print(f"🔑 Admin Status: {user.is_admin}")
    else:
        print("❌ 'xman' نام کا کوئی یوزر نہیں ملا!")
        print("💡 پہلے register کریں یا چیک کریں کہ username صحیح ہے")
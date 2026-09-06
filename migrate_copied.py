from app import db, app
with app.app_context():
    try:
        db.session.execute(db.text('ALTER TABLE "order" ADD COLUMN is_copied BOOLEAN DEFAULT 0'))
        db.session.commit()
        print('Migration successful')
    except Exception as e:
        print(e)

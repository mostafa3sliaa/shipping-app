from app import db, app
with app.app_context():
    try:
        db.session.execute(db.text('ALTER TABLE "order" ADD COLUMN courier_settled BOOLEAN DEFAULT 0'))
        db.session.execute(db.text('ALTER TABLE "order" ADD COLUMN company_settled BOOLEAN DEFAULT 0'))
        db.session.commit()
        print('Success')
    except Exception as e:
        print(e)

import os
os.environ['DATABASE_URL'] = 'postgresql://postgres.ireprwsicunuqlbxymbn:SuperSafeShipping2026!%23@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'

from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker
from app import db, app

with app.app_context():
    db.create_all()

# Local SQLite
sqlite_engine = create_engine('sqlite:///instance/shipping.db')
sqlite_meta = MetaData()
sqlite_meta.reflect(bind=sqlite_engine)
SqliteSession = sessionmaker(bind=sqlite_engine)
sqlite_session = SqliteSession()

# Remote Postgres (Supabase)
pg_uri = os.environ['DATABASE_URL']
pg_engine = create_engine(pg_uri)
with app.app_context():
    app.config['SQLALCHEMY_DATABASE_URI'] = pg_uri
    db.create_all()

# Copy data
tables = ['company', 'courier', 'order', 'treasury_transaction']

pg_meta = MetaData()
pg_meta.reflect(bind=pg_engine)
PgSession = sessionmaker(bind=pg_engine)
pg_session = PgSession()

for table_name in tables:
    sqlite_table = sqlite_meta.tables[table_name]
    pg_table = pg_meta.tables[table_name]
    
    pg_session.execute(pg_table.delete())
    
    rows = sqlite_session.execute(sqlite_table.select()).fetchall()
    print(f"Migrating {len(rows)} rows for table {table_name}")
    
    if rows:
        data = [dict(row._mapping) for row in rows]
        pg_session.execute(pg_table.insert(), data)
        
pg_session.commit()
print("Migration complete!")

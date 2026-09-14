import os
os.environ['DATABASE_URL'] = 'postgresql://postgres.ireprwsicunuqlbxymbn:SuperSafeShipping2026!%23@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'

from app import app, db
from sqlalchemy import text

with app.app_context():
    indexes = [
        'CREATE INDEX IF NOT EXISTS idx_order_id_desc ON "order" (id DESC);',
        'CREATE INDEX IF NOT EXISTS idx_order_status ON "order" (status);',
        'CREATE INDEX IF NOT EXISTS idx_order_company_id ON "order" (company_id);',
        'CREATE INDEX IF NOT EXISTS idx_order_courier_id ON "order" (courier_id);',
        'CREATE INDEX IF NOT EXISTS idx_order_phone ON "order" (phone);',
        'CREATE INDEX IF NOT EXISTS idx_order_tracking ON "order" (tracking_number);',
        'CREATE INDEX IF NOT EXISTS idx_order_batch_id ON "order" (batch_id);',
        'CREATE INDEX IF NOT EXISTS idx_order_created_at ON "order" (created_at DESC);',
        'CREATE INDEX IF NOT EXISTS idx_order_region ON "order" (region);',
        'CREATE INDEX IF NOT EXISTS idx_order_courier_settled ON "order" (courier_settled);',
        'CREATE INDEX IF NOT EXISTS idx_order_company_settled ON "order" (company_settled);',
        'CREATE INDEX IF NOT EXISTS idx_treasury_method ON treasury_transaction (method);',
        'CREATE INDEX IF NOT EXISTS idx_treasury_tx_type ON treasury_transaction (tx_type);',
        'CREATE INDEX IF NOT EXISTS idx_treasury_entity_id ON treasury_transaction (entity_id);'
    ]
    
    for idx in indexes:
        try:
            db.session.execute(text(idx))
            db.session.commit()
            print(f'Successfully created index: {idx.strip()}')
        except Exception as e:
            db.session.rollback()
            print(f'Error creating index: {e}')

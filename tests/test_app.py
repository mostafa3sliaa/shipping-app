import pytest
import os
from app import app, db, Company, Courier, Order, TreasuryTransaction

@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            from werkzeug.security import generate_password_hash
            from app import User
            if not User.query.filter_by(username='admin').first():
                db.session.add(User(username='admin', password_hash=generate_password_hash('admin123')))
                db.session.commit()
            
        client.post('/login', data={'username': 'admin', 'password': 'admin123'})
        yield client
        
        with app.app_context():
            db.drop_all()

def test_homepage(client):
    rv = client.get('/')
    assert rv.status_code == 200

def test_create_order(client):
    with app.app_context():
        comp = Company(name="Test Co")
        db.session.add(comp)
        db.session.commit()
    
    response = client.post('/order/new', data={
        'tracking_number': 'TRK123',
        'client_name': 'Ali',
        'phone': '010123',
        'address': 'Cairo',
        'region': 'Downtown',
        'company_id': '1',
        'cod': '1000',
        'shipping_fee': '50'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    with app.app_context():
        order = Order.query.filter_by(phone='010123').first()
        assert order is not None
        assert order.cod == 1000

def test_company_settlement(client):
    with app.app_context():
        comp = Company(name="Test Co")
        db.session.add(comp)
        db.session.commit()
        
        o = Order(tracking_number='TRK1', company_id=1, cod=1000, shipping_fee=50, status='تم التوصيل', collected_amount=1000)
        db.session.add(o)
        db.session.commit()
        
    response = client.post('/accounting/company?company_id=1', data={
        'action': 'settle',
        'order_1': 'on'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    with app.app_context():
        o = db.session.get(Order, 1)
        assert o.company_settled == True

def test_treasury_payment(client):
    with app.app_context():
        comp = Company(name="Test Co")
        db.session.add(comp)
        db.session.commit()
        
    response = client.post('/api/company/1/pay', data={
        'amount': '500',
        'method': 'كاش',
        'notes': 'Test payment'
    }, follow_redirects=True)
    
    assert response.status_code == 200
    with app.app_context():
        tx = TreasuryTransaction.query.first()
        assert tx is not None
        assert tx.amount == -500

def test_bulk_set_shipping_fee(client):
    with app.app_context():
        o1 = Order(tracking_number='TRK-BULK-1', shipping_fee=50.0)
        o2 = Order(tracking_number='TRK-BULK-2', shipping_fee=60.0)
        db.session.add_all([o1, o2])
        db.session.commit()
        id1, id2 = o1.id, o2.id

    response = client.post('/orders/bulk_action', data={
        'action': 'set_shipping_fee',
        'order_ids': [str(id1), str(id2)],
        'bulk_shipping_fee': '75.5'
    }, follow_redirects=True)

    assert response.status_code == 200
    with app.app_context():
        res1 = db.session.get(Order, id1)
        res2 = db.session.get(Order, id2)
        assert res1.shipping_fee == 75.5
        assert res2.shipping_fee == 75.5

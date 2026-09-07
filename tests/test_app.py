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
        order = Order.query.filter_by(tracking_number='TRK123').first()
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
        o = Order.query.get(1)
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

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

def test_revert_delivered_to_warehouse(client):
    with app.app_context():
        courier = Courier(name="Courier 1")
        db.session.add(courier)
        db.session.commit()
        
        o = Order(
            tracking_number='TRK-DELIV',
            cod=370.0,
            shipping_fee=70.0,
            status='تم التوصيل',
            collected_amount=370.0,
            courier_fee=50.0,
            courier_id=courier.id,
            courier_settled=True
        )
        db.session.add(o)
        
        # Simulate previous courier settlement treasury transaction (+320)
        tx = TreasuryTransaction(
            amount=320.0,
            method='كاش',
            tx_type='تحصيل_من_مندوب',
            entity_id=courier.id
        )
        db.session.add(tx)
        db.session.commit()
        order_id = o.id

    # Now edit order to revert status to 'مخزن'
    response = client.post(f'/order/{order_id}/edit', data={
        'status': 'مخزن',
        'cod': '370',
        'shipping_fee': '70'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.status == 'مخزن'
        assert order.courier_settled == False
        assert order.courier_id == None
        assert order.collected_amount == None
        assert order.courier_fee == None
        
        # Check that treasury was debited by 320
        reversal = TreasuryTransaction.query.filter_by(tx_type='استرجاع_مخزن').first()
        assert reversal is not None
        assert reversal.amount == -320.0
        
        # Total treasury should now sum to 0
        total_treasury = db.session.query(db.func.sum(TreasuryTransaction.amount)).scalar()
        assert total_treasury == 0.0

def test_courier_settle_return_to_warehouse(client):
    with app.app_context():
        c = Courier(name="Courier Returns")
        db.session.add(c)
        db.session.commit()
        
        o = Order(
            tracking_number='TRK-RET-WH',
            cod=500.0,
            shipping_fee=70.0,
            status='مع المندوب',
            courier_id=c.id,
            courier_settled=False
        )
        db.session.add(o)
        db.session.commit()
        c_id = c.id
        o_id = o.id

    # Settle courier with 'مرتجع_مخزن' (return to warehouse without fees)
    response = client.post(f'/accounting/courier?courier_id={c_id}', data={
        'action': 'settle',
        f'status_{o_id}': 'مرتجع_مخزن',
        f'collected_{o_id}': '0',
        f'courier_fee_{o_id}': '0'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        order = db.session.get(Order, o_id)
        assert order.status == 'مخزن'
        assert order.courier_id == None
        assert order.courier_settled == True

def test_partial_delivery_flow_and_company_return(client):
    with app.app_context():
        c = Courier(name="Courier Partial")
        db.session.add(c)
        db.session.commit()
        
        o = Order(
            tracking_number='TRK-PARTIAL-1',
            cod=370.0,
            shipping_fee=70.0,
            status='مع المندوب',
            courier_id=c.id,
            courier_settled=False
        )
        db.session.add(o)
        db.session.commit()
        c_id = c.id
        o_id = o.id

    # 1. Settle partial delivery: COD=370, collected=270, courier fee=50
    response = client.post(f'/accounting/courier?courier_id={c_id}', data={
        'action': 'settle',
        f'status_{o_id}': 'تسليم جزئي / مرتجع',
        f'collected_{o_id}': '270',
        f'courier_fee_{o_id}': '50'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        order = db.session.get(Order, o_id)
        assert order.status == 'تسليم جزئي / مرتجع'
        assert order.collected_amount == 270.0
        assert order.courier_fee == 50.0
        
        # Net cash in treasury = 270 - 50 = 220
        tx = TreasuryTransaction.query.filter_by(tx_type='تحصيل_من_مندوب').first()
        assert tx is not None
        assert tx.amount == 220.0

    # 2. Check dashboard page renders and displays partial return goods (100)
    home = client.get('/')
    assert home.status_code == 200
    # In homepage: partial goods = 370 - 270 = 100.00
    assert b'100.00' in home.data
    # Company profit = 70 - 50 = 20.00
    assert b'20.00' in home.data

    # 3. Now the remaining return part (100) is returned to the company: status -> 'مرتجع شركة'
    response2 = client.post('/orders/bulk_action', data={
        'action': 'return_company',
        'order_ids': [str(o_id)]
    }, follow_redirects=True)
    assert response2.status_code == 200

    with app.app_context():
        order = db.session.get(Order, o_id)
        assert order.status == 'مرتجع شركة'

    # Check dashboard again: the 100 partial goods is removed, and 20 profit is still preserved!
    home2 = client.get('/')
    assert home2.status_code == 200
    assert b'20.00' in home2.data


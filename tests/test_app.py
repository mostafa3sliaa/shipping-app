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

def test_bulk_action_simultaneous_fields(client):
    with app.app_context():
        o1 = Order(tracking_number='TRK-MULTI-1', region='قديم 1', shipping_fee=50.0, status='مخزن')
        o2 = Order(tracking_number='TRK-MULTI-2', region='قديم 2', shipping_fee=50.0, status='مخزن')
        db.session.add_all([o1, o2])
        db.session.commit()
        id1, id2 = o1.id, o2.id

    # Post with courier_name, bulk_region_name, and bulk_shipping_fee at the same time
    response = client.post('/orders/bulk_action', data={
        'action': 'set_region',
        'order_ids': [str(id1), str(id2)],
        'courier_name': 'مندوب النيل',
        'bulk_region_name': 'فيصل',
        'bulk_shipping_fee': '65.0'
    }, follow_redirects=True)

    assert response.status_code == 200
    with app.app_context():
        res1 = db.session.get(Order, id1)
        res2 = db.session.get(Order, id2)
        # All three must be updated together!
        assert res1.region == 'فيصل'
        assert res2.region == 'فيصل'
        assert res1.shipping_fee == 65.0
        assert res2.shipping_fee == 65.0
        assert res1.status == 'مع المندوب'
        assert res2.status == 'مع المندوب'
        assert res1.courier.name == 'مندوب النيل'
        assert res2.courier.name == 'مندوب النيل'


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

def test_treasury_expense(client):
    with app.app_context():
        # Add initial cash of 1000
        db.session.add(TreasuryTransaction(amount=1000.0, method='كاش', tx_type='إيداع_يدوي'))
        db.session.commit()

    # Record expense of 150
    response = client.post('/treasury/expense', data={
        'amount': '150',
        'notes': 'فاتورة كهرباء ومطبوعات'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        tx = TreasuryTransaction.query.filter_by(tx_type='مصروفات').first()
        assert tx is not None
        assert tx.amount == -150.0
        assert tx.method == 'كاش'
        
        # Net cash should be 850
        net_cash = db.session.query(db.func.sum(TreasuryTransaction.amount)).filter_by(method='كاش').scalar()
        assert net_cash == 850.0

def test_wallet_withdraw(client):
    with app.app_context():
        # Add initial transfer wallet balance of 500
        db.session.add(TreasuryTransaction(amount=500.0, method='تحويل', tx_type='إيداع_يدوي'))
        db.session.commit()

    # Withdraw 200 from wallet
    response = client.post('/treasury/withdraw_wallet', data={
        'amount': '200',
        'notes': 'سحب فودافون كاش'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        tx = TreasuryTransaction.query.filter_by(tx_type='سحب_محفظة').first()
        assert tx is not None
        assert tx.amount == -200.0
        assert tx.method == 'تحويل'
        
        # Net wallet should be 300
        net_transfer = db.session.query(db.func.sum(TreasuryTransaction.amount)).filter_by(method='تحويل').scalar()
        assert net_transfer == 300.0

def test_reset_profit_without_deducting_treasury(client):
    with app.app_context():
        # Add an order with profit 100
        o = Order(
            tracking_number='TRK-PROFIT-TEST',
            cod=1000.0,
            shipping_fee=150.0,
            courier_fee=50.0,
            collected_amount=1000.0,
            status='تم التوصيل',
            courier_settled=True
        )
        # Treasury cash received 950
        tx = TreasuryTransaction(amount=950.0, method='كاش', tx_type='تحصيل_من_مندوب')
        db.session.add_all([o, tx])
        db.session.commit()

    # Check dashboard: profit is 100 (150 - 50) and cash is 950
    h1 = client.get('/')
    assert h1.status_code == 200
    assert b'100.00' in h1.data
    assert b'950.00' in h1.data

    # Now reset profit
    res = client.post('/treasury/reset_profit', data={
        'notes': 'تصفير دورة أسبوعية'
    }, follow_redirects=True)
    assert res.status_code == 200

    # Check dashboard again: profit is 0.00, BUT cash is STILL 950.00!
    h2 = client.get('/')
    assert h2.status_code == 200
    assert b'0.00' in h2.data
    assert b'950.00' in h2.data

    with app.app_context():
        # Treasury cash must remain exactly 950.0
        cash = db.session.query(db.func.sum(TreasuryTransaction.amount)).filter_by(method='كاش').scalar()
        assert cash == 950.0
        # Reset record exists with method='أرباح'
        reset_tx = TreasuryTransaction.query.filter_by(tx_type='تصفير_أرباح').first()
        assert reset_tx is not None
        assert reset_tx.amount == 100.0
        assert reset_tx.method == 'أرباح'

def test_partial_delivery_display_and_reversal(client):
    with app.app_context():
        comp = Company(name="Co Partial")
        db.session.add(comp)
        db.session.commit()

        # Order with COD 370, shipping 70.
        # Delivered 270 (shipping 70 + goods 200), courier took 50.
        o = Order(
            tracking_number='TRK-PARTIAL-1',
            client_name='Customer 1',
            phone='0109999999',
            address='Cairo',
            company_id=comp.id,
            cod=370.0,
            shipping_fee=70.0,
            courier_fee=50.0,
            collected_amount=270.0,
            status='تسليم جزئي / مرتجع',
            courier_settled=True
        )
        # Net collected into treasury = 270 - 50 = 220
        tx = TreasuryTransaction(amount=220.0, method='كاش', tx_type='تحصيل_من_مندوب', notes='تحصيل جزئي')
        db.session.add_all([o, tx])
        db.session.commit()

    # 1. Test template display: Should show original COD 370.00 and Company Net 200.00 when filtered or searched
    res = client.get('/?tab=orders&status=تسليم جزئي / مرتجع')
    assert res.status_code == 200
    assert b'370.00' in res.data
    assert b'200.00' in res.data

    # 2. Test status change away from delivered/partial -> e.g. to 'مع المندوب'
    with app.app_context():
        order_id = Order.query.filter_by(tracking_number='TRK-PARTIAL-1').first().id

    edit_res = client.post(f'/order/{order_id}/edit', data={
        'tracking_number': 'TRK-PARTIAL-1',
        'client_name': 'Customer 1',
        'phone': '0109999999',
        'address': 'Cairo',
        'cod': '370',
        'shipping_fee': '70',
        'status': 'مع المندوب'
    }, follow_redirects=True)
    assert edit_res.status_code == 200

    # Verify treasury reversed the 220, so total cash in treasury becomes 0
    with app.app_context():
        cash = db.session.query(db.func.sum(TreasuryTransaction.amount)).filter_by(method='كاش').scalar()
        assert cash == 0.0

        updated_order = db.session.get(Order, order_id)
        assert updated_order.status == 'مع المندوب'
        assert updated_order.courier_settled == False
        assert updated_order.collected_amount is None

def test_api_scan_and_database_search(client):
    with app.app_context():
        comp = Company(name="Company Scan")
        db.session.add(comp)
        db.session.commit()

        o = Order(
            tracking_number='SHP-A7D8A6',
            client_name='Rabab Sobhy',
            phone='01111108660',
            address='10 Street Nasr City',
            content='2 Dress L',
            company_id=comp.id,
            cod=370.0,
            shipping_fee=70.0,
            status='مخزن'
        )
        db.session.add(o)
        db.session.commit()

    # 1. Test /api/scan with phone number returns full details
    resp = client.get('/api/scan?q=01111108660')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['orders']) == 1
    order_data = data['orders'][0]
    assert order_data['tracking_number'] == 'SHP-A7D8A6'
    assert order_data['client_name'] == 'Rabab Sobhy'
    assert order_data['address'] == '10 Street Nasr City'
    assert order_data['content'] == '2 Dress L'
    assert order_data['company_name'] == 'Company Scan'
    assert order_data['cod'] == 370.0
    assert order_data['shipping_fee'] == 70.0

    # 2. Test /api/scan with case-insensitive name
    resp_name = client.get('/api/scan?q=rabab')
    assert resp_name.status_code == 200
    assert len(resp_name.get_json()['orders']) == 1

    # 3. Test main database search with phone and case-insensitive tracking
    search_resp = client.get('/?tab=orders&search=01111108660')
    assert search_resp.status_code == 200
    assert b'SHP-A7D8A6' in search_resp.data
    assert b'Rabab Sobhy' in search_resp.data

    search_trk = client.get('/?tab=orders&search=shp-a7d8a6')
    assert search_trk.status_code == 200
    assert b'SHP-A7D8A6' in search_trk.data

def test_manual_order_with_courier_and_edit_courier(client):
    # 1. Create manual order with optional courier
    response = client.post('/order/new', data={
        'client_name': 'عمر',
        'phone': '01123456789',
        'address': 'Giza',
        'region': 'الدقي',
        'cod': '500',
        'shipping_fee': '50',
        'courier_name': 'مندوب يدوي'
    }, follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        order = Order.query.filter_by(phone='01123456789').first()
        assert order is not None
        assert order.status == 'مع المندوب'
        assert order.courier is not None
        assert order.courier.name == 'مندوب يدوي'

    # 2. Edit an order from 'مخزن' by adding a courier name
    with app.app_context():
        o_wh = Order(tracking_number='TRK-WH-EDIT', client_name='سعيد', phone='01198765432', cod=300, status='مخزن')
        db.session.add(o_wh)
        db.session.commit()
        wh_id = o_wh.id

    # Post edit with courier_name while status was 'مخزن' in form
    res_edit = client.post(f'/order/{wh_id}/edit', data={
        'client_name': 'سعيد',
        'phone': '01198765432',
        'cod': '300',
        'status': 'مخزن',
        'courier_name': 'مندوب تم إضافته'
    }, follow_redirects=True)
    assert res_edit.status_code == 200

    with app.app_context():
        updated_wh = db.session.get(Order, wh_id)
        assert updated_wh.courier is not None
        assert updated_wh.courier.name == 'مندوب تم إضافته'
        assert updated_wh.status == 'مع المندوب'

def test_clean_phone_smart_all_formats():
    from app import clean_phone_smart
    
    # 1. Arabic digits
    assert clean_phone_smart('٠١٠١٢٣٤٥٦٧٨') == '01012345678'
    
    # 2. Multi-number with slash and Arabic digits
    assert clean_phone_smart('٠١٠١٢٣٤٥٦٧٨ / ٠١١٩٨٧٦٥٤٣٢') == '01012345678 - 01198765432'
    assert clean_phone_smart('٠١٠١٢٣٤٥٦٧٨/٠١١٩٨٧٦٥٤٣٢') == '01012345678 - 01198765432'
    
    # 3. Word delimiters: أو / او / و
    assert clean_phone_smart('01012345678 او 01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('01012345678 أو 01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('01012345678 و 01198765432') == '01012345678 - 01198765432'
    
    # 4. Commas and symbols: ، , | ;
    assert clean_phone_smart('01012345678،01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('01012345678, 01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('01012345678 | 01198765432') == '01012345678 - 01198765432'
    
    # 5. Dashes between two numbers vs internal phone dash
    assert clean_phone_smart('01012345678 - 01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('01012345678-01198765432') == '01012345678 - 01198765432'
    assert clean_phone_smart('010-1234-5678') == '01012345678'
    assert clean_phone_smart('010 1234 5678') == '01012345678'
    
    # 6. Two numbers separated by spaces
    assert clean_phone_smart('01012345678 01198765432') == '01012345678 - 01198765432'
    
    # 7. Float representations and missing leading zero
    assert clean_phone_smart('1012345678.0') == '01012345678'
    assert clean_phone_smart(1012345678.0) == '01012345678'
    assert clean_phone_smart('1012345678') == '01012345678'
    
    # 8. Country codes (+20, 0020, 20)
    assert clean_phone_smart('+201012345678') == '01012345678'
    assert clean_phone_smart('00201012345678') == '01012345678'
    assert clean_phone_smart('201012345678') == '01012345678'
    
    # 9. None, empty, NaN
    assert clean_phone_smart(None) == ''
    assert clean_phone_smart('') == ''
    assert clean_phone_smart('nan') == ''

def test_order_creation_and_search_with_arabic_phone(client):
    # Create order with Arabic digits and slash
    response = client.post('/order/new', data={
        'client_name': 'أحمد إبراهيم',
        'phone': '٠١٠١١١٢٢٢٣٣ / ٠١٢٣٣٣٤٤٤٥٥',
        'address': 'مدينة نصر',
        'region': 'القاهرة',
        'cod': '500',
        'shipping_fee': '50'
    }, follow_redirects=True)
    assert response.status_code == 200
    
    with app.app_context():
        order = Order.query.filter_by(client_name='أحمد إبراهيم').first()
        assert order is not None
        assert order.phone == '01011122233 - 01233344455'
        order_id = order.id

    # Search in index with Arabic digits
    res_search = client.get('/?search=٠١٠١١١٢٢٢٣٣')
    assert res_search.status_code == 200
    assert '01011122233 - 01233344455' in res_search.get_data(as_text=True)

    # Search via api_scan with Arabic digits
    res_api = client.get('/api/scan?q=٠١٠١١١٢٢٢٣٣')
    assert res_api.status_code == 200
    data = res_api.get_json()
    assert len(data['orders']) == 1
    assert data['orders'][0]['phone'] == '01011122233 - 01233344455'

    # Edit phone using Arabic digits with word 'او'
    res_edit = client.post(f'/order/{order_id}/edit', data={
        'client_name': 'أحمد إبراهيم',
        'phone': '٠١٥٩٩٩٨٨٨٧٧ او ٠١٠١١١٢٢٢٣٣',
        'cod': '500',
        'status': 'مخزن'
    }, follow_redirects=True)
    assert res_edit.status_code == 200

    with app.app_context():
        updated = db.session.get(Order, order_id)
        assert updated.phone == '01599988877 - 01011122233'

def test_return_with_shipping_flow(client):
    with app.app_context():
        comp = Company(name="شركة الأمل")
        cour = Courier(name="مندوب التوصيل")
        db.session.add_all([comp, cour])
        db.session.commit()
        
        order = Order(
            tracking_number='TRK-RET-SHIP',
            client_name='محمود حسن',
            phone='01099887766',
            cod=400.0,
            shipping_fee=60.0,
            status='مع المندوب',
            company_id=comp.id,
            courier_id=cour.id
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id
        cour_id = cour.id
        comp_id = comp.id

    # 1. Settle courier with option 'مرتجع بشحن'
    # Collected = 60, courier fee = 40, net cash = 20
    res_settle = client.post(f'/accounting/courier?courier_id={cour_id}', data={
        'action': 'settle',
        f'status_{order_id}': 'مرتجع بشحن',
        f'collected_{order_id}': '60',
        f'courier_fee_{order_id}': '40',
        'transfers': '0'
    }, follow_redirects=True)
    assert res_settle.status_code == 200

    # Verify: order status is 'مرتجع' (not a separate status 'مرتجع بشحن'), courier_settled is True
    with app.app_context():
        o = db.session.get(Order, order_id)
        assert o.status == 'مرتجع'
        assert o.courier_settled is True
        assert o.collected_amount == 60.0
        assert o.courier_fee == 40.0

        # Treasury received net cash 20
        tx = TreasuryTransaction.query.filter_by(entity_id=cour_id, tx_type='تحصيل_من_مندوب').first()
        assert tx is not None
        assert tx.amount == 20.0

    # Verify dashboard metrics
    res_dash = client.get('/?tab=dashboard')
    assert res_dash.status_code == 200
    # Returned card should count it
    res_orders = client.get('/?tab=orders&status=مرتجع')
    assert res_orders.status_code == 200
    assert 'TRK-RET-SHIP' in res_orders.get_data(as_text=True)

    # 2. Convert to company return: 'مرتجع شركة'
    res_bulk = client.post('/orders/bulk_action', data={
        'action': 'return_company',
        'order_ids': [str(order_id)]
    }, follow_redirects=True)
    assert res_bulk.status_code == 200

    with app.app_context():
        o = db.session.get(Order, order_id)
        assert o.status == 'مرتجع شركة'
        # Treasury cash not reversed
        txs = TreasuryTransaction.query.filter_by(entity_id=cour_id).all()
        assert len(txs) == 1
        assert txs[0].amount == 20.0

    # 3. Check company accounting: order is ready under company returns
    res_comp = client.get(f'/accounting/company?company_id={comp_id}')
    assert res_comp.status_code == 200
    assert 'TRK-RET-SHIP' in res_comp.get_data(as_text=True)

def test_filters_all_none_specific(client):
    with app.app_context():
        comp = Company(name="شركة الفلاتر")
        cour = Courier(name="مندوب الفلاتر")
        db.session.add_all([comp, cour])
        db.session.commit()

        # Order 1: has courier, has company, has region, status 'مرتجع'
        o1 = Order(tracking_number='TRK-F1', client_name='عميل 1', phone='01011111111', cod=100, status='مرتجع', courier_id=cour.id, company_id=comp.id, region='المعادي')
        # Order 2: NO courier, has company, NO region, status 'مرتجع'
        o2 = Order(tracking_number='TRK-F2', client_name='عميل 2', phone='01022222222', cod=200, status='مرتجع', courier_id=None, company_id=comp.id, region=None)
        # Order 3: has courier, NO company, NO region, status 'مخزن'
        o3 = Order(tracking_number='TRK-F3', client_name='عميل 3', phone='01033333333', cod=300, status='مخزن', courier_id=cour.id, company_id=None, region='')
        
        db.session.add_all([o1, o2, o3])
        db.session.commit()

    # Test 1: courier_id='all' (only with courier)
    r1 = client.get('/?courier_id=all')
    text1 = r1.get_data(as_text=True)
    assert 'TRK-F1' in text1
    assert 'TRK-F3' in text1
    assert 'TRK-F2' not in text1

    # Test 2: courier_id='none' (only without courier)
    r2 = client.get('/?courier_id=none')
    text2 = r2.get_data(as_text=True)
    assert 'TRK-F2' in text2
    assert 'TRK-F1' not in text2
    assert 'TRK-F3' not in text2

    # Test 3: status='مرتجع' AND courier_id='all' (only returns that have couriers)
    r3 = client.get('/?status=مرتجع&courier_id=all')
    text3 = r3.get_data(as_text=True)
    assert 'TRK-F1' in text3
    assert 'TRK-F2' not in text3
    assert 'TRK-F3' not in text3

    # Test 4: status='مرتجع' AND courier_id='none' (only returns that have no courier)
    r4 = client.get('/?status=مرتجع&courier_id=none')
    text4 = r4.get_data(as_text=True)
    assert 'TRK-F2' in text4
    assert 'TRK-F1' not in text4
    assert 'TRK-F3' not in text4

    # Test 5: status='مرتجع' AND courier_id='' (all returns regardless of courier)
    r5 = client.get('/?status=مرتجع')
    text5 = r5.get_data(as_text=True)
    assert 'TRK-F1' in text5
    assert 'TRK-F2' in text5
    assert 'TRK-F3' not in text5

    # Test 6: company_id='all'
    r6 = client.get('/?company_id=all')
    text6 = r6.get_data(as_text=True)
    assert 'TRK-F1' in text6
    assert 'TRK-F2' in text6
    assert 'TRK-F3' not in text6

    # Test 7: region='all'
    r7 = client.get('/?region=all')
    text7 = r7.get_data(as_text=True)
    assert 'TRK-F1' in text7
    assert 'TRK-F2' not in text7
    assert 'TRK-F3' not in text7

def test_strictly_settled_company_profit(client):
    """
    Ensure that company_profit strictly counts orders with courier_settled == True.
    Unsettled orders marked 'تم التوصيل' must NOT inflate company_profit!
    """
    with app.app_context():
        courier = Courier(name="Ali Courier", phone="0100000000")
        company = Company(name="Test Brand")
        db.session.add_all([courier, company])
        db.session.commit()

        # 7 settled orders with shipping_fee=70, courier_fee=50 -> Net profit = 20 * 7 = 140
        for i in range(1, 8):
            o = Order(
                tracking_number=f'TRK-SETTLED-{i}',
                courier_id=courier.id,
                company_id=company.id,
                status='تم التوصيل',
                cod=300.0,
                shipping_fee=70.0,
                courier_fee=50.0,
                courier_settled=True,
                collected_amount=300.0
            )
            db.session.add(o)

        # 2 unsettled delivered orders with shipping_fee=185 each (total 370)
        # These must NOT be added to company_profit!
        o_unsettled1 = Order(
            tracking_number='TRK-UNSETTLED-1',
            company_id=company.id,
            status='تم التوصيل',
            cod=500.0,
            shipping_fee=185.0,
            courier_fee=0.0,
            courier_settled=False
        )
        o_unsettled2 = Order(
            tracking_number='TRK-UNSETTLED-2',
            company_id=company.id,
            status='تم التوصيل',
            cod=500.0,
            shipping_fee=185.0,
            courier_fee=0.0,
            courier_settled=False
        )
        db.session.add_all([o_unsettled1, o_unsettled2])
        db.session.commit()

    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The profit MUST be 140.00 ج.م and NOT 510.00 ج.م!
    assert '140.00 ج.م' in html
    assert '510.00' not in html

    # Verify modal exists and shows 7 settled shipments, and does NOT include unsettled orders
    assert 'تفاصيل الأرباح المُحصلة' in html
    assert 'id="profitBreakdownModal"' in html
    modal_part = html[html.find('id="profitBreakdownModal"'):]
    assert 'TRK-SETTLED-1' in modal_part
    assert 'TRK-UNSETTLED-1' not in modal_part

def test_order_settlement_edit_from_profit_modal(client):
    """
    Test editing an order's settlement via /order/<id>/settlement_edit:
    1. An order with return with shipping (70 collected, 0 fee -> profit = 70)
    2. Edited to 'مرتجع_بدون_شحن' (0 collected)
    3. Verify collected_amount becomes 0, profit drops from 70 to 0, and treasury is adjusted.
    """
    with app.app_context():
        comp = Company(name="Co Return")
        db.session.add(comp)
        db.session.commit()

        # Settled return with shipping 70
        order = Order(
            tracking_number='SHP-TEST-RETURN-70',
            company_id=comp.id,
            status='مرتجع',
            cod=370.0,
            shipping_fee=70.0,
            collected_amount=70.0,
            courier_fee=0.0,
            courier_settled=True
        )
        # Old treasury transaction
        tx = TreasuryTransaction(amount=70.0, method='كاش', tx_type='تحصيل_من_مندوب', notes='تحصيل')
        db.session.add_all([order, tx])
        db.session.commit()
        order_id = order.id

    # Check that initially company_profit is 70.00
    r1 = client.get('/')
    assert '70.00 ج.م' in r1.get_data(as_text=True)

    # Now edit settlement to 'مرتجع_بدون_شحن' with collected_amount=0
    post_res = client.post(f'/order/{order_id}/settlement_edit', data={
        'status_choice': 'مرتجع_بدون_شحن',
        'collected_amount': '0',
        'courier_fee': '0',
        'shipping_fee': '70'
    }, follow_redirects=True)
    assert post_res.status_code == 200

    # Verify order and treasury in DB
    with app.app_context():
        o = db.session.get(Order, order_id)
        assert o.status == 'مرتجع'
        assert o.collected_amount == 0.0
        assert o.courier_settled == True

        # Check treasury transactions: should have adjustment of -70
        adj_tx = TreasuryTransaction.query.filter_by(tx_type='تعديل_تحصيل').first()
        assert adj_tx is not None
        assert adj_tx.amount == -70.0

    # Check homepage: profit should now be 0.00 ج.م!
    r2 = client.get('/')
    assert '0.00 ج.م' in r2.get_data(as_text=True)

def test_filter_all_returns_and_net_display(client):
    """
    Test filtering by status='all_returns' and verifying 'بدون الشحن' display:
    - Order 1: 'مرتجع', cod=370, ship=70 -> net=300
    - Order 2: 'تسليم جزئي / مرتجع', cod=500, ship=70, collected=270 -> net=200
    - Order 3: 'مخزن', cod=1000, ship=70 -> net=930
    - Order 4: 'تم التوصيل', cod=400, ship=70 -> net=330
    """
    with app.app_context():
        comp = Company(name="Test Ret Co")
        db.session.add(comp)
        db.session.commit()

        o1 = Order(tracking_number='RET-FULL-1', company_id=comp.id, status='مرتجع', cod=370.0, shipping_fee=70.0)
        o2 = Order(tracking_number='RET-PARTIAL-1', company_id=comp.id, status='تسليم جزئي / مرتجع', cod=500.0, shipping_fee=70.0, collected_amount=270.0)
        o3 = Order(tracking_number='WH-ORD-1', company_id=comp.id, status='مخزن', cod=1000.0, shipping_fee=70.0)
        o4 = Order(tracking_number='DELIV-ORD-1', company_id=comp.id, status='تم التوصيل', cod=400.0, shipping_fee=70.0)
        o5 = Order(tracking_number='RET-COMPANY-1', company_id=comp.id, status='مرتجع شركة', cod=600.0, shipping_fee=50.0)
        db.session.add_all([o1, o2, o3, o4, o5])
        db.session.commit()

    # 1. Filter by all_returns
    resp = client.get('/?tab=orders&status=all_returns')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Should contain o1 and o2
    assert 'RET-FULL-1' in html
    assert 'RET-PARTIAL-1' in html
    # Should NOT contain o3, o4, or o5 (مرتجع شركة)
    assert 'WH-ORD-1' not in html
    assert 'DELIV-ORD-1' not in html
    assert 'RET-COMPANY-1' not in html

    # Total orders count for all_returns: 2
    # Net without shipping: o1 (300) + o2 (200) = 500.00 ج.م
    assert 'بدون الشحن:' in html
    assert '500.00 ج.م' in html

    # 2. Filter by warehouse (status=مخزن)
    resp_wh = client.get('/?tab=orders&status=مخزن')
    assert resp_wh.status_code == 200
    html_wh = resp_wh.get_data(as_text=True)
    assert 'WH-ORD-1' in html_wh
    assert 'RET-FULL-1' not in html_wh
    # Net without shipping for o3: 1000 - 70 = 930.00 ج.م
    assert '930.00 ج.م' in html_wh

def test_export_excel_features(client):
    with app.app_context():
        comp = Company(name='شركة التصدير')
        db.session.add(comp)
        db.session.flush()

        o1 = Order(tracking_number='EXP-001', company_id=comp.id, status='مخزن', cod=500.0, shipping_fee=50.0, client_name='عميل 1', phone='01011111111')
        o2 = Order(tracking_number='EXP-002', company_id=comp.id, status='مرتجع', cod=300.0, shipping_fee=50.0, client_name='عميل 2', phone='01022222222')
        o3 = Order(tracking_number='EXP-003', company_id=comp.id, status='تم التوصيل', cod=800.0, shipping_fee=60.0, client_name='عميل 3', phone='01033333333')
        db.session.add_all([o1, o2, o3])
        db.session.commit()
        id1, id2, id3 = o1.id, o2.id, o3.id
        comp_id = comp.id

    # 1. GET export with filter (status=مخزن)
    resp_get = client.get(f'/export_excel?company_id={comp_id}&status=مخزن')
    assert resp_get.status_code == 200
    assert 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in resp_get.content_type
    assert len(resp_get.data) > 1000

    # 2. POST export with order_ids (selected orders)
    resp_post = client.post('/export_excel', data={'order_ids': [str(id1), str(id3)]})
    assert resp_post.status_code == 200
    assert 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in resp_post.content_type
    assert len(resp_post.data) > 1000

    # 3. Bulk action export_excel
    resp_bulk = client.post('/orders/bulk_action', data={'action': 'export_excel', 'order_ids': [str(id2)]})
    assert resp_bulk.status_code == 200
    assert 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in resp_bulk.content_type
    assert len(resp_bulk.data) > 1000

def test_returned_company_hidden_by_default_in_orders_log(client):
    with app.app_context():
        comp = Company(name='شركة المرتجعات والمسلمات المستبعدة')
        db.session.add(comp)
        db.session.flush()

        active_ord = Order(tracking_number='ACT-WH-001', company_id=comp.id, status='مخزن', cod=400.0, shipping_fee=50.0)
        returned_co_ord = Order(tracking_number='RET-CO-999', company_id=comp.id, status='مرتجع شركة', cod=700.0, shipping_fee=60.0)
        delivered_ord = Order(tracking_number='DEL-999', company_id=comp.id, status='تم التوصيل', cod=550.0, shipping_fee=50.0)
        partial_ord = Order(tracking_number='PART-999', company_id=comp.id, status='تسليم جزئي / مرتجع', cod=600.0, shipping_fee=50.0, collected_amount=300.0)
        db.session.add_all([active_ord, returned_co_ord, delivered_ord, partial_ord])
        db.session.commit()
        comp_id = comp.id

    # 1. Default orders log for this company (filter_status is empty): 'مرتجع شركة', 'تم التوصيل', and 'تسليم جزئي / مرتجع' must be hidden!
    res_default = client.get(f'/?tab=orders&company_id={comp_id}')
    assert res_default.status_code == 200
    html_default = res_default.get_data(as_text=True)
    assert 'ACT-WH-001' in html_default
    assert 'RET-CO-999' not in html_default
    assert 'DEL-999' not in html_default
    assert 'PART-999' not in html_default

    # 2. When explicitly filtering by status='مرتجع شركة': 'مرتجع شركة' must appear!
    res_ret_co = client.get(f'/?tab=orders&company_id={comp_id}&status=مرتجع شركة')
    assert res_ret_co.status_code == 200
    html_ret_co = res_ret_co.get_data(as_text=True)
    assert 'RET-CO-999' in html_ret_co
    assert 'ACT-WH-001' not in html_ret_co

    # 3. When explicitly filtering by status='تم التوصيل': 'تم التوصيل' must appear!
    res_del = client.get(f'/?tab=orders&company_id={comp_id}&status=تم التوصيل')
    assert res_del.status_code == 200
    html_del = res_del.get_data(as_text=True)
    assert 'DEL-999' in html_del
    assert 'ACT-WH-001' not in html_del

    # 4. When explicitly filtering by status='تسليم جزئي / مرتجع': 'تسليم جزئي / مرتجع' must appear!
    res_part = client.get(f'/?tab=orders&company_id={comp_id}&status=تسليم جزئي / مرتجع')
    assert res_part.status_code == 200
    html_part = res_part.get_data(as_text=True)
    assert 'PART-999' in html_part
    assert 'ACT-WH-001' not in html_part

    # 5. When filtering by 'all_inclusive': all orders appear!
    res_all = client.get(f'/?tab=orders&company_id={comp_id}&status=all_inclusive')
    assert res_all.status_code == 200
    html_all = res_all.get_data(as_text=True)
    assert 'ACT-WH-001' in html_all
    assert 'RET-CO-999' in html_all
    assert 'DEL-999' in html_all
    assert 'PART-999' in html_all

def test_empty_regions_couriers_omitted_from_filter(client):
    with app.app_context():
        comp_active = Company(name='شركة الفلتر النشطة')
        comp_empty = Company(name='شركة الفلتر الفارغة')
        courier_active = Courier(name='مندوب نشط بالشغل')
        courier_empty = Courier(name='مندوب فاضي تماما')
        db.session.add_all([comp_active, comp_empty, courier_active, courier_empty])
        db.session.commit()

        # Active order for courier_active in 'منطقة المعادي النشطة'
        o1 = Order(
            tracking_number='FLT-ACT-01',
            company_id=comp_active.id,
            courier_id=courier_active.id,
            region='المعادي النشطة',
            status='مع المندوب',
            cod=500.0,
            shipping_fee=50.0
        )
        # Completed delivered order in 'منطقة شبرا المنتهية' (no active work here)
        o2 = Order(
            tracking_number='FLT-DEL-02',
            company_id=comp_active.id,
            courier_id=courier_active.id,
            region='شبرا المنتهية',
            status='تم التوصيل',
            cod=300.0,
            shipping_fee=40.0
        )
        db.session.add_all([o1, o2])
        db.session.commit()

    # 1. In default orders view:
    res = client.get('/?tab=orders')
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Verify filter dropdown selects specifically
    company_select = html.split('<select name="company_id"')[1].split('</select>')[0]
    courier_select = html.split('<select name="courier_id"')[1].split('</select>')[0]
    region_select = html.split('<select name="region"')[1].split('</select>')[0]

    # Active courier & company & region must be in filter dropdown:
    assert 'مندوب نشط بالشغل' in courier_select
    assert 'شركة الفلتر النشطة' in company_select
    assert 'المعادي النشطة' in region_select

    # Empty courier & company & region without active work MUST NOT be in the filter dropdown!
    assert 'مندوب فاضي تماما' not in courier_select
    assert 'شركة الفلتر الفارغة' not in company_select
    assert 'شبرا المنتهية' not in region_select

    # 2. When filtering by 'تم التوصيل', 'شبرا المنتهية' should appear in region select!
    res_del = client.get('/?tab=orders&status=تم التوصيل')
    assert res_del.status_code == 200
    html_del = res_del.get_data(as_text=True)
    region_select_del = html_del.split('<select name="region"')[1].split('</select>')[0]
    assert 'شبرا المنتهية' in region_select_del

def test_orders_summary_card_with_shipping_and_return_reset(client):
    with app.app_context():
        comp = Company(name="شركة الملخص")
        db.session.add(comp)
        db.session.commit()
        
        order = Order(
            tracking_number="TRK-SUMM-1",
            client_name="عميل الملخص",
            phone="01011112222",
            cod=370.0,
            shipping_fee=70.0,
            status="مخزن",
            company_id=comp.id
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.id

    # 1. Verify 'بدون الشحن' and 'بالشحن' both appear in orders summary card
    res = client.get('/?tab=orders')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'بدون الشحن:' in html
    assert 'بالشحن:' in html
    assert '300.00' in html  # 370 - 70 = 300 net
    assert '370.00' in html  # 370 total COD with shipping

    # 2. Simulate order being delivered with 370 collected
    with app.app_context():
        o = db.session.get(Order, order_id)
        o.status = 'تم التوصيل'
        o.collected_amount = 370.0
        db.session.commit()

    # 3. Edit order to 'مرتجع' without resetting collected_amount
    res_edit = client.post(f'/order/{order_id}/edit', data={
        'client_name': 'عميل الملخص',
        'phone': '01011112222',
        'address': 'عنوان',
        'region': 'القاهرة',
        'cod': '370',
        'shipping_fee': '70',
        'status': 'مرتجع',
        'collected_amount': '370'  # accidentally submitted full COD
    }, follow_redirects=True)
    assert res_edit.status_code == 200

    # Verify backend reset collected_amount to None so 370 is NOT shown as return shipping!
    with app.app_context():
        o = db.session.get(Order, order_id)
        assert o.status == 'مرتجع'
        assert o.collected_amount is None

    # Check html does not contain 'بتحصيل شحن: 370'
    res_orders = client.get('/?tab=orders&status=مرتجع')
    assert res_orders.status_code == 200
    assert 'بتحصيل شحن: 370' not in res_orders.get_data(as_text=True)













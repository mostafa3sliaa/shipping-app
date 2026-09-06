from app import db, app, Order
with app.app_context():
    r = Order.query.filter(Order.region != None).first()
    if r:
        region_str = r.region
        with open('test_out.txt', 'w', encoding='utf-8') as f:
            f.write(f'Region: "{region_str}"\n')
            
            orders = Order.query.filter_by(region=region_str).all()
            f.write(f'Exact Match Count: {len(orders)}\n')

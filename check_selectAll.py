with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()
    print('selectAll ID count:', html.count('id="selectAll"'))
    print('order-checkbox count:', html.count('order-checkbox'))

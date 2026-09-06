
    // Scanner focus logic
    document.addEventListener('DOMContentLoaded', function() {
        const scanTabButton = document.getElementById('scan-tab');
        const scanInput = document.getElementById('ajax_tracking_number');
        
        // Focus input when Scan tab is shown
        if (scanTabButton && scanInput) {
            scanTabButton.addEventListener('shown.bs.tab', function () {
                scanInput.focus();
            });
            
            // Auto-focus logic if we are already in the scan tab
            
                scanInput.focus();
                
                // Keep focus if clicking outside inputs or dropdowns
                document.body.addEventListener('click', function(e) {
                    if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT' && !e.target.closest('.dropdown')) {
                        scanInput.focus();
                    }
                });
            
        }
    });

    // Inline Scanner Logic in Orders Tab
    async function handleInlineScan(event) {
        event.preventDefault();
        const inputElement = document.getElementById('inline_tracking_number');
        const feedbackElement = document.getElementById('inlineScanFeedback');
        const query = inputElement.value.trim();
        
        if (!query) return;
        
        inputElement.value = ''; // clear for next scan
        feedbackElement.innerText = '';
        
        try {
            const response = await fetch(`/api/scan?q=${encodeURIComponent(query)}`);
            const data = await response.json();
            
            if (!response.ok) {
                feedbackElement.innerText = data.error || 'حدث خطأ أثناء البحث';
                return;
            }
            
            const orders = data.orders;
            const tbody = document.querySelector('#bulkForm table tbody');
            let addedAny = false;
            
            orders.forEach(order => {
                const tr = document.getElementById(`order-row-${order.id}`);
                const checkbox = document.getElementById(`checkbox-${order.id}`);
                
                if (tr && checkbox) {
                    addedAny = true;
                    // Move row to the top
                    tbody.prepend(tr);
                    
                    // Check the checkbox
                    checkbox.checked = true;
                    
                    // Highlight the row temporarily
                    const originalBg = tr.className;
                    tr.className = 'table-warning transition-all';
                    setTimeout(() => {
                        tr.className = originalBg;
                    }, 1500);
                }
            });
            
            if (!addedAny) {
                feedbackElement.innerText = 'هذا الأوردر غير موجود في الصفحة الحالية (أو تم مسحه)';
            }
            
        } catch (error) {
            feedbackElement.innerText = 'فشل الاتصال بالخادم';
        }
    }
    
    // === Live Search ===
    function liveSearch() {
        const input = document.getElementById('liveSearchInput').value.toLowerCase();
        const rows = document.querySelectorAll('tbody tr[id^="order-row-"]');
        
        rows.forEach(row => {
            const text = row.innerText.toLowerCase();
            if (text.includes(input)) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    }

    // Select all checkboxes logic
    const selectAllBtn = document.getElementById('selectAll');
    if (selectAllBtn) {
        selectAllBtn.addEventListener('change', function() {
            const checkboxes = document.querySelectorAll('.order-checkbox');
            for (let checkbox of checkboxes) {
                checkbox.checked = this.checked;
            }
        });
    }

    // Single Edit Modal JS
    async function openEditModal(orderId) {
        try {
            const response = await fetch('/api/order/' + orderId);
            const order = await response.json();
            
            document.getElementById('editModalTrackingNumber').innerText = order.tracking_number;
            document.getElementById('singleEditForm').action = '/order/' + order.id + '/edit';
            
            document.getElementById('edit_client_name').value = order.client_name;
            document.getElementById('edit_phone').value = order.phone;
            document.getElementById('edit_region').value = order.region;
            document.getElementById('edit_address').value = order.address;
            document.getElementById('edit_cod').value = order.cod;
            document.getElementById('edit_shipping_fee').value = order.shipping_fee;
            document.getElementById('edit_courier_name').value = order.courier_name;
            
            // Map old statuses visually if needed, otherwise select
            let status = order.status;
            if (status === 'جديد بالمخزن') status = 'مخزن';
            if (status === 'قيد التوصيل') status = 'مع المندوب';
            document.getElementById('edit_status').value = status;
            
            document.getElementById('edit_collected_amount').value = order.collected_amount;
            document.getElementById('edit_courier_fee').value = order.courier_fee;
            
            togglePartialDeliverySingle(document.getElementById('edit_status'));
            
            const modal = new bootstrap.Modal(document.getElementById('singleEditModal'));
            modal.show();
        } catch(e) {
            alert('حدث خطأ أثناء تحميل بيانات الأوردر');
        }
    }

    function togglePartialDeliverySingle(selectElement) {
        const fields = document.getElementById('single-accounting-fields');
        if (selectElement.value === 'تسليم جزئي' || selectElement.value === 'تم التوصيل') {
            fields.style.display = 'flex';
        } else {
            fields.style.display = 'none';
        }
    }


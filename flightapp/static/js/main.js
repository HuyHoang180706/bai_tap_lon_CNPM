// Hiển thị giá tiền theo hạng vé đang chọn (trang đặt vé online và bán vé tại quầy)
function initTicketForm() {
    const display = document.getElementById('price-display');
    if (!display)
        return;

    const radios = document.querySelectorAll('input[name="class_id"]');
    function update() {
        const checked = document.querySelector('input[name="class_id"]:checked');
        display.innerText = checked ? (parseInt(checked.dataset.price) || 0).toLocaleString('vi-VN') + ' ₫' : 'Chọn hạng vé';
    }

    radios.forEach(r => r.addEventListener('change', update));
    update();
}

// Đếm ngược thời gian giữ chỗ ở trang thanh toán
function initCountdown() {
    const el = document.getElementById('countdown');
    if (!el)
        return;

    let left = parseInt(el.dataset.remaining);
    function tick() {
        if (left <= 0) {
            el.innerText = '00:00';
            location.reload();
            return;
        }
        const m = Math.floor(left / 60), s = left % 60;
        el.innerText = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        left -= 1;
        setTimeout(tick, 1000);
    }
    tick();
}

window.addEventListener('load', function () {
    initTicketForm();
    initCountdown();
});

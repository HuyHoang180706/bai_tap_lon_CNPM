from datetime import datetime

from flask import redirect, request, flash
from flask_admin import Admin, BaseView, expose, AdminIndexView
from flask_admin.actions import action
from flask_admin.contrib.sqla import ModelView
from flask_admin.menu import MenuLink
from flask_login import logout_user, current_user
from wtforms import ValidationError

from flightapp import app, db, dao, utils
from flightapp.models import (Airport, TicketClass, Regulation, Route, Fare, Flight, FlightSeat, Stopover, User,
                              UserRoleEnum, FlightStatusEnum)

FLIGHT_STATUS_LABELS = {FlightStatusEnum.SCHEDULED: 'Đã lập lịch', FlightStatusEnum.CANCELLED: 'Đã hủy',
                        FlightStatusEnum.COMPLETED: 'Đã bay'}
ROLE_LABELS = {UserRoleEnum.ADMIN: 'Quản trị viên', UserRoleEnum.STAFF: 'Nhân viên',
               UserRoleEnum.CUSTOMER: 'Khách hàng'}


class MyAdminIndex(AdminIndexView):
    @expose('/')
    def index(self):
        stats, kpi = [], None
        if current_user.is_authenticated and current_user.user_role == UserRoleEnum.ADMIN:
            stats, kpi = dao.count_flights_by_route(), dao.stats_summary()
        return self.render('admin/index.html', stats=stats, kpi=kpi)


try:
    # Flask-Admin 2.x: chọn giao diện bằng theme
    from flask_admin.theme import Bootstrap4Theme
    admin = Admin(app=app, name='Quản trị chuyến bay', index_view=MyAdminIndex(), theme=Bootstrap4Theme())
except ImportError:
    # Flask-Admin 1.6.x: chọn giao diện bằng template_mode
    admin = Admin(app=app, name='Quản trị chuyến bay', index_view=MyAdminIndex(), template_mode='bootstrap4')


class AdminAuthenticated(ModelView):
    def is_accessible(self) -> bool:
        return current_user.is_authenticated and current_user.user_role == UserRoleEnum.ADMIN


# ======================================================================
# Thay đổi quy định (Yêu cầu 5)
# ======================================================================
class RegulationView(AdminAuthenticated):
    can_create = False
    can_delete = False
    column_list = [key for key, _, _ in utils.REG_FIELDS]
    form_columns = [key for key, _, _ in utils.REG_FIELDS]
    column_labels = {key: label for key, label, _ in utils.REG_FIELDS}

    def on_model_change(self, form, model, is_created):
        values = {key: getattr(model, key) for key, _, _ in utils.REG_FIELDS}
        with db.session.no_autoflush:
            errors, _ = utils.validate_regulation(values, dao.count_active_airports())
        if errors:
            raise ValidationError(' '.join(errors))


# ======================================================================
# Danh mục: sân bay, hạng vé, tuyến bay, bảng đơn giá
# ======================================================================
class AirportView(AdminAuthenticated):
    column_list = ['code', 'name', 'city', 'active']
    form_columns = ['code', 'name', 'city', 'active']
    column_searchable_list = ['code', 'name', 'city']
    column_labels = {'code': 'Mã sân bay', 'name': 'Tên sân bay', 'city': 'Thành phố', 'active': 'Hoạt động'}

    def on_model_change(self, form, model, is_created):
        model.code = (model.code or '').strip().upper()
        if not utils.AIRPORT_CODE_RE.match(model.code):
            raise ValidationError('Mã sân bay gồm đúng 3 chữ cái (ví dụ SGN).')
        if model.active:
            with db.session.no_autoflush:
                others = dao.count_active_airports(exclude_id=model.id)
                limit = dao.get_regulation().max_airports
            if others >= limit:
                raise ValidationError(f'Đã đạt số sân bay tối đa theo quy định ({limit}). '
                                      'Hãy tăng giới hạn ở mục Quy định hoặc ngừng hoạt động một sân bay.')

    def on_model_delete(self, model):
        if dao.airport_in_use(model.id):
            raise ValidationError(f'Sân bay {model.code} đang được dùng trong tuyến bay hoặc chuyến bay, '
                                  'chỉ có thể ngừng hoạt động.')


class TicketClassView(AdminAuthenticated):
    column_list = ['name', 'display_order', 'active']
    form_columns = ['name', 'display_order', 'active']
    column_labels = {'name': 'Tên hạng vé', 'display_order': 'Thứ tự hiển thị', 'active': 'Đang bán'}

    def on_model_change(self, form, model, is_created):
        if not (model.name or '').strip():
            raise ValidationError('Tên hạng vé không được để trống.')
        if not model.active:
            with db.session.no_autoflush:
                others = TicketClass.query.filter(TicketClass.active == True,
                                                  TicketClass.id != (model.id or 0)).count()
            if others == 0:
                raise ValidationError('Phải còn ít nhất một hạng vé đang bán.')

    def on_model_delete(self, model):
        if dao.class_in_use(model.id):
            raise ValidationError(f'{model.name} đã được dùng trong chuyến bay hoặc vé, chỉ có thể ngừng bán.')


class RouteView(AdminAuthenticated):
    column_list = ['origin', 'destination', 'active']
    form_columns = ['origin', 'destination', 'active']
    column_filters = ['active']
    column_labels = {'origin': 'Sân bay đi', 'destination': 'Sân bay đến', 'active': 'Đang khai thác'}

    def on_model_change(self, form, model, is_created):
        if model.origin is None or model.destination is None:
            raise ValidationError('Vui lòng chọn sân bay đi và sân bay đến.')
        if model.origin.id == model.destination.id:
            raise ValidationError('Sân bay đi và sân bay đến phải khác nhau.')
        if not model.origin.active or not model.destination.active:
            raise ValidationError('Chỉ tạo tuyến giữa các sân bay đang hoạt động.')

    def on_model_delete(self, model):
        if dao.route_has_flights(model.id):
            raise ValidationError(f'Tuyến {model.name} đã có chuyến bay, chỉ có thể ngừng khai thác.')


class FareView(AdminAuthenticated):
    """Bảng đơn giá vé theo (tuyến bay, hạng vé). Sửa giá trực tiếp trên danh sách."""
    column_list = ['route', 'ticket_class', 'price']
    form_columns = ['route', 'ticket_class', 'price']
    column_editable_list = ['price']
    column_labels = {'route': 'Tuyến bay', 'ticket_class': 'Hạng vé', 'price': 'Đơn giá (VNĐ)'}

    def on_model_change(self, form, model, is_created):
        if model.price is None or model.price <= 0:
            raise ValidationError('Đơn giá phải là số nguyên dương (VNĐ).')


# ======================================================================
# Quản lý chuyến bay (xem/tìm kiếm/sửa/xóa/hủy). Thêm chuyến bay: trang lập lịch của nhân viên.
# ======================================================================
class FlightView(AdminAuthenticated):
    can_create = False
    column_list = ['code', 'route', 'departure_time', 'duration_minutes', 'status']
    column_searchable_list = ['code']
    column_filters = ['code', 'departure_time', 'duration_minutes']
    column_default_sort = ('departure_time', True)
    form_columns = ['code', 'departure_time', 'duration_minutes']
    column_labels = {'code': 'Mã chuyến bay', 'route': 'Tuyến bay', 'departure_time': 'Ngày giờ khởi hành',
                     'duration_minutes': 'Thời gian bay (phút)', 'status': 'Trạng thái'}
    column_formatters = {'status': lambda v, c, m, p: FLIGHT_STATUS_LABELS.get(m.status, '')}

    def on_model_change(self, form, model, is_created):
        model.code = (model.code or '').strip().upper()
        if not utils.FLIGHT_CODE_RE.match(model.code):
            raise ValidationError('Mã chuyến bay gồm 3-20 ký tự: chữ in hoa, số hoặc dấu gạch ngang.')
        with db.session.no_autoflush:
            minimum = dao.get_regulation().min_flight_minutes
        if model.duration_minutes is None or model.duration_minutes < minimum:
            raise ValidationError(f'Thời gian bay tối thiểu là {minimum} phút.')

    def on_model_delete(self, model):
        if model.tickets:
            raise ValidationError(f'Chuyến bay {model.code} đã phát sinh vé nên không thể xóa. '
                                  'Hãy dùng thao tác Hủy chuyến.')

    @action('cancel', 'Hủy chuyến', 'Hủy các chuyến bay đã chọn?')
    def action_cancel(self, ids):
        cancelled, paid = dao.cancel_flights(ids)
        flash(f'Đã hủy {cancelled} chuyến bay.' + (f' Có {paid} vé đã thanh toán cần hoàn tiền thủ công.'
                                                    if paid else ''), 'warning' if paid else 'success')


class FlightSeatView(AdminAuthenticated):
    """Số ghế từng hạng của chuyến bay."""
    column_list = ['flight', 'ticket_class', 'total_seats']
    form_columns = ['flight', 'ticket_class', 'total_seats']
    column_labels = {'flight': 'Chuyến bay', 'ticket_class': 'Hạng vé', 'total_seats': 'Số ghế'}

    def on_model_change(self, form, model, is_created):
        if model.flight is None or model.ticket_class is None:
            raise ValidationError('Vui lòng chọn chuyến bay và hạng vé.')
        if model.total_seats is None or model.total_seats < 0:
            raise ValidationError('Số ghế phải là số nguyên không âm.')
        with db.session.no_autoflush:
            taken = dao.seats_taken(model.flight.id, model.ticket_class.id)
        if model.total_seats < taken:
            raise ValidationError(f'Đã có {taken} vé bán/giữ chỗ, số ghế không được nhỏ hơn con số này.')


class StopoverView(AdminAuthenticated):
    """Sân bay trung gian của chuyến bay."""
    column_list = ['flight', 'stop_order', 'airport', 'stop_minutes', 'note']
    form_columns = ['flight', 'stop_order', 'airport', 'stop_minutes', 'note']
    column_labels = {'flight': 'Chuyến bay', 'stop_order': 'STT', 'airport': 'Sân bay trung gian',
                     'stop_minutes': 'Thời gian dừng (phút)', 'note': 'Ghi chú'}

    def on_model_change(self, form, model, is_created):
        if model.flight is None:
            raise ValidationError('Vui lòng chọn chuyến bay.')
        with db.session.no_autoflush:
            reg = dao.get_regulation()
        route = model.flight.route
        if model.airport is None or model.stop_minutes is None:
            raise ValidationError('Vui lòng chọn sân bay và thời gian dừng.')
        if not (reg.min_stop_minutes <= model.stop_minutes <= reg.max_stop_minutes):
            raise ValidationError(f'Thời gian dừng phải từ {reg.min_stop_minutes} đến {reg.max_stop_minutes} phút.')
        if model.airport.id in (route.origin_id, route.destination_id):
            raise ValidationError('Sân bay trung gian không được trùng sân bay đi hoặc sân bay đến.')
        others = [s for s in model.flight.stopovers if s is not model]
        if len(others) + 1 > reg.max_intermediate_airports:
            raise ValidationError(f'Mỗi chuyến bay có tối đa {reg.max_intermediate_airports} sân bay trung gian.')
        if any(s.airport_id == model.airport.id for s in others):
            raise ValidationError('Sân bay này đã là sân bay trung gian của chuyến bay.')


# ======================================================================
# Tài khoản: khách hàng tự đăng ký, quản trị viên cấp quyền nhân viên / khóa tài khoản
# ======================================================================
class UserView(AdminAuthenticated):
    can_create = False
    can_edit = False
    can_delete = False
    column_list = ['id', 'name', 'username', 'user_role', 'active']
    column_searchable_list = ['name', 'username']
    form_columns = ['name']
    column_labels = {'name': 'Họ tên', 'username': 'Tên đăng nhập', 'user_role': 'Vai trò', 'active': 'Hoạt động'}
    column_formatters = {'user_role': lambda v, c, m, p: ROLE_LABELS.get(m.user_role, '')}

    def _done(self, n, text):
        flash(f'{text} {n} tài khoản (không áp dụng cho chính bạn).', 'success')

    @action('make_staff', 'Cấp quyền nhân viên', 'Cấp quyền nhân viên cho các tài khoản đã chọn?')
    def action_make_staff(self, ids):
        self._done(dao.set_user_role(ids, UserRoleEnum.STAFF, current_user.id), 'Đã cấp quyền nhân viên cho')

    @action('make_customer', 'Chuyển về khách hàng', 'Chuyển các tài khoản đã chọn về khách hàng?')
    def action_make_customer(self, ids):
        self._done(dao.set_user_role(ids, UserRoleEnum.CUSTOMER, current_user.id), 'Đã chuyển về khách hàng')

    @action('lock', 'Khóa tài khoản', 'Khóa các tài khoản đã chọn?')
    def action_lock(self, ids):
        self._done(dao.set_user_active(ids, False, current_user.id), 'Đã khóa')

    @action('unlock', 'Mở khóa', 'Mở khóa các tài khoản đã chọn?')
    def action_unlock(self, ids):
        self._done(dao.set_user_active(ids, True, current_user.id), 'Đã mở khóa')


# ======================================================================
# Thống kê, báo cáo (Yêu cầu 4)
# ======================================================================
class BaseAdminAuthenticated(BaseView):
    def is_accessible(self) -> bool:
        return current_user.is_authenticated and current_user.user_role == UserRoleEnum.ADMIN


class StatsView(BaseAdminAuthenticated):
    @expose('/')
    def index(self):
        year, month = utils.parse_month(request.args.get('month'), datetime.now())
        rows, total, total_flights = dao.stats_revenue_by_route(year, month)
        return self.render('admin/stats.html', year=year, month=month, month_value=f'{year:04d}-{month:02d}',
                           rows=rows, total=total, total_flights=total_flights)


class LogoutView(BaseAdminAuthenticated):
    @expose('/')
    def index(self):
        logout_user()
        return redirect('/admin')


admin.add_view(RegulationView(Regulation, db.session, name='Quy định'))
admin.add_view(AirportView(Airport, db.session, name='Sân bay', category='Danh mục'))
admin.add_view(TicketClassView(TicketClass, db.session, name='Hạng vé', category='Danh mục'))
admin.add_view(RouteView(Route, db.session, name='Tuyến bay', category='Danh mục'))
admin.add_view(FareView(Fare, db.session, name='Bảng giá vé', category='Danh mục'))
admin.add_view(FlightView(Flight, db.session, name='Chuyến bay', category='Quản lý bay'))
admin.add_view(FlightSeatView(FlightSeat, db.session, name='Số ghế từng hạng', category='Quản lý bay'))
admin.add_view(StopoverView(Stopover, db.session, name='Sân bay trung gian', category='Quản lý bay'))
admin.add_view(UserView(User, db.session, name='Tài khoản'))
admin.add_view(StatsView(name='Thống kê'))
admin.add_link(MenuLink(name='Lập lịch chuyến bay', url='/staff/flights/new'))
admin.add_link(MenuLink(name='Về trang web', url='/'))
admin.add_view(LogoutView(name='Đăng xuất'))

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

app = Flask(__name__)
app.secret_key = '&*(^UIGKJHJKHG&TFUTFYHGIUG&T*TUITGUIG&F'
app.config["SQLALCHEMY_DATABASE_URI"] = "mysql+pymysql://root:root@localhost/flightdb?charset=utf8mb4"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
#chống bán trùng ghế
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280,
                                           "isolation_level": "READ COMMITTED"}
app.config['PAGE_SIZE'] = 8

db = SQLAlchemy(app=app)
login = LoginManager(app=app)
login.login_view = 'login_view'
login.login_message = 'Vui lòng đăng nhập để tiếp tục.'

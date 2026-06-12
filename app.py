import json
import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get("DB_PATH", "food.db")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "aidy")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "aidy123")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
# Behind the nginx TLS proxy (production): trust X-Forwarded-* and require
# HTTPS-only session cookies.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if os.environ.get("SECURE_COOKIES") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True

BREADS = {"shami": "شامى", "baladi": "بلدى", "fransawi": "فرنساوى"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name_ar TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name_ar TEXT NOT NULL,
    price_shami REAL NOT NULL,
    price_baladi REAL NOT NULL,
    price_fransawi REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'open',
    delivery_fee REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    tip INTEGER NOT NULL DEFAULT 0,
    tip_amount REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (round_id, user_id)
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items(id),
    bread TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    salad INTEGER NOT NULL DEFAULT 1,
    tahini INTEGER NOT NULL DEFAULT 1
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    existing_cols = [r[1] for r in db.execute("PRAGMA table_info(order_items)")]
    for col in ("salad", "tahini"):
        if col not in existing_cols:
            db.execute(f"ALTER TABLE order_items ADD COLUMN {col} INTEGER NOT NULL DEFAULT 1")
    if db.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone() is None:
        db.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)),
        )
    # Additive menu sync: new categories/items in menu.json are inserted on
    # startup; existing rows (and their prices) are left untouched.
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "menu.json")
    with open(seed_path, encoding="utf-8") as f:
        menu = json.load(f)
    for sort, cat in enumerate(menu):
        row = db.execute(
            "SELECT id FROM categories WHERE name_ar = ?", (cat["category"],)
        ).fetchone()
        cat_id = row[0] if row else db.execute(
            "INSERT INTO categories (name_ar, sort) VALUES (?, ?)",
            (cat["category"], sort),
        ).lastrowid
        for name, ps, pb, pf in cat["items"]:
            if db.execute(
                "SELECT 1 FROM items WHERE category_id = ? AND name_ar = ?",
                (cat_id, name),
            ).fetchone() is None:
                db.execute(
                    "INSERT INTO items (category_id, name_ar, price_shami, price_baladi, price_fransawi)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (cat_id, name, ps, pb, pf),
                )
    db.commit()
    db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        if not user["is_admin"]:
            flash("Admins only.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def open_round():
    return get_db().execute(
        "SELECT * FROM rounds WHERE status = 'open' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def get_or_create_order(round_id, user_id):
    db = get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE round_id = ? AND user_id = ?", (round_id, user_id)
    ).fetchone()
    if order is None:
        db.execute(
            "INSERT INTO orders (round_id, user_id, created_at) VALUES (?, ?, ?)",
            (round_id, user_id, now()),
        )
        db.commit()
        order = db.execute(
            "SELECT * FROM orders WHERE round_id = ? AND user_id = ?",
            (round_id, user_id),
        ).fetchone()
    return order


def order_lines(order_id):
    return get_db().execute(
        """SELECT oi.id, oi.bread, oi.qty, oi.salad, oi.tahini, i.name_ar,
                  CASE oi.bread
                      WHEN 'shami' THEN i.price_shami
                      WHEN 'baladi' THEN i.price_baladi
                      ELSE i.price_fransawi
                  END AS price
           FROM order_items oi JOIN items i ON i.id = oi.item_id
           WHERE oi.order_id = ?
           ORDER BY oi.id""",
        (order_id,),
    ).fetchall()


def round_summary(round_row):
    """Everything Aidy needs about one round: per-person breakdown,
    sandwich counts, and the delivery/tip math."""
    db = get_db()
    orders = db.execute(
        """SELECT o.*, u.username FROM orders o
           JOIN users u ON u.id = o.user_id
           WHERE o.round_id = ? ORDER BY u.username""",
        (round_row["id"],),
    ).fetchall()

    people = []
    item_counts = {}
    bread_counts = {bread: 0 for bread in BREADS}
    total_sandwiches = 0
    for order in orders:
        lines = order_lines(order["id"])
        if not lines:
            continue
        food_total = sum(l["price"] * l["qty"] for l in lines)
        for l in lines:
            key = (l["name_ar"], l["bread"], l["salad"], l["tahini"])
            item_counts[key] = item_counts.get(key, 0) + l["qty"]
            bread_counts[l["bread"]] += l["qty"]
            total_sandwiches += l["qty"]
        people.append({
            "username": order["username"],
            "user_id": order["user_id"],
            "lines": lines,
            "food_total": food_total,
            "tip": order["tip"],
            "tip_amount": order["tip_amount"] if order["tip"] else 0,
        })

    participants = len(people)
    share = round_row["delivery_fee"] / participants if participants else 0
    for p in people:
        p["total"] = p["food_total"] + share + p["tip_amount"]

    food_sum = sum(p["food_total"] for p in people)
    tips_sum = sum(p["tip_amount"] for p in people)
    return {
        "round": round_row,
        "people": people,
        "participants": participants,
        "share": share,
        "total_sandwiches": total_sandwiches,
        "bread_counts": bread_counts,
        "item_counts": sorted(
            item_counts.items(),
            key=lambda kv: (list(BREADS).index(kv[0][1]), -kv[1], kv[0][0]),
        ),
        "food_sum": food_sum,
        "tips_sum": tips_sum,
        "grand_total": food_sum + round_row["delivery_fee"] + tips_sum,
    }


@app.context_processor
def inject_globals():
    return {"user": current_user(), "BREADS": BREADS}


@app.template_filter("money")
def money(value):
    return f"{value:.2f}"


@app.template_filter("extras")
def extras_label(line):
    """Arabic note for the restaurant call; sandwiches come with salad and
    tahini by default, so only deviations are spelled out."""
    if line["salad"] and line["tahini"]:
        return ""
    if line["salad"]:
        return "بدون طحينة"
    if line["tahini"]:
        return "بدون سلطة"
    return "بدون سلطة وطحينة"


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Name and password are required.")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                flash("That name is already taken.")
            else:
                user = db.execute(
                    "SELECT * FROM users WHERE username = ?", (username,)
                ).fetchone()
                session["user_id"] = user["id"]
                return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Wrong name or password.")
        else:
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    rnd = open_round()
    if rnd is None:
        return render_template("menu.html", round=None)
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY sort").fetchall()
    items = db.execute("SELECT * FROM items ORDER BY id").fetchall()
    items_by_cat = {}
    for item in items:
        items_by_cat.setdefault(item["category_id"], []).append(item)

    user = current_user()
    order = db.execute(
        "SELECT * FROM orders WHERE round_id = ? AND user_id = ?",
        (rnd["id"], user["id"]),
    ).fetchone()
    lines = order_lines(order["id"]) if order else []
    food_total = sum(l["price"] * l["qty"] for l in lines)
    participants = db.execute(
        """SELECT COUNT(DISTINCT o.user_id) AS n FROM orders o
           WHERE o.round_id = ? AND EXISTS
               (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id)""",
        (rnd["id"],),
    ).fetchone()["n"]
    share = rnd["delivery_fee"] / participants if participants else 0
    return render_template(
        "menu.html",
        round=rnd,
        categories=categories,
        items_by_cat=items_by_cat,
        order=order,
        lines=lines,
        food_total=food_total,
        participants=participants,
        share=share,
    )


@app.route("/order/add", methods=["POST"])
@login_required
def order_add():
    rnd = open_round()
    if rnd is None:
        flash("No open round right now.")
        return redirect(url_for("index"))
    item_id = request.form.get("item_id", type=int)
    bread = request.form.get("bread", "")
    qty = max(1, request.form.get("qty", default=1, type=int) or 1)
    salad = 1 if request.form.get("salad") else 0
    tahini = 1 if request.form.get("tahini") else 0
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None or bread not in BREADS:
        flash("Invalid item.")
        return redirect(url_for("index"))
    order = get_or_create_order(rnd["id"], current_user()["id"])
    existing = db.execute(
        """SELECT * FROM order_items
           WHERE order_id = ? AND item_id = ? AND bread = ? AND salad = ? AND tahini = ?""",
        (order["id"], item_id, bread, salad, tahini),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE order_items SET qty = qty + ? WHERE id = ?", (qty, existing["id"])
        )
    else:
        db.execute(
            "INSERT INTO order_items (order_id, item_id, bread, qty, salad, tahini)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (order["id"], item_id, bread, qty, salad, tahini),
        )
    db.commit()
    note = extras_label({"salad": salad, "tahini": tahini})
    flash(f"Added {item['name_ar']} ({BREADS[bread]}){' — ' + note if note else ''} ×{qty}")
    return redirect(url_for("index") + "#my-order")


@app.route("/order/remove/<int:line_id>", methods=["POST"])
@login_required
def order_remove(line_id):
    rnd = open_round()
    if rnd is None:
        flash("Round is closed; orders can no longer be changed.")
        return redirect(url_for("index"))
    db = get_db()
    db.execute(
        """DELETE FROM order_items WHERE id = ? AND order_id IN
               (SELECT id FROM orders WHERE round_id = ? AND user_id = ?)""",
        (line_id, rnd["id"], current_user()["id"]),
    )
    db.commit()
    return redirect(url_for("index") + "#my-order")


@app.route("/order/tip", methods=["POST"])
@login_required
def order_tip():
    rnd = open_round()
    if rnd is None:
        flash("Round is closed; orders can no longer be changed.")
        return redirect(url_for("index"))
    tip = 1 if request.form.get("tip") == "yes" else 0
    tip_amount = request.form.get("tip_amount", default=0.0, type=float) or 0.0
    if tip and tip_amount <= 0:
        flash("Please enter a tip amount greater than 0.")
        return redirect(url_for("index") + "#my-order")
    order = get_or_create_order(rnd["id"], current_user()["id"])
    db = get_db()
    db.execute(
        "UPDATE orders SET tip = ?, tip_amount = ? WHERE id = ?",
        (tip, tip_amount if tip else 0, order["id"]),
    )
    db.commit()
    flash("Tip choice saved." if tip else "No tip — saved.")
    return redirect(url_for("index") + "#my-order")


@app.route("/history")
@login_required
def history():
    db = get_db()
    user = current_user()
    rounds = db.execute(
        """SELECT r.* FROM rounds r
           JOIN orders o ON o.round_id = r.id
           WHERE o.user_id = ? AND r.status = 'closed'
             AND EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id)
           ORDER BY r.id DESC""",
        (user["id"],),
    ).fetchall()
    entries = []
    for rnd in rounds:
        summary = round_summary(rnd)
        mine = next((p for p in summary["people"] if p["user_id"] == user["id"]), None)
        if mine:
            entries.append({"round": rnd, "share": summary["share"], "me": mine})
    return render_template("history.html", entries=entries)


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    rnd = open_round()
    summary = round_summary(rnd) if rnd else None
    past = db.execute(
        "SELECT * FROM rounds WHERE status = 'closed' ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return render_template("admin.html", summary=summary, past=past)


@app.route("/admin/round/open", methods=["POST"])
@admin_required
def admin_open_round():
    if open_round() is not None:
        flash("A round is already open.")
        return redirect(url_for("admin"))
    fee = request.form.get("delivery_fee", default=0.0, type=float) or 0.0
    if fee < 0:
        flash("Delivery fee can't be negative.")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute(
        "INSERT INTO rounds (status, delivery_fee, created_at) VALUES ('open', ?, ?)",
        (fee, now()),
    )
    db.commit()
    flash("Round opened — the team can order now.")
    return redirect(url_for("admin"))


@app.route("/admin/round/fee", methods=["POST"])
@admin_required
def admin_update_fee():
    rnd = open_round()
    if rnd is None:
        flash("No open round.")
        return redirect(url_for("admin"))
    fee = request.form.get("delivery_fee", default=0.0, type=float) or 0.0
    if fee < 0:
        flash("Delivery fee can't be negative.")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("UPDATE rounds SET delivery_fee = ? WHERE id = ?", (fee, rnd["id"]))
    db.commit()
    flash("Delivery fee updated.")
    return redirect(url_for("admin"))


@app.route("/admin/round/close", methods=["POST"])
@admin_required
def admin_close_round():
    rnd = open_round()
    if rnd is None:
        flash("No open round.")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute(
        "UPDATE rounds SET status = 'closed', closed_at = ? WHERE id = ?",
        (now(), rnd["id"]),
    )
    db.commit()
    flash("Round closed.")
    return redirect(url_for("admin_round_detail", round_id=rnd["id"]))


@app.route("/admin/round/<int:round_id>")
@admin_required
def admin_round_detail(round_id):
    rnd = get_db().execute(
        "SELECT * FROM rounds WHERE id = ?", (round_id,)
    ).fetchone()
    if rnd is None:
        flash("Round not found.")
        return redirect(url_for("admin"))
    return render_template("round_detail.html", summary=round_summary(rnd))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

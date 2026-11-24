"""
Billing demo: a tiny web page that saves bills into MySQL.

Run:   python app.py
Open:  http://127.0.0.1:5000

Every bill you save becomes rows in three MySQL tables:
bills (header), bill_items (one row per product) and products (stock goes down).
The receipt page shows the exact SQL that ran, so students can see where the data came from.
"""

from decimal import Decimal

import mysql.connector
from flask import (Flask, flash, redirect, render_template, request,
                   session, url_for)
from jinja2 import DictLoader

# ---------------------------------------------------------------------
# 1. Change these to match YOUR MySQL login
# ---------------------------------------------------------------------
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "Tejas@123",
    "database": "billing_demo",
}

SHOP_NAME = "Chalkboard Stationery Mart"
ITEM_ROWS = 5  # how many product rows the bill form shows

app = Flask(__name__)
app.secret_key = "classroom-demo-only"


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


def show_sql(sql, params):
    """Put the values into the SQL text so students can read it (display only)."""
    parts = sql.split("%s")
    out = parts[0]
    for value, part in zip(params, parts[1:]):
        if value is None:
            text = "NULL"
        elif isinstance(value, (int, float, Decimal)):
            text = str(value)
        else:
            text = "'" + str(value).replace("'", "''") + "'"
        out += text + part
    return " ".join(out.split()) + ";"


# ---------------------------------------------------------------------
# 2. Pages
# ---------------------------------------------------------------------
@app.get("/")
def new_bill():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT product_id, product_name, price, stock "
                "FROM products WHERE stock > 0 ORDER BY category, product_name")
    products = cur.fetchall()
    cur.close()
    db.close()
    return render_template("new_bill.html", title="New bill",
                           products=products, rows=ITEM_ROWS)


@app.post("/bill")
def create_bill():
    name = request.form.get("customer_name", "").strip()
    phone = request.form.get("phone", "").strip() or None
    mode = request.form.get("payment_mode", "Cash")

    lines = []
    for pid, qty in zip(request.form.getlist("product_id"),
                        request.form.getlist("qty")):
        if pid and qty:
            try:
                qty = int(qty)
            except ValueError:
                continue
            if qty > 0:
                lines.append((int(pid), qty))

    if not name or not lines:
        flash("Enter a customer name and at least one product with a quantity.", "error")
        return redirect(url_for("new_bill"))

    db = get_db()
    cur = db.cursor(dictionary=True)
    log = []

    def run(sql, params=()):
        cur.execute(sql, params)
        log.append(show_sql(sql, params))

    try:
        # Header row first, so we get a bill_id for the items
        run("INSERT INTO bills (customer_name, phone, payment_mode) VALUES (%s, %s, %s)",
            (name, phone, mode))
        bill_id = cur.lastrowid

        total = Decimal("0")
        for pid, qty in lines:
            cur.execute("SELECT product_name, price, stock FROM products WHERE product_id = %s", (pid,))
            product = cur.fetchone()
            if product is None:
                raise ValueError("That product no longer exists.")
            if qty > product["stock"]:
                raise ValueError(f"Only {product['stock']} left of {product['product_name']}.")

            line_total = product["price"] * qty
            total += line_total
            run("INSERT INTO bill_items (bill_id, product_id, quantity, unit_price, line_total) "
                "VALUES (%s, %s, %s, %s, %s)",
                (bill_id, pid, qty, product["price"], line_total))
            run("UPDATE products SET stock = stock - %s WHERE product_id = %s", (qty, pid))

        run("UPDATE bills SET total_amount = %s WHERE bill_id = %s", (total, bill_id))
        db.commit()  # nothing is saved until this line
    except (ValueError, mysql.connector.Error) as err:
        db.rollback()  # undo every statement of this bill
        flash(f"Bill not saved: {err}", "error")
        return redirect(url_for("new_bill"))
    finally:
        cur.close()
        db.close()

    session["sql_log"] = {"bill_id": bill_id, "statements": log}
    return redirect(url_for("receipt", bill_id=bill_id))


@app.get("/bill/<int:bill_id>")
def receipt(bill_id):
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("SELECT * FROM bills WHERE bill_id = %s", (bill_id,))
    bill = cur.fetchone()
    cur.execute("""
        SELECT p.product_name, i.quantity, i.unit_price, i.line_total
        FROM bill_items i
        JOIN products p ON p.product_id = i.product_id
        WHERE i.bill_id = %s
        ORDER BY i.item_id""", (bill_id,))
    items = cur.fetchall()
    cur.close()
    db.close()

    if bill is None:
        flash(f"Bill {bill_id} was not found (maybe it was deleted with SQL).", "error")
        return redirect(url_for("all_bills"))

    sql_log = session.get("sql_log")
    statements = sql_log["statements"] if sql_log and sql_log["bill_id"] == bill_id else None
    if statements:
        session.pop("sql_log")
    return render_template("receipt.html", title=f"Bill #{bill_id}",
                           bill=bill, items=items, statements=statements)


@app.get("/bills")
def all_bills():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT b.bill_id, b.customer_name, b.payment_mode, b.bill_date,
               b.total_amount, COUNT(i.item_id) AS items
        FROM bills b
        LEFT JOIN bill_items i ON i.bill_id = b.bill_id
        GROUP BY b.bill_id
        ORDER BY b.bill_id DESC""")
    bills = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS bill_count, COALESCE(SUM(total_amount), 0) AS revenue FROM bills")
    summary = cur.fetchone()
    cur.execute("SELECT * FROM products ORDER BY product_id")
    products = cur.fetchall()
    cur.close()
    db.close()
    return render_template("bills.html", title="All bills",
                           bills=bills, summary=summary, products=products)


@app.errorhandler(mysql.connector.Error)
def db_error(err):
    return render_template("db_error.html", title="Database error", err=err), 500


@app.context_processor
def shop():
    return {"shop_name": SHOP_NAME}


# ---------------------------------------------------------------------
# 3. HTML (kept in this file so the demo is a single Python file)
# ---------------------------------------------------------------------
TEMPLATES = {
"layout.html": """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} · {{ shop_name }}</title>
<style>
  :root { --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --bg:#f6f7fb; --card:#fff;
          --brand:#2563eb; --brand-soft:#eff4ff; --bad:#b91c1c; --bad-soft:#fef2f2; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; color:var(--ink); background:var(--bg); }
  header { background:var(--card); border-bottom:1px solid var(--line); padding:14px 24px;
           display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
  .brand { font-weight:700; font-size:18px; }
  .brand span { font-weight:400; color:var(--muted); font-size:13px; margin-left:6px; }
  nav a { margin-left:18px; color:var(--brand); text-decoration:none; font-weight:600; }
  main { max-width:900px; margin:24px auto; padding:0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:22px; margin-bottom:20px; }
  h1 { font-size:22px; margin:0 0 16px; } h2 { font-size:16px; margin:0 0 12px; }
  label { display:block; font-size:13px; color:var(--muted); margin-bottom:4px; }
  input, select { width:100%; padding:9px 10px; border:1px solid var(--line); border-radius:8px; font:inherit; background:#fff; }
  .grid { display:grid; grid-template-columns:2fr 1.4fr 1fr; gap:14px; margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th { text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
       border-bottom:1px solid var(--line); padding:8px 6px; }
  td { border-bottom:1px solid var(--line); padding:8px 6px; vertical-align:middle; }
  .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .total-row td { font-weight:700; font-size:16px; border-bottom:none; }
  button, .btn { background:var(--brand); color:#fff; border:none; border-radius:8px; padding:10px 18px;
                 font:inherit; font-weight:600; cursor:pointer; text-decoration:none; display:inline-block; }
  .btn.ghost { background:var(--brand-soft); color:var(--brand); }
  .actions { display:flex; gap:10px; justify-content:flex-end; margin-top:18px; }
  .flash { padding:10px 14px; border-radius:8px; margin-bottom:16px; background:var(--bad-soft); color:var(--bad); }
  .stats { display:grid; grid-template-columns:repeat(2,1fr); gap:14px; margin-bottom:20px; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
  .stat b { display:block; font-size:24px; } .stat span { color:var(--muted); font-size:13px; }
  .muted { color:var(--muted); }
  pre { background:#0f172a; color:#e2e8f0; padding:14px; border-radius:8px; overflow-x:auto; font-size:13px; line-height:1.6; margin:0; }
  .sql-kw { color:#93c5fd; }
  .receipt-head { display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px; margin-bottom:16px; }
  a.link { color:var(--brand); text-decoration:none; font-weight:600; }
  .low { color:var(--bad); font-weight:600; }
  @media (max-width:640px) { .grid { grid-template-columns:1fr; } nav a { margin:0 14px 0 0; } }
  @media print { header, .no-print { display:none; } body { background:#fff; } .card { border:none; } }
</style></head>
<body>
<header>
  <div class="brand">{{ shop_name }}<span>billing demo</span></div>
  <nav><a href="{{ url_for('new_bill') }}">New bill</a><a href="{{ url_for('all_bills') }}">All bills</a></nav>
</header>
<main>
  {% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="flash">{{ msg }}</div>{% endfor %}
  {% block content %}{% endblock %}
</main>
</body></html>""",

"new_bill.html": """{% extends "layout.html" %}{% block content %}
<form class="card" method="post" action="{{ url_for('create_bill') }}">
  <h1>New bill</h1>
  <div class="grid">
    <div><label>Customer name *</label><input name="customer_name" required placeholder="e.g. Priya Kulkarni"></div>
    <div><label>Phone</label><input name="phone" maxlength="15" placeholder="optional"></div>
    <div><label>Payment</label>
      <select name="payment_mode"><option>Cash</option><option>UPI</option><option>Card</option></select></div>
  </div>
  <table>
    <thead><tr><th>Product</th><th style="width:90px">Qty</th><th class="num" style="width:110px">Price</th><th class="num" style="width:120px">Amount</th></tr></thead>
    <tbody>
    {% for r in range(rows) %}
      <tr>
        <td><select name="product_id" class="product">
              <option value="">-- choose --</option>
              {% for p in products %}<option value="{{ p.product_id }}" data-price="{{ p.price }}">{{ p.product_name }} ({{ p.stock }} left)</option>{% endfor %}
            </select></td>
        <td><input name="qty" class="qty" type="number" min="1" value="1"></td>
        <td class="num price">–</td>
        <td class="num amount">–</td>
      </tr>
    {% endfor %}
      <tr class="total-row"><td colspan="3" class="num">Total</td><td class="num" id="grand">₹0.00</td></tr>
    </tbody>
  </table>
  <div class="actions"><button type="submit">Save bill to MySQL</button></div>
</form>
<script>
  const rupee = n => "₹" + n.toFixed(2);
  function recalc() {
    let grand = 0;
    document.querySelectorAll("tbody tr").forEach(tr => {
      const sel = tr.querySelector(".product"); if (!sel) return;
      const opt = sel.selectedOptions[0];
      const price = opt && opt.dataset.price ? parseFloat(opt.dataset.price) : 0;
      const qty = parseInt(tr.querySelector(".qty").value) || 0;
      tr.querySelector(".price").textContent = price ? rupee(price) : "–";
      tr.querySelector(".amount").textContent = price ? rupee(price * qty) : "–";
      grand += price * qty;
    });
    document.getElementById("grand").textContent = rupee(grand);
  }
  document.addEventListener("input", recalc); document.addEventListener("change", recalc);
</script>
{% endblock %}""",

"receipt.html": """{% extends "layout.html" %}{% block content %}
<div class="card">
  <div class="receipt-head">
    <div><h1 style="margin:0">Bill #{{ bill.bill_id }}</h1>
         <div class="muted">{{ bill.bill_date.strftime('%d %b %Y, %I:%M %p') }}</div></div>
    <div style="text-align:right"><b>{{ bill.customer_name }}</b><br>
         <span class="muted">{{ bill.phone or '' }}</span><br>
         <span class="muted">Paid by {{ bill.payment_mode }}</span></div>
  </div>
  <table>
    <thead><tr><th>Product</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Amount</th></tr></thead>
    <tbody>
    {% for i in items %}
      <tr><td>{{ i.product_name }}</td><td class="num">{{ i.quantity }}</td>
          <td class="num">₹{{ '%.2f'|format(i.unit_price) }}</td><td class="num">₹{{ '%.2f'|format(i.line_total) }}</td></tr>
    {% else %}
      <tr><td colspan="4" class="muted">No items on this bill.</td></tr>
    {% endfor %}
      <tr class="total-row"><td colspan="3" class="num">Total</td><td class="num">₹{{ '%.2f'|format(bill.total_amount) }}</td></tr>
    </tbody>
  </table>
  <div class="actions no-print">
    <button class="btn ghost" onclick="window.print()">Print</button>
    <a class="btn" href="{{ url_for('new_bill') }}">Next bill</a>
  </div>
</div>
{% if statements %}
<div class="card no-print">
  <h2>The SQL that just ran in MySQL</h2>
  <p class="muted" style="margin-top:0">Saving this bill sent {{ statements|length }} statements, then COMMIT.</p>
  <pre>{% for s in statements %}{{ s }}
{% endfor %}COMMIT;</pre>
</div>
{% endif %}
{% endblock %}""",

"bills.html": """{% extends "layout.html" %}{% block content %}
<div class="stats">
  <div class="stat"><b>{{ summary.bill_count }}</b><span>bills in MySQL</span></div>
  <div class="stat"><b>₹{{ '%.2f'|format(summary.revenue) }}</b><span>total sales</span></div>
</div>
<div class="card">
  <h2>Bills (table: bills)</h2>
  <table>
    <thead><tr><th>Bill</th><th>Customer</th><th>Payment</th><th>Date</th><th class="num">Items</th><th class="num">Total</th></tr></thead>
    <tbody>
    {% for b in bills %}
      <tr><td><a class="link" href="{{ url_for('receipt', bill_id=b.bill_id) }}">#{{ b.bill_id }}</a></td>
          <td>{{ b.customer_name }}</td><td>{{ b.payment_mode }}</td>
          <td>{{ b.bill_date.strftime('%d %b, %I:%M %p') }}</td>
          <td class="num">{{ b.items }}</td><td class="num">₹{{ '%.2f'|format(b.total_amount) }}</td></tr>
    {% else %}
      <tr><td colspan="6" class="muted">No bills yet. Make one from “New bill”.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
<div class="card">
  <h2>Stock (table: products)</h2>
  <table>
    <thead><tr><th>ID</th><th>Product</th><th>Category</th><th class="num">Price</th><th class="num">Stock</th></tr></thead>
    <tbody>
    {% for p in products %}
      <tr><td>{{ p.product_id }}</td><td>{{ p.product_name }}</td><td>{{ p.category }}</td>
          <td class="num">₹{{ '%.2f'|format(p.price) }}</td>
          <td class="num {{ 'low' if p.stock < 10 }}">{{ p.stock }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}""",

"db_error.html": """{% extends "layout.html" %}{% block content %}
<div class="card">
  <h1>Could not talk to MySQL</h1>
  <div class="flash">{{ err }}</div>
  <ul class="muted">
    <li>Is the MySQL server running?</li>
    <li>Is the password in <code>DB_CONFIG</code> (top of app.py) correct?</li>
    <li>Did you run <code>schema.sql</code> to create the <code>billing_demo</code> database?</li>
  </ul>
</div>
{% endblock %}""",
}
app.jinja_loader = DictLoader(TEMPLATES)


if __name__ == "__main__":
    app.run(debug=True)

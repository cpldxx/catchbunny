"""Mug & Co: a small shop with seeded bugs, the benchmark target and the demo.

Every bug is behind a flag, so the same app is both the buggy build and the
fixed build:

    catchbunny demo                          # every bug on, at http://127.0.0.1:8811
    catchbunny demo --port 8812 --bugs none  # the fixed build

It lives in the package, not beside it, so that an installed copy can start it:
the first thing a new user does is point Catchbunny at something with bugs in it.

It also contains *traps*: correct behaviour that a naive tool reports as a bug
(a 401 on a wrong password, a 422 on bad input, a "Copy link" button whose only
effect is the clipboard, a "Like" button that only toggles a class).

It is dressed as a real small shop, because it is also what people see in
recordings. The dressing is presentation only: every control, label, id and
handler a tester can touch is the same in both builds, the page loads nothing
from another origin, and no decoration is interactive - so the look can change
without moving a single benchmark number. (That includes placeholders: a
placeholder gives a field an accessible name, which would quietly repair the
unlabelled newsletter field.)
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

# id -> what a tool must report to get credit. `semantic` bugs raise no error
# anywhere: only a tester that understands the page can find them.
BUGS: dict[str, dict[str, Any]] = {
    "checkout_500": {"title": "Placing an order with quantity >= 2 returns 500", "semantic": False},
    "reviews_crash": {"title": "Show reviews throws on the Desk Lamp page", "semantic": False},
    "broken_image": {"title": "Steel Bottle image is missing", "semantic": False},
    "dead_coupon": {"title": "Apply coupon button does nothing", "semantic": False},
    "search_500": {"title": "Searching for % or a quote returns 500", "semantic": False},
    "unlabeled_input": {"title": "Newsletter email field has no accessible label", "semantic": False},
    "mobile_overflow": {"title": "Products page overflows on phones", "semantic": False},
    "prefs_error": {"title": "Account page fails to load preferences", "semantic": False},
    "dead_link": {"title": "Shipping policy link is a 404", "semantic": False},
    "negative_qty": {"title": "A negative quantity is accepted and makes the total negative", "semantic": True},
    "stale_badge": {"title": "Cart count in the nav does not drop after removing an item", "semantic": True},
}
PRODUCTS = {
    1: ("Blue Mug", 12.0, "mug"),
    2: ("Canvas Tote", 18.5, "tote"),
    3: ("Desk Lamp", 39.0, "lamp"),
    4: ("Notebook Set", 9.75, "notebook"),
    5: ("Steel Bottle", 22.0, "bottle"),
    6: ("Wool Socks", 14.0, "socks"),
}
USER, PASSWORD = "demo", "demo-pass"
# One stylesheet, inlined in every page. System fonts only: a web font would be a request to another origin.
CSS = """
:root{--ink:#161a23;--muted:#5d6675;--line:#dfe3ea;--panel:#f3f5f9;--accent:#2445d6;--accent-deep:#162a8c;--tint:#e9edfd;--err:#b3261e}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);font:16px/1.5 "Avenir Next","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--ink);text-underline-offset:3px;text-decoration-color:#b9c0cc}a:hover{text-decoration-color:var(--ink)}
h1{font-size:34px;line-height:1.15;margin:0 0 12px;letter-spacing:-.01em}h2{font-size:20px;margin:40px 0 16px}p{margin:0 0 14px}
img{max-width:100%;height:auto;display:block}
header{border-bottom:1px solid var(--line);background:#fff}
nav{max-width:1088px;margin:0 auto;padding:14px 24px;display:flex;gap:6px 22px;flex-wrap:wrap;align-items:center}
.brand{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:18px;margin-right:10px}
nav a{color:var(--muted);text-decoration:none;font-weight:500}nav a:hover{color:var(--ink)}
nav form{margin-left:auto;display:flex;gap:8px}nav input{width:190px}
main{max-width:1088px;margin:0 auto;padding:40px 24px 72px}
button,input,select{font:inherit}
input{border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:#fff;color:var(--ink);max-width:100%}
input:focus-visible,button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button,.btn{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:9px 16px;background:#fff;color:var(--ink);font-weight:600;font-size:15px;cursor:pointer;text-decoration:none}
button:hover,.btn:hover{border-color:#b9c0cc}
.primary,.primary:hover{background:var(--accent);border-color:var(--accent);color:#fff}.primary:hover{background:var(--accent-deep)}
.on,.on:hover{background:var(--tint);border-color:var(--accent);color:var(--accent-deep)}
.err{color:var(--err)}.muted{color:var(--muted)}
.hero{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:24px;align-items:center;background:var(--tint);border-radius:20px;padding:40px 40px 0;overflow:hidden}
.hero h1{font-size:52px;margin-bottom:8px}.hero p{font-size:20px;color:#3a4460;padding-bottom:40px}.hero svg{display:block;width:100%;height:auto}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
.card{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}
.card img{width:100%}.card-body{display:flex;justify-content:space-between;gap:12px;align-items:baseline;padding:14px 16px 16px}
.card a{font-weight:600;text-decoration:none}.card a:hover{text-decoration:underline}.price{color:var(--muted);font-variant-numeric:tabular-nums}
.product{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:40px;align-items:start}
.product .shot{border-radius:20px;overflow:hidden;border:1px solid var(--line)}.product .shot img{width:100%}
.product .price{font-size:22px;color:var(--ink)}.product form{margin:24px 0 16px}.more{display:flex;gap:8px;flex-wrap:wrap}
label{display:block;font-weight:600;font-size:14px;margin-bottom:6px}
#qty{width:104px;margin-right:8px}main form p input{width:100%;max-width:380px}
#reviews{padding:0;margin:20px 0 0;list-style:none}#reviews li{padding:10px 0;border-top:1px solid var(--line)}
table{width:100%;border-collapse:collapse}th{font-size:13px;font-weight:600;color:var(--muted);text-align:left}
th,td{padding:12px 8px;border-bottom:1px solid var(--line);vertical-align:middle}td form{margin:0}
.sum{font-size:18px;margin-top:20px}.narrow{max-width:460px}
.panel{border:1px solid var(--line);border-radius:16px;padding:24px}
main ul{padding-left:20px}main li{margin:6px 0}
footer{background:var(--panel);border-top:1px solid var(--line)}
.foot{max-width:1088px;margin:0 auto;padding:28px 24px;display:flex;gap:14px 28px;flex-wrap:wrap;align-items:center}
.foot form{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.foot input{width:220px}.foot .note{margin:0 0 0 auto;font-size:14px;color:var(--muted)}
@media (max-width:720px){h1{font-size:28px}main{padding:28px 16px 56px}nav,.foot{padding-left:16px;padding-right:16px}nav form{margin-left:0;width:100%}nav input{flex:1;width:auto;min-width:0}
.hero{grid-template-columns:1fr;padding:28px 24px 0}.hero h1{font-size:38px}.hero p{padding-bottom:8px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.product{grid-template-columns:1fr;gap:24px}.foot .note{margin-left:0}.card-body{flex-direction:column;gap:2px}}
"""

# Product pictures, drawn here so the shop needs no image files. Each is (backdrop colour, drawing on a 480x360 canvas).
_ART: dict[str, tuple[str, str]] = {
    "mug": ("#e9edfd", '<path d="M318 168h26a40 40 0 0 1 0 80h-26" fill="none" stroke="#2445d6" stroke-width="22"/><rect x="162" y="120" width="160" height="170" rx="22" fill="#2445d6"/>'
            '<ellipse cx="242" cy="124" rx="80" ry="14" fill="#162a8c"/><rect x="184" y="150" width="16" height="110" rx="8" fill="#fff" opacity=".28"/>'
            '<path d="M214 92c-10-12 10-20 0-34M256 92c-10-12 10-20 0-34" fill="none" stroke="#9fb0ee" stroke-width="7" stroke-linecap="round"/>'),
    "tote": ("#f1e9dc", '<path d="M196 150c0-64 88-64 88 0" fill="none" stroke="#8a6f43" stroke-width="12"/><path d="M148 146h184l18 150H130z" fill="#d9c7a6"/>'
             '<path d="M148 146h184l3 22H145z" fill="#c4b08a"/><circle cx="240" cy="226" r="26" fill="#2445d6"/><circle cx="240" cy="226" r="11" fill="#d9c7a6"/>'),
    "lamp": ("#dfe9e0", '<ellipse cx="208" cy="292" rx="72" ry="13" fill="#20242c"/><path d="M208 288 168 184 264 120" fill="none" stroke="#20242c" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>'
             '<circle cx="168" cy="184" r="10" fill="#4d7a5c"/><path d="M246 82l88 40-34 66-92-44z" fill="#20242c"/><path d="M228 150l66 32-8 12-64-30z" fill="#f4d35e"/>'),
    "notebook": ("#f6e3e0", '<rect x="128" y="196" width="232" height="96" rx="8" fill="#20242c" transform="rotate(-4 244 244)"/><rect x="118" y="150" width="232" height="96" rx="8" fill="#2445d6" transform="rotate(3 234 198)"/>'
                 '<rect x="124" y="98" width="232" height="96" rx="8" fill="#c8553d" transform="rotate(-2 240 146)"/><rect x="150" y="122" width="92" height="26" rx="4" fill="#fff" opacity=".9" transform="rotate(-2 240 146)"/>'
                 '<rect x="318" y="98" width="10" height="96" fill="#8f3a29" transform="rotate(-2 240 146)"/>'),
    "bottle": ("#e3eef7", '<rect x="214" y="58" width="52" height="34" rx="8" fill="#46566f"/><rect x="222" y="88" width="36" height="20" fill="#6c7d96"/>'
               '<path d="M198 132a24 24 0 0 1 24-24h36a24 24 0 0 1 24 24v152a16 16 0 0 1-16 16h-52a16 16 0 0 1-16-16z" fill="#8fa1b8"/><rect x="198" y="184" width="84" height="46" fill="#2445d6"/>'
               '<rect x="212" y="128" width="12" height="152" rx="6" fill="#fff" opacity=".4"/>'),
    "socks": ("#efe9df", '<path d="M150 84h62v118l44 40a26 26 0 0 1-36 38l-70-64z" fill="#c8553d"/><rect x="150" y="84" width="62" height="26" fill="#f6ead8"/><path d="M256 242a26 26 0 0 1-36 38l-22-20z" fill="#8f3a29"/>'
              '<path d="M262 104h62v118l44 40a26 26 0 0 1-36 38l-70-64z" fill="#2445d6"/><rect x="262" y="104" width="62" height="26" fill="#f6ead8"/><path d="M368 262a26 26 0 0 1-36 38l-22-20z" fill="#162a8c"/>'),
}


def art(slug: str) -> str:
    backdrop, drawing = _ART[slug]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 360" width="480" height="360"><rect width="480" height="360" fill="{backdrop}"/>'
            f'<ellipse cx="240" cy="304" rx="150" ry="12" fill="#000" opacity=".07"/>{drawing}</svg>')


# The home page's shelf: three of the products standing on one line. Decoration, hidden from assistive technology.
HERO_ART = (
    '<svg viewBox="0 0 720 330" aria-hidden="true"><rect x="0" y="300" width="720" height="30" fill="#162a8c"/>'
    f'<g transform="translate(-40 6)">{_ART["lamp"][1]}</g><g transform="translate(250 76) scale(.76)">{_ART["mug"][1]}</g>'
    f'<g transform="translate(410 82) scale(.72)">{_ART["notebook"][1]}</g></svg>'
)
MARK = ('<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M17 9h2.5a3 3 0 0 1 0 6H17" fill="none" stroke="#2445d6" stroke-width="2.4"/>'
        '<rect x="4" y="5" width="13" height="15" rx="3" fill="#2445d6"/></svg>')


def create_shop(bugs: set[str], latency_ms: int = 0) -> FastAPI:
    app = FastAPI()
    sessions: dict[str, dict[str, Any]] = {}

    def on(bug: str) -> bool:
        return bug in bugs

    @app.middleware("http")
    async def session_middleware(request: Request, call_next: Any) -> Response:
        if latency_ms:
            await asyncio.sleep(latency_ms / 1000)  # stand-in for a real backend's latency
        sid = request.cookies.get("sid") or secrets.token_hex(8)
        request.state.s = sessions.setdefault(sid, {"cart": {}, "orders": [], "user": None, "badge": 0})
        response = await call_next(request)
        response.set_cookie("sid", sid, httponly=True)
        return response

    def page(request: Request, title: str, body: str, status: int = 200) -> HTMLResponse:
        s = request.state.s
        count = s["badge"] if on("stale_badge") else sum(s["cart"].values())
        label = "" if on("unlabeled_input") else ' aria-label="Email address"'
        html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} - Mug &amp; Co</title><style>{CSS}</style></head>
<body><header><nav><span class="brand">{MARK}Mug &amp; Co</span>
<a href="/">Home</a><a href="/products">Products</a><a href="/cart">Cart ({count})</a><a href="/orders">Orders</a>
<a href="/account">Account</a><a href="/about">About</a>
<form action="/search" method="get" role="search"><input type="search" name="q" aria-label="Search products">
<button type="submit">Search</button></form></nav></header>
<main>{body}</main>
<footer><div class="foot"><a href="/shipping">Shipping policy</a>
<form id="newsletter" onsubmit="return subscribe(event)"><input type="email" name="email" required{label}>
<button type="submit">Subscribe</button> <span id="nl-status"></span></form>
<button type="button" onclick="navigator.clipboard &amp;&amp; navigator.clipboard.writeText(location.href).catch(() =&gt; 0)">Copy link</button>
<p class="note">Mug &amp; Co is a demo shop. Nothing here is for sale.</p></div></footer>
<script>
function subscribe(e) {{
  e.preventDefault();
  const email = e.target.email.value;
  fetch('/api/newsletter', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{email}})}})
    .then((r) => {{ document.getElementById('nl-status').textContent = r.ok ? 'Subscribed' : 'Enter a valid email'; }});
  return false;
}}
</script></body></html>"""
        return HTMLResponse(html, status_code=status)

    def img(pid: int) -> str:
        name, _, slug = PRODUCTS[pid]
        src = f"/static/img/{slug}-v2.svg" if pid == 5 and on("broken_image") else f"/static/img/{slug}.svg"
        return f'<img src="{src}" alt="{name}" width="480" height="360">'

    # ---------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        cards = "".join(f'<div class="card">{img(i)}<div class="card-body"><a href="/products/{i}">{PRODUCTS[i][0]}</a></div></div>' for i in (1, 2, 3))
        hero = f'<section class="hero"><div><h1>Mug &amp; Co</h1><p>Small things for your desk.</p></div>{HERO_ART}</section>'
        return page(request, "Home", f"{hero}<h2>Featured</h2><div class='grid'>{cards}</div>")

    @app.get("/products", response_class=HTMLResponse)
    async def products(request: Request) -> HTMLResponse:
        cards = "".join(
            f'<div class="card">{img(i)}<div class="card-body"><a href="/products/{i}">{n}</a><span class="price">${p:.2f}</span></div></div>' for i, (n, p, _) in PRODUCTS.items()
        )
        specs = ""
        if on("mobile_overflow"):  # a fixed-width table: fine on desktop, breaks a phone viewport
            rows = "".join(f"<tr><td>{n}</td><td>${p:.2f}</td><td>In stock</td><td>Ships in 2 days</td></tr>" for n, p, _ in PRODUCTS.values())
            specs = f'<h2>Compare</h2><table style="width:900px;min-width:900px"><tr><th>Name</th><th>Price</th><th>Stock</th><th>Shipping</th></tr>{rows}</table>'
        return page(request, "Products", f"<h1>Products</h1><div class='grid'>{cards}</div>{specs}")

    @app.get("/products/{pid}", response_class=HTMLResponse)
    async def product(request: Request, pid: int) -> HTMLResponse:
        if pid not in PRODUCTS:
            return page(request, "Not found", "<h1>Product not found</h1>", 404)
        name, price, _ = PRODUCTS[pid]
        qty_min = "" if on("negative_qty") else ' min="1"'
        body = f"""<div class="product"><div class="shot">{img(pid)}</div><div><h1>{name}</h1><p class="price">${price:.2f}</p>
<form action="/cart/add" method="post"><input type="hidden" name="pid" value="{pid}">
<label for="qty">Quantity</label> <input id="qty" type="number" name="qty" value="1"{qty_min} required>
<button type="submit" class="primary">Add to cart</button></form>
<p class="more"><button type="button" onclick="this.classList.toggle('on')">Like</button>
<button type="button" id="show-reviews" onclick="showReviews()">Show reviews</button></p><ul id="reviews"></ul></div></div>
<script>
function showReviews() {{
  fetch('/api/products/{pid}/reviews').then((r) => r.json()).then((data) => {{
    document.getElementById('reviews').innerHTML = data.reviews.map((x) => '<li>' + x.author + ': ' + x.text + '</li>').join('');
  }});
}}
</script>"""
        return page(request, name, body)

    @app.get("/cart", response_class=HTMLResponse)
    async def cart(request: Request) -> HTMLResponse:
        s = request.state.s
        if not s["cart"]:
            return page(request, "Cart", "<h1>Your cart</h1><p class='muted'>Your cart is empty.</p><a href='/products' class='btn'>Browse products</a>")
        rows, total = "", 0.0
        for pid, qty in s["cart"].items():
            name, price, _ = PRODUCTS[pid]
            total += price * qty
            rows += f'<tr><td>{name}</td><td>{qty}</td><td>${price * qty:.2f}</td><td><form action="/cart/remove" method="post"><input type="hidden" name="pid" value="{pid}"><button type="submit">Remove</button></form></td></tr>'
        apply = "" if on("dead_coupon") else ' onclick="applyCoupon()"'
        body = f"""<h1>Your cart</h1><table><tr><th>Item</th><th>Qty</th><th>Line total</th><th></th></tr>{rows}</table>
<p class="sum">Total: <strong id="total">${total:.2f}</strong></p>
<p><label for="coupon">Coupon code</label> <input id="coupon" name="coupon"> <button type="button"{apply}>Apply coupon</button> <span id="coupon-status" class="muted"></span></p>
<a href="/checkout" class="btn primary">Checkout</a>
<script>function applyCoupon() {{ document.getElementById('coupon-status').textContent = 'Coupon not recognised'; }}</script>"""
        return page(request, "Cart", body)

    @app.post("/cart/add")
    async def cart_add(request: Request, pid: int = Form(...), qty: int = Form(...)) -> Response:
        if pid not in PRODUCTS or (qty < 1 and not on("negative_qty")) or qty > 10_000_000:
            return page(request, "Invalid", "<h1>Invalid quantity</h1><a href='/products'>Back</a>", 400)
        s = request.state.s
        s["cart"][pid] = s["cart"].get(pid, 0) + qty
        s["badge"] += qty
        return RedirectResponse("/cart", status_code=303)

    @app.post("/cart/remove")
    async def cart_remove(request: Request, pid: int = Form(...)) -> Response:
        request.state.s["cart"].pop(pid, None)  # with `stale_badge`, the nav counter is never decremented
        return RedirectResponse("/cart", status_code=303)

    @app.get("/checkout", response_class=HTMLResponse)
    async def checkout(request: Request) -> HTMLResponse:
        if not request.state.s["cart"]:
            return page(request, "Checkout", "<h1>Checkout</h1><p class='muted'>Your cart is empty.</p><a href='/products' class='btn'>Browse products</a>")
        body = """<h1>Checkout</h1><form id="order" class="panel narrow" onsubmit="return placeOrder(event)">
<p><label for="name">Full name</label> <input id="name" name="name" required></p>
<p><label for="email">Email</label> <input id="email" type="email" name="email" required></p>
<p><label for="address">Address</label> <input id="address" name="address" required></p>
<button type="submit" class="primary">Place order</button> <span id="order-status" class="err"></span></form>
<script>
function placeOrder(e) {
  e.preventDefault();
  const f = e.target;
  fetch('/api/orders', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: f.name.value, email: f.email.value, address: f.address.value})})
    .then((r) => r.ok ? r.json().then((o) => { location.href = '/orders/' + o.id; })
                      : (document.getElementById('order-status').textContent = 'Something went wrong. Please try again.'));
  return false;
}
</script>"""
        return page(request, "Checkout", body)

    @app.get("/orders", response_class=HTMLResponse)
    async def orders(request: Request) -> HTMLResponse:
        items = "".join(f'<li><a href="/orders/{o}">Order #{o}</a></li>' for o in request.state.s["orders"]) or "<li>No orders yet.</li>"
        return page(request, "Orders", f"<h1>Your orders</h1><ul>{items}</ul>")

    @app.get("/orders/{oid}", response_class=HTMLResponse)
    async def order(request: Request, oid: int) -> HTMLResponse:
        if oid not in request.state.s["orders"]:
            return page(request, "Not found", "<h1>Order not found</h1>", 404)
        return page(request, "Order placed", f"<h1>Order placed</h1><p>Thank you. Your order number is #{oid}.</p><a href='/orders'>All orders</a>")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, error: str = "") -> HTMLResponse:
        msg = '<p class="err">Wrong username or password.</p>' if error else ""
        body = f"""<h1>Sign in</h1>{msg}<form action="/login" method="post" class="panel narrow">
<p><label for="username">Username</label> <input id="username" name="username" required></p>
<p><label for="password">Password</label> <input id="password" type="password" name="password" required></p>
<button type="submit" class="primary">Sign in</button></form>"""
        return page(request, "Sign in", body, 401 if error else 200)

    @app.post("/login")
    async def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
        if username == USER and password == PASSWORD:
            request.state.s["user"] = username
            return RedirectResponse("/account", status_code=303)
        return await login_form(request, error="1")  # 401: correct behaviour, not a bug

    @app.get("/account", response_class=HTMLResponse)
    async def account(request: Request) -> Response:
        user = request.state.s["user"]
        if not user:
            return RedirectResponse("/login", status_code=303)
        body = f"""<h1>Account</h1><p>Signed in as <strong>{user}</strong>.</p><h2>Preferences</h2><p id="prefs">Loading...</p>
<script>
fetch('/api/prefs').then((r) => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
  .then((p) => {{ document.getElementById('prefs').textContent = 'Theme: ' + p.theme; }})
  .catch((e) => console.error('Failed to load preferences: ' + e.message));
</script>"""
        return page(request, "Account", body)

    @app.get("/search", response_class=HTMLResponse)
    async def search(request: Request, q: str = "") -> HTMLResponse:
        if on("search_500") and any(c in q for c in "%'\""):
            raise RuntimeError("unescaped search term")  # -> 500
        hits = [(i, n) for i, (n, _, _) in PRODUCTS.items() if q and q.lower() in n.lower()]
        items = "".join(f'<li><a href="/products/{i}">{n}</a></li>' for i, n in hits) or "<li>No products match.</li>"
        return page(request, "Search", f"<h1>Search results</h1><ul>{items}</ul>")

    @app.get("/about", response_class=HTMLResponse)
    async def about(request: Request) -> HTMLResponse:
        return page(request, "About", "<h1>About us</h1><p>We sell a few well-made things. Questions? Write to hello@mugandco.test.</p>")

    @app.get("/shipping", response_class=HTMLResponse)
    async def shipping(request: Request) -> HTMLResponse:
        if on("dead_link"):
            return page(request, "Not found", "<h1>Page not found</h1>", 404)
        return page(request, "Shipping policy", "<h1>Shipping policy</h1><p>Orders ship within two working days.</p>")

    # ---------------------------------------------------------------- api

    @app.get("/api/products/{pid}/reviews")
    async def reviews(pid: int) -> JSONResponse:
        if pid == 3 and on("reviews_crash"):
            return JSONResponse({"reviews": None})  # the page calls .map() on this
        return JSONResponse({"reviews": [{"author": "Sam", "text": "Does the job."}, {"author": "Ira", "text": "Would buy again."}]})

    @app.post("/api/newsletter")
    async def newsletter(request: Request) -> JSONResponse:
        email = str((await request.json()).get("email", ""))
        ok = "@" in email and "." in email.rsplit("@", 1)[-1]
        return JSONResponse({"ok": ok}, status_code=200 if ok else 422)  # 422 on bad input: correct behaviour

    @app.get("/api/prefs")
    async def prefs(request: Request) -> JSONResponse:
        if on("prefs_error"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"theme": "light"})

    @app.post("/api/orders")
    async def create_order(request: Request) -> JSONResponse:
        s = request.state.s
        data = await request.json()
        if not s["cart"]:
            return JSONResponse({"error": "cart is empty"}, status_code=400)
        if not all(str(data.get(k, "")).strip() for k in ("name", "email", "address")):
            return JSONResponse({"error": "missing fields"}, status_code=422)
        if on("checkout_500") and any(q >= 2 for q in s["cart"].values()):
            raise RuntimeError("amount must be an integer number of cents")  # -> 500
        oid = 1000 + len(s["orders"]) + 1
        s["orders"].append(oid)
        s["cart"].clear()
        s["badge"] = 0
        return JSONResponse({"id": oid}, status_code=201)

    @app.get("/static/img/{name}")
    async def image(name: str) -> Response:
        slugs = {slug for _, _, slug in PRODUCTS.values()}
        if name.removesuffix(".svg") not in slugs:
            return Response(status_code=404)
        return Response(art(name.removesuffix(".svg")), media_type="image/svg+xml")

    return app


def parse_bugs(spec: str) -> set[str]:
    if spec == "all":
        return set(BUGS)
    if spec in ("none", ""):
        return set()
    return {b.strip() for b in spec.split(",") if b.strip() in BUGS}


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--bugs", default="all", help="all | none | comma list of: " + ", ".join(BUGS))
    ap.add_argument("--latency-ms", type=int, default=0)
    a = ap.parse_args(argv)
    uvicorn.run(create_shop(parse_bugs(a.bugs), a.latency_ms), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()

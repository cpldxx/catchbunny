"""A small app for the things the demo shop does not have: uploads, drag and drop,
iframes, closed shadow roots, and data an account keeps between sign-ins.

Each page plants one defect behind the feature it exercises, so a test can say
"the tester reached this" by asking whether the defect was found.

    python tests/lab_app.py --port 8821
"""

from __future__ import annotations

import argparse
import secrets
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

USER, PASSWORD = "demo", "demo-pass"


def page(title: str, body: str) -> HTMLResponse:
    nav = '<nav><a href="/">Home</a> <a href="/upload">Upload</a> <a href="/board">Board</a> <a href="/embed">Embed</a> <a href="/widget">Widget</a> <a href="/notes">Notes</a></nav>'
    return HTMLResponse(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} - Lab</title></head><body><header>{nav}</header><main>{body}</main></body></html>')


def create_lab() -> FastAPI:
    app = FastAPI()
    sessions: dict[str, str] = {}  # session id -> user
    notes: dict[str, list[str]] = {}  # user -> notes: kept on the account, not on the session
    app.state.resets = 0

    def user_of(request: Request) -> str | None:
        return sessions.get(request.cookies.get("sid", ""))

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return page("Home", "<h1>Lab</h1><p>One feature per page.</p>")

    # ---------------------------------------------------------------- uploads

    @app.get("/upload", response_class=HTMLResponse)
    async def upload_form() -> HTMLResponse:
        # The input is hidden behind a styled label, as upload controls usually are.
        return page("Upload", """<h1>Upload a receipt</h1>
<form action="/upload" method="post" enctype="multipart/form-data">
<label for="receipt" style="display:inline-block;padding:8px 12px;border:1px solid #888">Choose a receipt</label>
<input id="receipt" type="file" name="receipt" accept="image/*" style="position:absolute;width:1px;height:1px;opacity:0">
<button type="submit">Send receipt</button></form>""")

    @app.post("/upload")
    async def upload(receipt: UploadFile | None = File(default=None)) -> Response:
        if receipt is None or not receipt.filename:
            return page("Upload", "<h1>Upload a receipt</h1><p>Choose a file first.</p><a href='/upload'>Back</a>")
        data = await receipt.read()
        if "<" in receipt.filename:
            raise RuntimeError("unescaped file name")  # planted: markup in the file name -> 500
        return page("Uploaded", f"<h1>Receipt received</h1><p>{len(data)} bytes.</p>")

    # ---------------------------------------------------------------- drag and drop

    @app.get("/board", response_class=HTMLResponse)
    async def board() -> HTMLResponse:
        return page("Board", """<h1>Board</h1>
<div style="display:flex;gap:24px">
<section id="todo" style="min-width:220px;min-height:160px;border:1px solid #888;padding:8px"><h2>To do</h2>
<div class="card" id="card-1" draggable="true" style="padding:8px;border:1px solid #444;margin:4px 0">Write the report</div>
<div class="card" id="card-2" draggable="true" style="padding:8px;border:1px solid #444;margin:4px 0">Book the room</div></section>
<section id="done" style="min-width:220px;min-height:160px;border:1px solid #888;padding:8px"><h2>Done</h2></section>
</div>
<script>
let dragged = null;
for (const card of document.querySelectorAll('.card')) card.addEventListener('dragstart', (e) => { dragged = card; e.dataTransfer.setData('text/plain', card.id); });
for (const column of document.querySelectorAll('section')) {
  column.addEventListener('dragover', (e) => e.preventDefault());
  column.addEventListener('drop', (e) => {
    e.preventDefault();
    column.appendChild(dragged);
    fetch('/api/cards/' + dragged.id + '/move', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to: column.id }) });
  });
}
</script>""")

    @app.post("/api/cards/{card}/move")
    async def move(card: str, request: Request) -> JSONResponse:
        if (await request.json()).get("to") == "done":
            raise RuntimeError("no such status")  # planted: moving a card to Done -> 500
        return JSONResponse({"ok": True})

    # ---------------------------------------------------------------- iframes

    @app.get("/embed", response_class=HTMLResponse)
    async def embed(request: Request) -> HTMLResponse:
        # The same server under another host name is another origin: somebody else's frame, as far as a browser cares.
        port = request.url.port
        other = f"http://localhost:{port}" if request.url.hostname == "127.0.0.1" else f"http://127.0.0.1:{port}"
        return page("Embed", f"""<h1>Settings</h1>
<iframe name="settings" src="/embed/inner" width="420" height="160" title="Settings form"></iframe>
<iframe src="{other}/embed/theirs" width="420" height="120" title="A widget from elsewhere"></iframe>""")

    @app.get("/embed/inner", response_class=HTMLResponse)
    async def inner() -> HTMLResponse:
        return HTMLResponse("""<!doctype html><html lang="en"><body><h2>Notifications</h2>
<button type="button" onclick="fetch('/api/settings', {method: 'POST'})">Save settings</button></body></html>""")

    @app.get("/embed/theirs", response_class=HTMLResponse)
    async def theirs() -> HTMLResponse:
        return HTMLResponse("""<!doctype html><html lang="en"><body>
<button type="button" onclick="fetch('/api/settings', {method: 'POST'})">Pay with Elsewhere</button></body></html>""")

    @app.post("/api/settings")
    async def settings() -> JSONResponse:
        raise RuntimeError("settings store is read-only")  # planted: saving from the frame -> 500

    # ---------------------------------------------------------------- closed shadow root

    @app.get("/widget", response_class=HTMLResponse)
    async def widget() -> HTMLResponse:
        return page("Widget", """<h1>Rating</h1><star-rating></star-rating>
<script>
customElements.define('star-rating', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'closed' });
    root.innerHTML = '<button id="rate">Rate five stars</button><span id="out"></span>';
    root.getElementById('rate').onclick = () => { root.getElementById('out').textContent = this.ratings.five; };  // planted: `ratings` is undefined
  }
});
</script>""")

    # ---------------------------------------------------------------- data an account keeps

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> HTMLResponse:
        return page("Sign in", """<h1>Sign in</h1><form action="/login" method="post">
<p><label for="username">Username</label> <input id="username" name="username" required></p>
<p><label for="password">Password</label> <input id="password" type="password" name="password" required></p>
<button type="submit">Sign in</button></form>""")

    @app.post("/login")
    async def login(username: str = Form(...), password: str = Form(...)) -> Response:
        if username != USER or password != PASSWORD:
            return RedirectResponse("/login", status_code=303)
        sid = secrets.token_hex(8)
        sessions[sid] = username
        response = RedirectResponse("/notes", status_code=303)
        response.set_cookie("sid", sid, httponly=True)
        return response

    @app.get("/notes", response_class=HTMLResponse)
    async def notes_page(request: Request) -> Response:
        user = user_of(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        mine = notes.setdefault(user, [])
        if any('"' in note for note in mine):
            raise RuntimeError("unescaped note")  # planted: one note with a quote in it breaks the page for this account from then on -> 500
        items = "".join(f"<li>{n}</li>" for n in mine) or "<li>No notes yet.</li>"
        return page("Notes", f"""<h1>Notes</h1><ul>{items}</ul>
<form action="/notes" method="post"><label for="note">New note</label> <input id="note" name="note" required>
<button type="submit">Add note</button></form>""")

    @app.post("/notes")
    async def add_note(request: Request, note: str = Form(...)) -> Response:
        user = user_of(request)
        if user:
            notes.setdefault(user, []).append(note[:40])
        return RedirectResponse("/notes", status_code=303)

    @app.post("/api/test/reset")
    async def reset() -> JSONResponse:
        notes.clear()
        app.state.resets += 1
        return JSONResponse({"ok": True})

    @app.get("/api/test/state")
    async def state() -> dict[str, Any]:
        return {"resets": app.state.resets, "notes": {user: len(items) for user, items in notes.items()}}

    return app


if __name__ == "__main__":
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8821)
    uvicorn.run(create_lab(), host="127.0.0.1", port=ap.parse_args().port, log_level="warning")

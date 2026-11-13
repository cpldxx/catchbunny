"""Page -> Observation.

One `page.evaluate` round trip distils the live DOM into what a tester needs:
the interactive elements (each tagged with an ephemeral `data-gx` ref and a
durable signature) - in the light DOM and in every shadow root - plus
headings, visible text, and the page-level defects that can
be detected without any judgement (unlabelled inputs, broken images, horizontal
overflow). A text outline of ~1-2k tokens replaces a screenshot for the model,
which is both cheaper and something a deterministic brain can read too.

The same snapshot runs in each of the app's own iframes; `merge` folds those
into the page's, so a control in a frame is a control like any other, found
again by the frame it lives in (`ElementSig.frame`).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, Field

from ..domain import ElementSig, norm_text, path_pattern, sha, url_path

SNAPSHOT_JS = r"""
(prefix) => {
  const MAX = 160;
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  // Shadow roots are part of the page: web-component apps keep most of their controls
  // there. (A root the page asked to be closed was opened by the tracker, see below.)
  const roots = [document];
  for (let i = 0; i < roots.length; i++) {
    for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
  }
  const all = (selector) => roots.flatMap((root) => Array.from(root.querySelectorAll(selector)));
  const CHROME = 'header,nav,footer,[role=navigation],[role=banner],[role=contentinfo]';
  const inChrome = (el) => {
    for (let node = el; node; node = node.getRootNode().host) if (node.closest(CHROME)) return true;
    return false;
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'hidden') return 'hidden';
      if (t === 'number') return 'spinbutton';
      if (t === 'search') return 'searchbox';
      if (t === 'range') return 'slider';
      if (t === 'file') return 'file';
      return 'textbox';
    }
    // Something that can only be dragged is not a button: clicking it is not expected to do anything.
    if (el.getAttribute('draggable') === 'true' && !el.hasAttribute('onclick') && !el.hasAttribute('tabindex')) return 'draggable';
    return 'clickable';
  };
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const scope = el.getRootNode();
      const t = clean(by.split(/\s+/).map((id) => ((scope.getElementById ? scope.getElementById(id) : null) || {}).textContent || '').join(' '));
      if (t) return t;
    }
    if (el.labels && el.labels.length) return clean(el.labels[0].textContent);
    return '';
  };
  const nameOf = (el) => {
    const label = labelOf(el);
    if (label) return label.slice(0, 80);
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      const t = (el.getAttribute('type') || '').toLowerCase();
      if (['submit', 'button', 'reset'].includes(t)) return clean(el.value) || t;
      if (t === 'file') return clean(el.getAttribute('title') || '').slice(0, 80) || 'Choose a file';
      return clean(el.getAttribute('placeholder') || el.getAttribute('title') || '').slice(0, 80);
    }
    const txt = clean(el.innerText || el.textContent);
    if (txt) return txt.slice(0, 80);
    const img = el.querySelector('img[alt]');
    if (img && clean(img.alt)) return clean(img.alt).slice(0, 80);
    return clean(el.getAttribute('title') || '').slice(0, 80);
  };
  const cssPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && parts.length < 7) {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      let sel = cur.tagName.toLowerCase();
      const sibs = cur.parentElement ? Array.from(cur.parentElement.children).filter((c) => c.tagName === cur.tagName) : [];
      if (sibs.length > 1) sel += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      parts.unshift(sel);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const nearOf = (el) => {
    let cur = el.parentElement;
    for (let hops = 0; cur && hops < 3; hops++, cur = cur.parentElement) {
      const h = cur.querySelector('h1,h2,h3,h4,legend,th');
      if (h && !h.contains(el) && clean(h.textContent)) return clean(h.textContent).slice(0, 60);
    }
    return '';
  };

  all('[data-gx]').forEach((e) => e.removeAttribute('data-gx'));
  const forms = new Map();
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=switch],[onclick],[tabindex]:not([tabindex="-1"]),[draggable=true]';
  // Where something can be dropped: an `ondrop` attribute, or a drop listener the tracker saw being added.
  const tracked = window.__gx && window.__gx.drops ? window.__gx.drops.filter((el) => el.isConnected) : [];
  const zones = new Set([...all('[ondrop]'), ...tracked].filter(vis));
  const zoneOf = (el) => {
    for (let node = el.parentElement; node; node = node.parentElement) if (zones.has(node)) return node;
    return null;
  };
  // A file input is usually hidden behind a styled label or button; it counts when what stands in for it is shown.
  const offered = (el) => vis(el) || Array.from(el.labels || []).some(vis) || (el.parentElement && vis(el.parentElement));
  const elements = [];
  const a11y = [];
  let n = 0;
  let truncated = false;
  for (const el of new Set([...all(SEL), ...zones])) {
    const role = zones.has(el) && !el.matches(SEL) ? 'dropzone' : roleOf(el);
    if (role === 'hidden' || !(role === 'file' ? offered(el) : vis(el))) continue;
    if (n >= MAX) { truncated = true; break; }
    const tag = el.tagName.toLowerCase();
    const name = nameOf(el);
    const attrs = {};
    for (const k of ['id', 'name', 'type', 'placeholder', 'href', 'data-testid', 'min', 'max', 'maxlength', 'pattern', 'accept']) {
      const v = el.getAttribute(k);
      if (v) attrs[k] = String(v).slice(0, 160);
    }
    const form = el.closest('form');
    if (form) {
      if (!forms.has(form)) forms.set(form, 'f' + (forms.size + 1));
      attrs.form = forms.get(form);
      attrs.form_method = (form.getAttribute('method') || 'get').toLowerCase();
      if (form.querySelector(':invalid')) attrs.form_invalid = '1';
    }
    if (el.required) attrs.required = '1';
    if (inChrome(el)) attrs.chrome = '1';
    if (el.getAttribute('draggable') === 'true') {
      attrs.draggable = '1';
      const zone = zoneOf(el);
      if (zone) attrs.in_zone = cssPath(zone);
    }
    if (zones.has(el)) attrs.dropzone = '1';
    if (tag === 'select') attrs.options = Array.from(el.options).slice(0, 12).map((o) => o.value).join('|');
    const ref = prefix + (++n);
    el.setAttribute('data-gx', ref);
    const isField = tag === 'input' || tag === 'textarea' || tag === 'select';
    elements.push({
      ref, role, name, tag, attrs,
      text: clean(el.innerText || '').slice(0, 80),
      near: nearOf(el),
      css: cssPath(el),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      value: isField && attrs.type !== 'password' ? String(el.value || '').slice(0, 80) : '',
      checked: el.checked === true,
    });
    if (!name && ['textbox', 'searchbox', 'spinbutton', 'combobox', 'checkbox', 'radio'].includes(role)) {
      a11y.push({ rule: 'input-missing-label', element: cssPath(el), html: el.outerHTML.slice(0, 160) });
    } else if (!name && (role === 'button' || role === 'link')) {
      a11y.push({ rule: role + '-missing-name', element: cssPath(el), html: el.outerHTML.slice(0, 160) });
    }
  }
  const brokenImages = [];
  for (const img of document.images) {
    if (!img.getAttribute('src')) continue;
    if (img.complete && img.naturalWidth === 0) brokenImages.push(img.currentSrc || img.src);
    if (!img.hasAttribute('alt') && vis(img)) a11y.push({ rule: 'img-missing-alt', element: cssPath(img), html: img.outerHTML.slice(0, 160) });
  }
  const headings = all('h1,h2,h3').filter(vis).slice(0, 12)
    .map((h) => h.tagName.toLowerCase() + ': ' + clean(h.textContent).slice(0, 80));
  const root = document.querySelector('main') || document.body;
  const se = document.scrollingElement || document.documentElement;
  const shadowText = roots.slice(1).map((r) => Array.from(r.children).map((c) => c.innerText || '').join(' ')).join(' ');
  const html = (document.body ? document.body.innerHTML : '') + roots.slice(1).map((r) => r.innerHTML).join('\u0000');
  let h = 0;
  for (let i = 0; i < html.length; i++) h = (h * 31 + html.charCodeAt(i)) | 0;
  return {
    url: location.href, title: document.title, elements, headings, a11y, brokenImages, truncated,
    dom: html.length + ':' + h, fx: window.__gx ? window.__gx.fx : 0, calls: window.__gx ? window.__gx.calls : -1,
    text: clean((root ? root.innerText : '') + ' ' + shadowText).slice(0, 1600),
    overflowX: Math.max(0, se.scrollWidth - se.clientWidth),
    viewport: [window.innerWidth, window.innerHeight],
  };
}
"""

# Installed before any page script runs. The page keeps its own books, so the
# tester never has to infer what happened from events that arrive late:
#   fx  - side effects that leave no trace in the DOM (clipboard, storage, history,
#         print...), so "this button did nothing" is not concluded from the DOM alone
#   net - fetch/XHR calls in flight, counted synchronously at the call site
#   calls - how many such calls the page has ever started (did this click do anything?)
#   nav - when a document navigation was initiated (beforeunload)
PAGE_TRACKER_JS = r"""
(() => {
  if (window.__gx) return;
  const g = { fx: 0, net: 0, nav: 0, calls: 0 };
  try { Object.defineProperty(window, '__gx', { value: g }); } catch (e) { return; }
  const wrap = (obj, name) => {
    try {
      const orig = obj && obj[name];
      if (typeof orig !== 'function') return;
      obj[name] = function (...args) { g.fx++; return orig.apply(this, args); };
    } catch (e) {}
  };
  if (navigator.clipboard) { wrap(navigator.clipboard, 'writeText'); wrap(navigator.clipboard, 'write'); }
  for (const name of ['print', 'open', 'scrollTo', 'postMessage']) wrap(window, name);
  for (const name of ['pushState', 'replaceState']) wrap(history, name);
  for (const name of ['setItem', 'removeItem', 'clear']) wrap(Storage.prototype, name);
  wrap(document, 'execCommand');
  for (const name of ['scrollIntoView', 'requestFullscreen']) wrap(Element.prototype, name);
  for (const name of ['play', 'pause']) wrap(HTMLMediaElement.prototype, name);
  for (const name of ['showModal', 'show', 'close']) wrap(HTMLDialogElement.prototype, name);

  const done = () => { g.net = Math.max(0, g.net - 1); };
  const nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function (...args) {
      g.net++; g.calls++;
      let promise;
      try { promise = nativeFetch.apply(this, args); } catch (e) { done(); throw e; }
      promise.then(done, done);
      return promise;
    };
  }
  const nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    g.net++; g.calls++;
    this.addEventListener('loadend', done, { once: true });
    return nativeSend.apply(this, args);
  };
  for (const [obj, name] of [[navigator, 'sendBeacon'], [WebSocket.prototype, 'send']]) {
    try {
      const orig = obj[name];
      obj[name] = function (...args) { g.calls++; return orig.apply(this, args); };
    } catch (e) {}
  }
  window.addEventListener('beforeunload', () => { g.nav = performance.now(); });

  // Where things can be dropped. Drag and drop has no markup of its own: a drop target is whatever
  // had a drop listener added, so the tester has to be told which elements those are.
  g.drops = [];
  const nativeListen = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, ...rest) {
    if (type === 'drop' && this instanceof Element && !g.drops.includes(this)) g.drops.push(this);
    return nativeListen.call(this, type, ...rest);
  };

  // A closed shadow root hides its controls from every outside script, a tester's included. The page's
  // own code keeps the root it was handed, so opening it changes nothing the page relies on, and it
  // lets the controls inside be seen and found again.
  const nativeAttach = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init) {
    return nativeAttach.call(this, init && init.mode === 'closed' ? { ...init, mode: 'open' } : init);
  };
})();
"""

# Resolves when the page has finished reacting: nothing in flight for `quiet` ms
# (fetch/XHR, a pending navigation, images still loading) and no DOM mutation for
# `quiet` ms. Pending work is waited for up to `max`; a DOM that never stops
# changing (a ticker) is given `domMax` and then left alone.
PAGE_QUIET_JS = r"""
([quiet, domMax, max]) => new Promise((resolve) => {
  const g = window.__gx || { net: 0, nav: 0 };
  const start = performance.now();
  let lastMutation = start;
  let lastBusy = -Infinity;
  const observer = new MutationObserver(() => { lastMutation = performance.now(); });
  const watch = (root) => {
    observer.observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) watch(el.shadowRoot);
  };
  watch(document);
  const busy = (now) => g.net > 0 || (g.nav > 0 && now - g.nav < 3000)
    || Array.from(document.images).some((img) => !img.complete && img.loading !== 'lazy');
  const tick = () => {
    const now = performance.now();
    if (busy(now)) lastBusy = now;
    const calm = now - lastBusy >= quiet;
    const still = now - lastMutation >= quiet || now - start >= domMax;
    if ((calm && still) || now - start >= max) { observer.disconnect(); resolve(calm && still); }
    else setTimeout(tick, 8);
  };
  setTimeout(tick, Math.min(quiet, 16));
})
"""

FIELD_ROLES = frozenset({"textbox", "searchbox", "spinbutton"})
CLICK_ROLES = frozenset({"link", "button", "tab", "menuitem", "switch", "clickable", "checkbox", "radio", "combobox"})
MAX_FRAMES = 6  # the app's own iframes read per observation; past this a page is a mash-up, not an app


def merge(raw: dict[str, Any], frames: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Fold the snapshots of a page's iframes into the page's own.

    Controls keep the frame they live in; everything that says "did anything change?"
    (the DOM hash, the side-effect and request counters) covers the frames too, so a
    click whose only effect is inside a frame is not read as a click that did nothing.
    """
    if not frames:
        return raw
    out = {**raw, "elements": list(raw.get("elements", [])), "headings": list(raw.get("headings", [])), "a11y": list(raw.get("a11y", [])), "brokenImages": list(raw.get("brokenImages", []))}
    for key, snap in frames:
        out["elements"] += [{**e, "frame": key} for e in snap.get("elements", [])]
        out["headings"] += snap.get("headings", [])
        out["a11y"] += [{**issue, "element": f"{key} {issue.get('element', '')}"} for issue in snap.get("a11y", [])]
        out["brokenImages"] += snap.get("brokenImages", [])
        out["text"] = f"{out.get('text', '')} {snap.get('text', '')}"[:2400]
        out["dom"] = f"{out.get('dom', '')}|{snap.get('dom', '')}"
        out["truncated"] = bool(out.get("truncated")) or bool(snap.get("truncated"))
        for counter in ("fx", "calls"):
            if snap.get(counter, -1) >= 0 and out.get(counter, -1) >= 0:
                out[counter] += snap[counter]
    return out


class Element(ElementSig):
    """An element as seen in one observation: durable signature + live state."""

    disabled: bool = False
    value: str = ""
    checked: bool = False

    def sig(self) -> ElementSig:
        return ElementSig(**self.model_dump(include=set(ElementSig.model_fields)))


class Observation(BaseModel):
    url: str
    title: str = ""
    elements: list[Element] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    text: str = ""
    a11y: list[dict[str, Any]] = Field(default_factory=list)
    broken_images: list[str] = Field(default_factory=list)
    overflow_x: int = 0
    viewport: tuple[int, int] = (0, 0)
    truncated: bool = False
    dom: str = ""  # cheap hash of the whole DOM: equal before/after an action = nothing happened
    fx: int = 0  # count of DOM-invisible side effects so far (clipboard, storage, history, print...)
    calls: int = -1  # fetch/XHR/beacon/socket sends the page has started so far; -1 = page tracker absent
    # identities, computed once in `from_raw`
    state_id: str = ""  # this screen (concrete path + its controls)
    template_id: str = ""  # this *kind* of screen (/products/# and its controls)
    digest: str = ""  # state + text + field values: "did anything change?"
    structure: str = ""  # state + text, ignoring field values

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Observation:
        obs = cls(
            url=raw["url"],
            title=raw.get("title", ""),
            elements=[Element(**e) for e in raw.get("elements", [])],
            headings=raw.get("headings", []),
            text=raw.get("text", ""),
            a11y=raw.get("a11y", []),
            broken_images=raw.get("brokenImages", []),
            overflow_x=int(raw.get("overflowX", 0)),
            viewport=tuple(raw.get("viewport", (0, 0))),
            truncated=bool(raw.get("truncated", False)),
            dom=str(raw.get("dom", "")),
            fx=int(raw.get("fx", 0)),
            calls=int(raw.get("calls", -1)),
        )
        # Names are digit-normalised so "Cart (2)" and "Cart (3)" are one state;
        # query *values* are dropped so /search?q=a and ?q=b are one state.
        controls = "\n".join(sorted({e.key() for e in obs.elements}))
        query = ",".join(sorted(k for k, _ in parse_qsl(urlsplit(obs.url).query, keep_blank_values=True)))
        obs.state_id = sha(f"{url_path(obs.url)}?{query}\n{controls}", 10)
        obs.template_id = sha(f"{path_pattern(obs.url)}\n{controls}", 10)
        obs.structure = sha(f"{obs.state_id}\n{obs.title}\n{obs.text}", 10)
        values = "\n".join(f"{e.value}|{e.checked}|{e.disabled}" for e in obs.elements)
        obs.digest = sha(f"{obs.structure}\n{values}", 10)
        return obs

    def find(self, ref: str) -> Element | None:
        for e in self.elements:
            if e.ref == ref:
                return e
        return None

    def form_fields(self, form: str) -> list[Element]:
        return [e for e in self.elements if e.attrs.get("form") == form and (e.role in FIELD_ROLES or e.role == "combobox")]

    def form_uploads(self, form: str) -> list[Element]:
        return [e for e in self.elements if e.attrs.get("form") == form and e.role == "file"]

    def has(self, role: str, name: str) -> Element | None:
        want = norm_text(name)
        if not want:
            return None  # a check that names nothing must not match the first control on the page
        for e in self.elements:
            if (not role or e.role == role) and want in norm_text(e.name):
                return e
        return None

    def render(self, max_elements: int = 90, max_text: int = 900) -> str:
        """Compact outline for a model: one line per control, addressable by ref."""
        lines = [f"URL: {self.url}", f"TITLE: {self.title}"]
        lines += [h.upper()[:2] + h[2:] for h in self.headings[:8]]
        for e in self.elements[:max_elements]:
            line = f'[{e.ref}] {e.role} "{e.name}"'
            if e.role in FIELD_ROLES or e.role == "combobox":
                line += f' value="{e.value}"'
                if e.attrs.get("type") == "password":
                    line += " (password)"
                if e.attrs.get("required"):
                    line += " required"
            if e.role in ("checkbox", "radio"):
                line += " checked" if e.checked else " unchecked"
            if e.role == "link" and e.attrs.get("href"):
                line += f" -> {e.attrs['href'][:60]}"
            if e.role == "file":
                line += f" accepts {e.attrs.get('accept') or 'any file'}"
            if e.attrs.get("draggable"):
                line += " draggable"
            if e.attrs.get("dropzone"):
                line += " accepts drops"
            if e.frame:
                line += f" (in frame {e.frame})"
            if e.disabled:
                line += " disabled"
            lines.append(line)
        if len(self.elements) > max_elements or self.truncated:
            lines.append(f"... {max(0, len(self.elements) - max_elements)} more controls not shown")
        lines.append(f"TEXT: {self.text[:max_text]}")
        return "\n".join(lines)

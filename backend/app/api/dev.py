# ruff: noqa: E501  (embedded HTML and JavaScript)
"""A throwaway page to try Web Push before the dashboard exists.

Only served while DEV_PUSH_TEST_PAGE=true (default false); otherwise both routes are 404.
It subscribes the browser to push, and creates a test task due in five hours, which sends an
escalation email and a push right away. Sign in at /auth/login first.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.config import get_settings

router = APIRouter(tags=["dev"], include_in_schema=False)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Push test (dev)</title>
<style>body{font-family:Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}
button{font-size:16px;padding:10px 18px;margin:6px 6px 6px 0}pre{background:#f1f3f4;padding:12px}
input{width:100%;padding:8px;font-size:15px}</style></head><body>
<h1>Push test (dev only)</h1>
<p>Sign in first at <a href="/auth/login">/auth/login</a>, then:</p>
<ol>
<li><button id="enable">1. Enable push in this browser</button></li>
<li>Action link for the test task:
<input id="link" value="https://example.com/complete-me"></li>
<li><button id="create">2. Create a test task (due in 5 hours)</button></li>
</ol>
<p>Within about a minute a push notification and an email arrive. Clicking the notification
should open the action link.</p>
<pre id="log"></pre>
<script>
const log = m => { document.getElementById('log').textContent += m + '\\n'; };
const toBytes = s => {
  const pad = '='.repeat((4 - s.length % 4) % 4);
  const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
};
document.getElementById('enable').onclick = async () => {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return log('Push is not supported here.');
    const reg = await navigator.serviceWorker.register('/dev/sw.js', {scope: '/dev/'});
    await navigator.serviceWorker.ready;
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return log('Notification permission: ' + perm);
    const key = await (await fetch('/push/public-key')).json();
    const sub = (await reg.pushManager.getSubscription()) ||
      (await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: toBytes(key.public_key)}));
    const res = await fetch('/push/subscribe', {method: 'POST',
      headers: {'content-type': 'application/json'}, body: JSON.stringify(sub.toJSON())});
    log('Subscribe: HTTP ' + res.status + (res.status === 401 ? ' (sign in first)' : ''));
  } catch (e) { log('Error: ' + e); }
};
document.getElementById('create').onclick = async () => {
  const deadline = new Date(Date.now() + 5 * 3600 * 1000).toISOString();
  const res = await fetch('/tasks', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({title: 'Module 9 test task', deadline, link: document.getElementById('link').value})});
  const body = await res.json();
  log('Create task: HTTP ' + res.status + (body.id ? ' (task ' + body.id + ')' : ' ' + JSON.stringify(body)));
};
</script></body></html>
"""

SERVICE_WORKER = """self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(self.registration.showNotification(data.title || 'Reminder', {
    body: data.body || '', tag: data.tag, data: {url: data.url},
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url;
  if (url && /^https?:/i.test(url)) event.waitUntil(clients.openWindow(url));
});
"""


def _require_enabled() -> None:
    if not get_settings().dev_push_test_page:
        raise HTTPException(404, "Not Found")


@router.get("/dev/push-test", response_class=HTMLResponse)
def push_test_page() -> HTMLResponse:
    _require_enabled()
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@router.get("/dev/sw.js")
def service_worker() -> Response:
    _require_enabled()
    return Response(
        SERVICE_WORKER, media_type="application/javascript", headers={"Cache-Control": "no-store"}
    )

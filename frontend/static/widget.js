/* City Agent Aria — embeddable chat widget loader.
 *
 * Usage (paste once, before </body>):
 *   <script src="https://YOUR-HOST/widget.js" data-key="pk_live_xxx" async></script>
 *
 * Optional attributes:
 *   data-accent="#c2683f"   bubble color
 *   data-title="Ask Aria"   launcher tooltip
 *   data-position="right"   right | left
 *
 * Loads a sandboxed iframe pointing at /embed?key=… on the same origin the
 * script was served from. All chat auth happens inside the iframe via the
 * public embed key — no member login, no cookies, full style isolation.
 */
(function () {
  var me = document.currentScript;
  if (!me) {
    var all = document.getElementsByTagName('script');
    me = all[all.length - 1];
  }
  var KEY = me.getAttribute('data-key') || '';
  var ACCENT = me.getAttribute('data-accent') || '#c2683f';
  var TITLE = me.getAttribute('data-title') || 'Ask Aria';
  var POS = me.getAttribute('data-position') === 'left' ? 'left' : 'right';
  // origin the script was loaded from = where the agent lives
  var ORIGIN = new URL(me.src, location.href).origin;

  if (!KEY) { console.error('[aria-widget] missing data-key'); return; }
  if (document.getElementById('aria-widget-root')) return; // already mounted

  var side = POS + ': 20px';

  var root = document.createElement('div');
  root.id = 'aria-widget-root';
  root.style.cssText = 'position:fixed;bottom:20px;' + side + ';z-index:2147483000;';

  // ---- launcher bubble ----
  var btn = document.createElement('button');
  btn.setAttribute('aria-label', TITLE);
  btn.style.cssText =
    'width:60px;height:60px;border:none;border-radius:50%;cursor:pointer;' +
    'background:' + ACCENT + ';color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.25);' +
    'display:flex;align-items:center;justify-content:center;transition:transform .15s;';
  btn.onmouseenter = function () { btn.style.transform = 'scale(1.06)'; };
  btn.onmouseleave = function () { btn.style.transform = 'scale(1)'; };
  btn.innerHTML =
    '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#fff" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9' +
    'L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5' +
    'a8.48 8.48 0 0 1 8 8v.5z"/></svg>';

  // ---- panel (holds the iframe) ----
  var panel = document.createElement('div');
  panel.style.cssText =
    'position:absolute;bottom:74px;' + side + ';width:390px;height:600px;max-width:calc(100vw - 32px);' +
    'max-height:calc(100vh - 110px);border-radius:16px;overflow:hidden;' +
    'box-shadow:0 12px 48px rgba(0,0,0,.28);background:#fff;display:none;';

  var frame = document.createElement('iframe');
  frame.title = TITLE;
  frame.style.cssText = 'width:100%;height:100%;border:none;';
  frame.setAttribute('allow', 'clipboard-write');
  panel.appendChild(frame);

  var open = false, loaded = false;
  function toggle() {
    open = !open;
    if (open && !loaded) {
      frame.src = ORIGIN + '/embed?key=' + encodeURIComponent(KEY) +
                  '&accent=' + encodeURIComponent(ACCENT);
      loaded = true;
    }
    panel.style.display = open ? 'block' : 'none';
    btn.innerHTML = open
      ? '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" ' +
        'stroke-width="2.4" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>'
      : btn.innerHTML;
  }
  btn.onclick = toggle;

  // let the iframe ask to close itself
  window.addEventListener('message', function (e) {
    if (e.origin === ORIGIN && e.data === 'aria:close' && open) toggle();
  });

  root.appendChild(panel);
  root.appendChild(btn);
  (document.body || document.documentElement).appendChild(root);
})();

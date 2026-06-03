"""
Browser stealth: hides every Playwright/Chromium fingerprint that headless-
detection scripts use. Applied via page.add_init_script() so it runs before
any page JS sees the window object.

Optionally integrates playwright-stealth if installed (more thorough coverage).
"""
from __future__ import annotations

_STEALTH_JS = """
(function () {
    // 1. navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Chrome runtime object
    if (!window.chrome) {
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
    }

    // 3. Permissions API — avoid "notification" permission query fingerprint
    const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origQuery(p);

    // 4. Realistic plugin list
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                { name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer', description: '' },
                { name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client',       filename: 'internal-nacl-plugin', description: '' },
            ];
            arr.item = (i) => arr[i];
            arr.namedItem = (n) => arr.find(p => p.name === n) || null;
            arr.refresh = () => {};
            return arr;
        },
    });

    // 5. Languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

    // 6. Platform & hardware
    Object.defineProperty(navigator, 'platform',        { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory',    { get: () => 8 });
    Object.defineProperty(navigator, 'maxTouchPoints',  { get: () => 0 });

    // 7. WebGL vendor/renderer (common headless giveaway)
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (param) {
        if (param === 37445) return 'Intel Inc.';                     // UNMASKED_VENDOR_WEBGL
        if (param === 37446) return 'Intel Iris OpenGL Engine';       // UNMASKED_RENDERER_WEBGL
        return _getParam.call(this, param);
    };

    // 8. iframe contentWindow.navigator inherits stealth
    const _iframe = HTMLIFrameElement.prototype;
    const _cw = Object.getOwnPropertyDescriptor(_iframe, 'contentWindow');
    if (_cw) {
        Object.defineProperty(_iframe, 'contentWindow', {
            get() {
                const w = _cw.get.call(this);
                if (w) {
                    try { Object.defineProperty(w.navigator, 'webdriver', { get: () => undefined }); } catch {}
                }
                return w;
            },
        });
    }

    // 9. Remove headless-only properties
    delete navigator.__proto__.webdriver;
})();
"""


async def apply_stealth(page) -> None:
    """Inject stealth patches into every page before any JS runs."""
    await page.add_init_script(_STEALTH_JS)

    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        pass  # manual patches above are still effective


async def randomise_viewport(page) -> None:
    """Set a realistic, slightly randomised viewport."""
    import random
    width  = random.choice([1280, 1366, 1440, 1536, 1920])
    height = random.choice([720, 768, 800, 864, 900, 1080])
    await page.set_viewport_size({"width": width, "height": height})


async def human_delay(min_s: float = 0.3, max_s: float = 1.2) -> None:
    """Simulate human think-time between actions."""
    import asyncio, random
    await asyncio.sleep(random.uniform(min_s, max_s))

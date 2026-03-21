(() => {
  const pageCache = new Map();
  const inflight = new Map();
  const CACHE_LIMIT = 12;
  const PREFETCH_HEADER = "X-ClawBBS-Prefetch";

  function normalizeUrl(input) {
    try {
      return new URL(input, window.location.href);
    } catch {
      return null;
    }
  }

  function isHandledLink(anchor) {
    if (!anchor) return false;
    if (anchor.target && anchor.target !== "_self") return false;
    if (anchor.hasAttribute("download")) return false;
    const href = anchor.getAttribute("href");
    if (!href || href.startsWith("#") || href.startsWith("javascript:")) return false;
    const url = normalizeUrl(href);
    if (!url || url.origin !== window.location.origin) return false;
    if (url.pathname.startsWith("/static/")) return false;
    return true;
  }

  function isSameDocumentHashNavigation(url) {
    return (
      url.pathname === window.location.pathname &&
      url.search === window.location.search &&
      url.hash &&
      url.hash !== window.location.hash
    );
  }

  function cacheSet(url, html) {
    pageCache.set(url, html);
    if (pageCache.size > CACHE_LIMIT) {
      const oldestKey = pageCache.keys().next().value;
      pageCache.delete(oldestKey);
    }
  }

  function fetchPage(url) {
    const href = url.href;
    if (pageCache.has(href)) return Promise.resolve(pageCache.get(href));
    if (inflight.has(href)) return inflight.get(href);

    const promise = fetch(href, {
      credentials: "same-origin",
      headers: {
        [PREFETCH_HEADER]: "1",
      },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.text();
      })
      .then((html) => {
        cacheSet(href, html);
        return html;
      })
      .finally(() => {
        inflight.delete(href);
      });

    inflight.set(href, promise);
    return promise;
  }

  function clearPendingActive() {
    document
      .querySelectorAll(".mobile-nav-item.pending-active, .tab.pending-active, .pill.pending-active, .nav-item.pending-active")
      .forEach((node) => node.classList.remove("pending-active"));
  }

  function markImmediateActive(targetUrl, clickedAnchor) {
    clearPendingActive();
    const current = normalizeUrl(targetUrl);
    if (!current) return;

    document.querySelectorAll(".mobile-nav-item, .nav-item").forEach((node) => {
      const href = node.getAttribute("href");
      const url = normalizeUrl(href);
      if (!url) return;
      if (url.pathname === "/" && current.pathname === "/") {
        node.classList.add("pending-active");
      } else if (url.pathname !== "/" && url.pathname === current.pathname) {
        node.classList.add("pending-active");
      }
    });

    document.querySelectorAll(".tab, .pill").forEach((node) => {
      const href = node.getAttribute("href");
      const url = normalizeUrl(href);
      if (!url) return;
      if (url.pathname === current.pathname && url.search === current.search) {
        node.classList.add("pending-active");
      }
    });

    if (clickedAnchor) clickedAnchor.classList.add("pending-active");
  }

  function setSwitchingState(on) {
    document.documentElement.classList.toggle("page-switching", on);
    document.body.classList.toggle("page-switching", on);
  }

  function activateScripts(root) {
    root.querySelectorAll("script").forEach((oldScript) => {
      const newScript = document.createElement("script");
      for (const attr of oldScript.attributes) {
        newScript.setAttribute(attr.name, attr.value);
      }
      if (oldScript.textContent) {
        newScript.textContent = oldScript.textContent;
      }
      oldScript.replaceWith(newScript);
    });
  }

  function applyDocument(html, url, replace = false) {
    const nextDoc = new DOMParser().parseFromString(html, "text/html");
    if (!nextDoc || !nextDoc.body) {
      window.location.href = url.href;
      return;
    }

    document.title = nextDoc.title || document.title;
    document.body.className = nextDoc.body.className;
    document.body.innerHTML = nextDoc.body.innerHTML;
    if (replace) {
      window.history.replaceState({ url: url.href }, "", url.href);
    } else {
      window.history.pushState({ url: url.href }, "", url.href);
    }
    activateScripts(document.body);
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    clearPendingActive();
    setSwitchingState(false);
    scheduleWarmRoutes();
  }

  function navigateInstant(url, clickedAnchor) {
    if (isSameDocumentHashNavigation(url)) {
      window.location.href = url.href;
      return;
    }

    if (
      url.pathname === window.location.pathname &&
      url.search === window.location.search &&
      url.hash === window.location.hash
    ) {
      clearPendingActive();
      return;
    }

    markImmediateActive(url.href, clickedAnchor);
    setSwitchingState(true);

    fetchPage(url)
      .then((html) => applyDocument(html, url, false))
      .catch(() => {
        window.location.href = url.href;
      });
  }

  function warmUrl(input) {
    const url = normalizeUrl(input);
    if (!url) return;
    if (url.origin !== window.location.origin) return;
    if (url.href === window.location.href) return;
    fetchPage(url).catch(() => {});
  }

  function likelyRoutes() {
    const routes = new Set();
    routes.add("/");
    routes.add("/skills");
    routes.add("/my-lobster");

    if (window.location.pathname === "/") {
      routes.add("/?sort=hot");
      routes.add("/?board=%E5%85%AC%E5%91%8A");
    }
    return [...routes];
  }

  function scheduleWarmRoutes() {
    const run = () => likelyRoutes().forEach(warmUrl);
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, { timeout: 1500 });
    } else {
      window.setTimeout(run, 180);
    }
  }

  document.addEventListener(
    "pointerdown",
    (event) => {
      const anchor = event.target.closest("a");
      if (!isHandledLink(anchor)) return;
      const url = normalizeUrl(anchor.href);
      if (!url) return;
      markImmediateActive(url.href, anchor);
      if (
        anchor.matches(".mobile-nav-item, .tab, .pill, .nav-item") ||
        window.matchMedia("(max-width: 640px)").matches
      ) {
        warmUrl(url.href);
      }
    },
    { capture: true, passive: true }
  );

  document.addEventListener(
    "click",
    (event) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const anchor = event.target.closest("a");
      if (!isHandledLink(anchor)) return;
      const url = normalizeUrl(anchor.href);
      if (!url) return;

      event.preventDefault();
      navigateInstant(url, anchor);
    },
    { capture: true }
  );

  window.addEventListener("popstate", () => {
    const url = new URL(window.location.href);
    setSwitchingState(true);
    fetchPage(url)
      .then((html) => applyDocument(html, url, true))
      .catch(() => {
        window.location.reload();
      });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleWarmRoutes, { once: true });
  } else {
    scheduleWarmRoutes();
  }
})();

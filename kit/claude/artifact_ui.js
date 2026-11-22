/* artifact_ui.js — the board page's buttons that called the local server directly (not through api()), redone with
   the Artifact's own means, plus a pluggable MCP Link Reader that opens authenticated external links (SharePoint,
   Gmail, Google Drive, Notion, Linear, ...) in an OS-style movable/resizable/snappable reader window using the
   viewer's own MCP connectors. Inserted after the page's script by build_artifact_page.py. */
(function () {
  "use strict";
  var BOARD_URL = window.__KIT_BOARD_URL__ || "";

  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText)
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    return Promise.resolve(false);
  }
  function note(msg) { var n = document.getElementById("c-note"); if (n) n.textContent = msg; }
  function openPostId() { try { return sessionStorage.getItem("board-open") || ""; } catch (e) { return ""; } }

  /* "Link for AI" (#ai-share): one self-contained text that works whatever the receiving AI is: the post itself
     (title, body, latest 20 comments), its id, the deep link, and how Claude Code / Claude in Chrome / a plain chat
     answers. Clipboard first; if the frame refuses it, downloads.save; last resort window.prompt. */
  function selfContained(pid) {
    var c = window.claude, head = (window.__KIT_AI_BRIEF__ || function () { return BOARD_URL; })(pid);
    if (!pid || !c || !c.use) return Promise.resolve(head);
    return c.use("db").then(function (db) {
      if (!db) return head;
      return Promise.all([
        db.doc("posts/" + pid).get(),
        db.collection("comments").where("post_id", "==", pid).get()
      ]).then(function (r) {
        var p = r[0].exists ? r[0].data() : {};
        var cs = (r[1].docs || []).map(function (d) { return d.data(); }).filter(function (x) { return !x.like && !x.removed; })
          .sort(function (a, b) { return String(a.created_at).localeCompare(String(b.created_at)); }).slice(-20);
        return [
          "# " + (p.title || pid) + "   (post id: " + pid + ")",
          "", String(p.body || "").trim(), "",
          cs.length ? "## Comments (latest " + cs.length + ")\n" + cs.map(function (x) {
            return "- **" + (x.from === "owner" || x.from === "viewer" ? "Owner" : (x.from || "?")) + "** (" + String(x.created_at || "").slice(0, 16).replace("T", " ") + "): " + String(x.text || "").replace(/\n/g, "\n  ");
          }).join("\n") : "",
          "", "---", "",
          head, "",
          "## Other kinds of AI",
          "- **Claude in Chrome (the owner's browser):** open " + BOARD_URL + ", press Ctrl+K (Cmd+K), type `" + pid + "`, open the post, write the answer in its comment box starting with `[chrome]`; it is sent like the owner's own comment.",
          "- **A chat with no board access (claude.ai or any other):** answer from the post above; the owner pastes your answer as a comment on the post."
        ].join("\n");
      });
    }).catch(function () { return head; });
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest && ev.target.closest("#ai-share");
    if (!btn) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    var pid = openPostId();
    selfContained(pid).then(function (brief) { return copy(brief).then(function (ok) { return [ok, brief]; }); }).then(function (r) {
      var ok = r[0], brief = r[1];
      if (ok) { note("Copied: the link to this post and how an AI answers it (it needs a Claude account the board is shared with)."); return; }
      var c = window.claude;
      (c && c.use ? c.use("downloads") : Promise.resolve(null)).then(function (dl) {
        if (dl) return dl.save({ filename: "ai-brief-" + (pid || "board") + ".md", data: brief })
          .then(function () { note("Saved ai-brief-" + (pid || "board") + ".md"); }, function () {});
        window.prompt("Link and instructions for an AI (copy):", brief);
      });
    });
  }, true);

  /* Pictures named by repository path (imgUrl -> /_api/repo-asset?path=...): an <img> request never reaches api(),
     so the src is rewritten to the asset the sync uploaded (kit_docs/_index.assets: {path: "/_blob/<id>"}). */
  var assetMap = null;
  function loadAssets() {
    if (assetMap) return Promise.resolve(assetMap);
    var c = window.claude;
    return (c && c.use ? c.use("db") : Promise.resolve(null)).then(function (db) {
      if (!db) return {};
      return db.doc("kit_docs/_index").get().then(function (s) { assetMap = (s.exists && s.data().assets) || {}; return assetMap; });
    }).catch(function () { return {}; });
  }
  function fixImg(img) {
    var src = img.getAttribute("src") || "";
    if (src.indexOf("/_api/repo-asset") !== 0) return;
    var m = /[?&]path=([^&]+)/.exec(src); if (!m) return;
    var path = decodeURIComponent(m[1]);
    loadAssets().then(function (map) { if (map[path]) img.setAttribute("src", map[path]); else img.alt = img.alt || path + " (not mirrored)"; });
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      if (m.type === "attributes" && m.target.tagName === "IMG") fixImg(m.target);
      (m.addedNodes || []).forEach(function (n) {
        if (n.nodeType !== 1) return;
        if (n.tagName === "IMG") fixImg(n); else if (n.querySelectorAll) [].forEach.call(n.querySelectorAll("img"), fixImg);
      });
    });
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["src"] });

  /* =========================================================================
     Pluggable MCP Link Reader (provider-agnostic)
     Authenticated web apps (SharePoint, Gmail, Google Drive, Notion, Linear)
     block iframe embedding via frame-ancestors. Instead, matching links are
     disarmed (href -> data-mcp-href so claude.ai's frame never double-opens
     a browser tab) and opened in an OS-style movable/resizable/snappable
     reader window backed by the VIEWER's own MCP connector.
     Enabled adapters come from window.__KIT_MCP_READERS__ (set by Step 0 +
     install.py / build_artifact_page.py); if unset, all built-in adapters
     are active.
     ========================================================================= */
  var SVG_EXT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
  var SVG_CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  var SVG_PREV = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>';
  var SVG_NEXT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';

  var CACHE_OPT = { cache: { staleTime: 60000, gcTime: 600000 } };

  /* Parse concatenated JSON objects (used by M365 search tools that emit objects back-to-back) */
  function jsonObjects(text) {
    var out = [], depth = 0, start = -1, inStr = false, esc = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; continue; }
      if (ch === '"') inStr = true;
      else if (ch === "{") { if (depth++ === 0) start = i; }
      else if (ch === "}" && --depth === 0 && start >= 0) { try { out.push(JSON.parse(text.slice(start, i + 1))); } catch (e) {} start = -1; }
    }
    return out;
  }
  function resultText(r) {
    if (!r) return "";
    if (typeof r.payload === "string") return r.payload;
    var t = (r.content || []).filter(function (b) { return b && b.type === "text"; }).map(function (b) { return b.text; }).join("\n");
    return t || (r.payload ? JSON.stringify(r.payload, null, 2) : "");
  }
  function norm(s) { return decodeURIComponent(String(s || "")).replace(/^https:\/\/[^/]+\//i, "").replace(/\/+$/, "").toLowerCase(); }

  function mcpErrorText(code, serverName, retryable) {
    var s = serverName || "this connector";
    var map = {
      server_not_connected: "Add the " + s + " connector in claude.ai Settings -> Connectors.",
      needs_reauth: "Reconnect " + s + " in claude.ai Settings -> Connectors.",
      selection_required: "Choose which " + s + " connector this board uses (claude.ai asked once).",
      not_in_manifest: s + " is not declared in this board's publish capabilities.",
      approval_required: "Reading needs your approval: click the link again and allow it.",
      blocked_by_policy: "Your organization blocks " + s + " here.",
      not_granted: "Connectors are not available in this view.",
      capability_disabled: "Connectors are not available in this view."
    };
    return map[code] || ("Could not read via " + s + " (" + (code || "error") + ")" + (retryable ? "; try again in a moment." : "."));
  }

  /* Built-in connector adapters */
  var FILE_RE = /\.(docx?|xlsx?|pptx?|pdf|txt|csv|md|json|xml|html?|rtf|odt|ods)$/i;
  var ADAPTERS = [
    {
      id: "sharepoint",
      label: "SharePoint",
      server: "Microsoft 365",
      tools: ["sharepoint_search", "read_resource"],
      match: function (href) {
        var m = /^https:\/\/[a-z0-9-]+(?:-my)?\.sharepoint\.com\/([^?#]+)/i.exec(href);
        if (!m) return null;
        var path = decodeURIComponent(m[1]).replace(/\/+$/, "");
        if (/\/(Lists|_layouts|SitePages)\//i.test("/" + path) || !FILE_RE.test(path)) return null;
        var name = path.split("/").pop();
        return { href: href, path: path, name: name, title: name };
      },
      read: function (mcp, item, page) {
        var n = page || 1;
        var q = item.name.replace(/\.[^.]+$/, "");
        var findHit = item._hit
          ? Promise.resolve(item._hit)
          : mcp.callTool(this.server, "sharepoint_search", { query: q, limit: 25 }, CACHE_OPT).then(function (r) {
              var hits = jsonObjects(resultText(r)).filter(function (x) { return x.uri && x.webUrl; });
              var want = norm(item.path);
              var hit = hits.filter(function (x) { return norm(x.webUrl) === want; })[0] || hits.filter(function (x) { return x.name === item.name; })[0];
              if (!hit) return { _notFound: "This file was not found by your Microsoft 365 search (" + hits.length + " other results)." };
              item._hit = hit;
              return hit;
            });
        var srv = this.server;
        return findHit.then(function (hit) {
          if (hit._notFound) return { text: hit._notFound };
          return mcp.callTool(srv, "read_resource", n > 1 ? { uri: hit.uri, startPage: n, endPage: n } : { uri: hit.uri, endPage: 1 }, CACHE_OPT).then(function (rr) {
            var t = resultText(rr);
            var more = /re-call with startPage=(\d+)|startPage=(\d+) to continue/.exec(t);
            var meta = (hit.lastModifiedDateTime ? "Modified " + String(hit.lastModifiedDateTime).slice(0, 16).replace("T", " ") + " · " : "") + "read with your account" + (rr && rr.cache ? " (cached " + new Date(rr.cache.storedAt).toLocaleTimeString() + ")" : "");
            return {
              title: hit.name || item.title,
              text: t || "(empty)",
              prevPage: n > 1 ? n - 1 : 0,
              nextPage: more ? Number(more[1] || more[2]) : 0,
              meta: meta
            };
          });
        });
      }
    },
    {
      id: "gmail",
      label: "Gmail",
      server: "Gmail",
      tools: ["gmail_read_thread", "gmail_get_message"],
      match: function (href) {
        var m = /^https:\/\/mail\.google\.com\/mail\/u\/\d+\/[^#]*#[^/]+\/([A-Za-z0-9_-]{8,})/i.exec(href);
        if (!m) return null;
        return { href: href, id: m[1], title: "Gmail thread " + m[1].slice(0, 10) };
      },
      read: function (mcp, item) {
        var srv = this.server;
        return mcp.callTool(srv, "gmail_read_thread", { thread_id: item.id }, CACHE_OPT).catch(function () {
          return mcp.callTool(srv, "gmail_get_message", { message_id: item.id }, CACHE_OPT);
        }).then(function (rr) {
          var t = resultText(rr);
          var objs = jsonObjects(t);
          var subj = (objs[0] && (objs[0].subject || (objs[0].messages && objs[0].messages[0] && objs[0].messages[0].subject))) || "";
          return {
            title: subj || item.title,
            text: t || "(empty)",
            meta: "read with your Gmail account" + (rr && rr.cache ? " (cached " + new Date(rr.cache.storedAt).toLocaleTimeString() + ")" : "")
          };
        });
      }
    },
    {
      id: "gdrive",
      label: "Google Drive",
      server: "Google Drive",
      tools: ["google_drive_fetch"],
      match: function (href) {
        var m = /^https:\/\/(?:docs|drive)\.google\.com\/(?:document|spreadsheets|presentation|file)\/d\/([A-Za-z0-9_-]{10,})/i.exec(href);
        if (!m) return null;
        return { href: href, id: m[1], title: "Google Drive " + m[1].slice(0, 10) };
      },
      read: function (mcp, item) {
        return mcp.callTool(this.server, "google_drive_fetch", { document_ids: [item.id] }, CACHE_OPT).then(function (rr) {
          var t = resultText(rr);
          var objs = jsonObjects(t);
          var nm = (objs[0] && (objs[0].title || objs[0].name)) || "";
          return {
            title: nm || item.title,
            text: t || "(empty)",
            meta: "read with your Google Drive account" + (rr && rr.cache ? " (cached " + new Date(rr.cache.storedAt).toLocaleTimeString() + ")" : "")
          };
        });
      }
    },
    {
      id: "outlook",
      label: "Outlook",
      server: "Microsoft 365",
      tools: ["outlook_email_search", "read_resource"],
      match: function (href) {
        var m = /^https:\/\/outlook\.(?:office|office365|live)\.com\/mail\/.*\/id\/([^/?#]+)/i.exec(href);
        if (!m) return null;
        return { href: href, id: decodeURIComponent(m[1]), title: "Outlook message" };
      },
      read: function (mcp, item) {
        return mcp.callTool(this.server, "read_resource", { uri: item.href }, CACHE_OPT).then(function (rr) {
          return {
            title: item.title,
            text: resultText(rr) || "(empty)",
            meta: "read with your Microsoft 365 account"
          };
        });
      }
    },
    {
      id: "notion",
      label: "Notion",
      server: "Notion",
      tools: ["notion-fetch"],
      match: function (href) {
        var m = /^https:\/\/(?:www\.)?notion\.(?:so|site)\/([^?#]+)/i.exec(href);
        if (!m) return null;
        var slug = decodeURIComponent(m[1].split("/").pop() || "Notion page").replace(/-[0-9a-f]{32}$/i, "").replace(/-/g, " ");
        return { href: href, id: href, title: slug || "Notion page" };
      },
      read: function (mcp, item) {
        return mcp.callTool(this.server, "notion-fetch", { id: item.id }, CACHE_OPT).then(function (rr) {
          return { title: item.title, text: resultText(rr) || "(empty)", meta: "read with your Notion account" };
        });
      }
    },
    {
      id: "linear",
      label: "Linear",
      server: "Linear",
      tools: ["get_issue"],
      match: function (href) {
        var m = /^https:\/\/linear\.app\/[^/]+\/issue\/([A-Z0-9]+-\d+)/i.exec(href);
        if (!m) return null;
        return { href: href, id: m[1].toUpperCase(), title: m[1].toUpperCase() };
      },
      read: function (mcp, item) {
        return mcp.callTool(this.server, "get_issue", { id: item.id }, CACHE_OPT).then(function (rr) {
          return { title: item.title, text: resultText(rr) || "(empty)", meta: "read with your Linear account" };
        });
      }
    }
  ];

  function activeAdapters() {
    var cfg = window.__KIT_MCP_READERS__;
    if (Array.isArray(cfg)) {
      if (!cfg.length) return [];
      var set = {};
      cfg.forEach(function (x) { set[String(x).toLowerCase()] = true; });
      return ADAPTERS.filter(function (ad) { return !!set[ad.id]; });
    }
    return ADAPTERS;
  }

  function matchLink(href) {
    if (!href) return null;
    var list = activeAdapters();
    for (var i = 0; i < list.length; i++) {
      var hit = list[i].match(href);
      if (hit) return { adapter: list[i], item: hit };
    }
    return null;
  }

  function openReaderWin(adapter, item) {
    var old = document.getElementById("kit-mcp-win"); if (old) old.remove();
    var w = document.createElement("section");
    w.id = "kit-mcp-win";
    w.className = "kit-tl-win";
    w.setAttribute("role", "dialog");
    w.setAttribute("aria-label", item.title || adapter.label);
    if (!window._kitMakeWindow) {
      w.style.cssText = "position:fixed;inset:max(16px,4vh) max(16px,6vw);z-index:60;display:flex;flex-direction:column;background:var(--surface,#fff);color:var(--ink,#111);border:1px solid var(--line,#ccc);border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.35);overflow:hidden;";
    }
    var h = document.createElement("div"); h.className = "kit-tl-hdr";
    if (!window._kitMakeWindow) h.style.cssText = "display:flex;gap:8px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line,#ccc);background:var(--sunk,#f3f3f3);";
    var b = document.createElement("b"); b.textContent = item.title || adapter.label;
    b.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
    var sub = document.createElement("span"); sub.className = "tlsub"; sub.textContent = adapter.label;

    /* The ONE real external link left for this resource (so claude.ai's frame opens it only on explicit click) */
    var open = document.createElement("a");
    open.href = item.href; open.target = "_blank"; open.rel = "noopener";
    open.className = "btn small kit-icon-btn";
    open.title = "Open in " + adapter.label;
    open.setAttribute("aria-label", open.title);
    open.innerHTML = SVG_EXT;

    var close = document.createElement("button");
    close.type = "button"; close.className = "btn small kit-icon-btn";
    close.title = "Close"; close.setAttribute("aria-label", "Close");
    close.innerHTML = SVG_CLOSE;
    close.addEventListener("click", function () { w.remove(); });

    h.appendChild(b); h.appendChild(sub); h.appendChild(open); h.appendChild(close);
    var body = document.createElement("div"); body.className = "kit-tl-body";
    body.style.cssText = "flex:1;overflow:auto;overscroll-behavior:contain;padding:14px 18px;";
    var pre = document.createElement("pre");
    pre.style.cssText = "white-space:pre-wrap;word-break:break-word;font:14px/1.6 var(--text,system-ui);margin:0;max-width:72ch;";
    pre.textContent = "Reading through your " + adapter.server + " connector...";
    var foot = document.createElement("div");
    foot.style.cssText = "display:flex;gap:8px;align-items:center;padding:8px 14px;border-top:1px solid var(--line,#ccc);background:var(--sunk,#f3f3f3);";
    body.appendChild(pre); w.appendChild(h); w.appendChild(body); w.appendChild(foot);
    document.body.appendChild(w);
    if (window._kitMakeWindow) {
      window._kitMakeWindow(w, h, { minW: 360, minH: 260, winKey: "mcp_reader" });
      if (window._kitPlaceWindow) window._kitPlaceWindow(w, 720, 600, "mcp_reader", "mcp_reader", 360, 260);
    }
    document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { w.remove(); document.removeEventListener("keydown", esc); } });
    return { win: w, titleEl: b, pre: pre, foot: foot };
  }

  function showMatched(adapter, item) {
    var ui = openReaderWin(adapter, item);
    var c = window.claude;
    (c && c.use ? c.use("mcp") : Promise.resolve(null)).then(function (mcp) {
      if (!mcp) { ui.pre.textContent = "Connectors are not available in this view. Use the external link icon to open in " + adapter.label + "."; return; }
      function loadPage(n) {
        ui.pre.textContent = "Reading " + (item.title || adapter.label) + (n > 1 ? ", page " + n : "") + "...";
        return adapter.read(mcp, item, n).then(function (res) {
          if (res.title) ui.titleEl.textContent = res.title;
          ui.pre.textContent = res.text || "(empty)";
          ui.foot.textContent = "";
          if (res.prevPage) {
            var pv = document.createElement("button"); pv.type = "button"; pv.className = "btn small";
            pv.style.cssText = "display:inline-flex;align-items:center;gap:4px;";
            pv.innerHTML = SVG_PREV + "<span>Page " + res.prevPage + "</span>";
            pv.onclick = function () { loadPage(res.prevPage); };
            ui.foot.appendChild(pv);
          }
          if (res.nextPage) {
            var nx = document.createElement("button"); nx.type = "button"; nx.className = "btn small";
            nx.style.cssText = "display:inline-flex;align-items:center;gap:4px;";
            nx.innerHTML = "<span>Page " + res.nextPage + "</span>" + SVG_NEXT;
            nx.onclick = function () { loadPage(res.nextPage); };
            ui.foot.appendChild(nx);
          }
          if (res.meta) {
            var when = document.createElement("span"); when.className = "note";
            when.textContent = res.meta;
            ui.foot.appendChild(when);
          }
        });
      }
      return loadPage(1);
    }).catch(function (e) {
      var code = e && e.code;
      ui.pre.textContent = mcpErrorText(code, adapter.server, e && e.retryable) + " You can still open it in " + adapter.label + ".";
    });
  }

  /* Disarm matching links: claude.ai's frame opens a link's href on its own before/beside page handlers.
     Moving href -> data-mcp-href ensures only the reader window opens; the header external-link icon is the
     one real <a> href left. */
  function disarm(a) {
    if (a.hasAttribute("data-mcp-href") || a.closest("#kit-mcp-win")) return;
    var href = a.getAttribute("href") || "";
    var hit = matchLink(href);
    if (!hit) return;
    a.setAttribute("data-mcp-href", href);
    a.setAttribute("data-mcp-adapter", hit.adapter.id);
    if (hit.adapter.id === "sharepoint") a.setAttribute("data-sp-href", href);
    a.removeAttribute("href"); a.removeAttribute("target");
    a.setAttribute("role", "link"); a.tabIndex = 0;
    a.style.cursor = "pointer"; a.style.textDecoration = "underline"; a.style.color = "var(--ember, #b8561c)";
    a.title = "Open in reader window (" + hit.adapter.server + " connector)";
  }
  function disarmIn(n) { if (n.tagName === "A") disarm(n); else if (n.querySelectorAll) [].forEach.call(n.querySelectorAll("a[href]"), disarm); }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      if (m.type === "attributes" && m.target.tagName === "A") disarm(m.target);
      (m.addedNodes || []).forEach(function (n) { if (n.nodeType === 1) disarmIn(n); });
    });
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["href"] });
  disarmIn(document.documentElement);

  function openFrom(ev) {
    var a = ev.target && ev.target.closest && ev.target.closest("a[data-mcp-href]");
    if (!a) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    var hit = matchLink(a.getAttribute("data-mcp-href") || "");
    if (hit) showMatched(hit.adapter, hit.item);
  }
  window.addEventListener("click", openFrom, true);
  window.addEventListener("keydown", function (ev) { if (ev.key === "Enter") openFrom(ev); }, true);

  /* Public hooks for tests & custom adapters */
  window.__KIT_MCP_READER__ = {
    adapters: ADAPTERS,
    matchLink: matchLink,
    register: function (ad) { if (ad && ad.id) ADAPTERS.push(ad); }
  };
  window.__KIT_SP__ = {
    spFile: function (a) { return ADAPTERS[0].match(a.getAttribute("data-mcp-href") || a.getAttribute("data-sp-href") || a.getAttribute("href") || ""); },
    jsonObjects: jsonObjects,
    norm: norm
  };
})();

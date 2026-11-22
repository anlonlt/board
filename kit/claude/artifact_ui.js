/* artifact_ui.js — the board page's buttons that called the local server directly (not through api()), redone with
   the Artifact's own means. Inserted after the page's script by build_artifact_page.py. */
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

  /* "Link for AI" (#ai-share). Local: mints an anonymous 24 h token link (POST /_api/shares).
     Artifact: no anonymous access exists, so it gives what an AI with a Claude account needs: the deep link to the
     post and a bootstrap brief (ArtifactData read/answer/deliver). Clipboard first; if the frame refuses it, the
     brief is offered as a file (downloads.save, the viewer confirms); last resort, a prompt to copy by hand. */
  /* One text that works whatever the receiving AI is: the post itself (title, body, latest comments) so a chat with
     no access can answer from it; how to find it on the board by its id (the #/post/ link is not guaranteed to
     reach the page through claude.ai's frame); and how each kind of runner answers. */
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
})();

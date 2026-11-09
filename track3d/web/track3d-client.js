/* track3d client - use the model builder from any page.

   <script src="https://YOUR-SERVER/track3d-client.js"></script>

   // 1) headless: ask for the finished files, no UI
   const { blob, filename, meta } = await Track3D.build({ demo: true, format: "3mf" });
   Track3D.download({ blob, filename });

   // 2) embed the full editor in an <iframe>; get the files back as the user generates / compiles
   Track3D.embed("#holder", { src: "demo", onCompiled: e => console.log(e.files) });

   The server must list your page's origin (--cors-origin) for the browser to allow build(). */
(function (root) {
  "use strict";
  const here = (typeof document !== "undefined" && document.currentScript && document.currentScript.src)
    ? new URL(document.currentScript.src).origin : "";
  const trim = u => String(u == null ? "" : u).replace(/\/+$/, "");
  const baseOf = o => trim(o.baseUrl != null ? o.baseUrl : here);
  const headers = o => Object.assign({ "Content-Type": "application/json" }, o.apiKey ? { "X-API-Key": o.apiKey } : {});
  const BODY_KEYS = ["gpx", "place", "lat", "lon", "radius_km", "demo", "settings", "options", "objects", "project",
                     "colors", "name", "per_object", "format", "async"];

  async function fail(res) {
    let msg = ""; try { msg = (await res.json()).error; } catch (e) { /* not JSON */ }
    const err = new Error(msg || "HTTP " + res.status); err.status = res.status; return err;
  }

  /* build(options) -> { blob, filename, meta }            for format zip | 3mf | stl | project
                    -> manifest object                     for format "json" or async:true
     options: any request field (see GET /api/build for the list) + baseUrl, apiKey, signal */
  async function build(o) {
    o = o || {};
    const body = {}; BODY_KEYS.forEach(k => { if (o[k] !== undefined) body[k] = o[k]; });
    const res = await fetch(baseOf(o) + "/api/build", { method: "POST", headers: headers(o), body: JSON.stringify(body), signal: o.signal });
    if (!res.ok) throw await fail(res);
    const isFile = !/json/.test(res.headers.get("Content-Type") || "") || body.format === "project";
    if (!isFile) return res.json();
    const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
    let meta = null; try { meta = JSON.parse(res.headers.get("X-Track3D-Meta") || "null"); } catch (e) { /* optional */ }
    return { blob: await res.blob(), filename: m ? m[1] : "track3d-model", meta };
  }

  /* After build({async:true}): wait until it finishes. Resolves with the manifest (files + zip link). */
  async function wait(job, o) {
    o = o || {}; const base = baseOf(o), every = o.interval || 1500;
    for (;;) {
      const res = await fetch(base + "/api/build?id=" + encodeURIComponent(job.id), { headers: o.apiKey ? { "X-API-Key": o.apiKey } : {}, signal: o.signal });
      if (!res.ok) throw await fail(res);
      const j = await res.json();
      if (o.onStatus) o.onStatus(j);
      if (j.status === "done") return j.result;
      if (j.status === "error") throw new Error(j.error || "build failed");
      await new Promise(r => setTimeout(r, every));
    }
  }

  /* Save a { blob, filename } result through the browser's download mechanism. */
  function download(r) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(r.blob); a.download = r.filename || "track3d-model";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  /* embed(target, options) puts the full editor into target (element or selector).
     options: baseUrl, src ("demo"|"gpx"), lat, lon, radius (km), name, autorun, height, width,
              onGenerated(e), onCompiled(e)   - e = { id, name, files:[{name,size,url}], zip }
     Returns { iframe, destroy() }. */
  function embed(target, o) {
    o = o || {};
    const host = typeof target === "string" ? document.querySelector(target) : target;
    if (!host) throw new Error("Track3D.embed: target not found");
    const base = baseOf(o) || location.origin, q = new URLSearchParams({ embed: "1", origin: location.origin });
    ["src", "lat", "lon", "radius", "name"].forEach(k => { if (o[k] != null) q.set(k, o[k]); });
    if (o.autorun) q.set("autorun", "1");
    const f = document.createElement("iframe");
    f.src = base + "/?" + q; f.title = o.title || "track3d model builder"; f.allow = "fullscreen";
    f.style.cssText = `border:0;width:${o.width || "100%"};height:${o.height || "720px"}`;
    const on = ev => {
      if (ev.source !== f.contentWindow || ev.origin !== new URL(base).origin || !ev.data || ev.data.source !== "track3d") return;
      const h = ev.data.type === "generated" ? o.onGenerated : ev.data.type === "compiled" ? o.onCompiled : null;
      if (h) h(ev.data);
    };
    window.addEventListener("message", on);
    host.appendChild(f);
    return { iframe: f, destroy() { window.removeEventListener("message", on); f.remove(); } };
  }

  root.Track3D = { build, wait, download, embed };
})(typeof window !== "undefined" ? window : this);

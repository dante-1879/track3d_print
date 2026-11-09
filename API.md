# track3d as a web service

One process serves **the full editor UI** (`/`) and **one headless route** (`/api/build`) that returns the finished
model files from a single call, with no browser or UI session.

```
python -m track3d ui --host 0.0.0.0 --port 8765 --workdir data --no-browser      # or: docker compose up
```

## `POST /api/build` — files in one call

Send JSON, get the files back. Pick **one source**:

| field | meaning |
|---|---|
| `gpx` | GPX file contents (text) |
| `place` + `radius_km` | place name (looked up with OpenStreetMap Nominatim) |
| `lat`, `lon` + `radius_km` | map centre |
| `demo: true` | offline synthetic terrain (no internet needed) |
| `project` | an *edit file* (`format: "project"` output / UI "Export edit file") – rebuilds it, no map download |

Optional fields:

| field | meaning |
|---|---|
| `settings` | nested settings, same JSON as the UI's *Save settings* |
| `options` | flat `{"path_width": 1.6, "size": 200}`; names and limits are listed at `GET /api/schema` |
| `objects` | edited object list (from an edit file) applied on top of the generated map |
| `colors` | `{"buildings": "#aabbcc", ...}` per class: `terrain path green water roads buildings` |
| `name` | file-name prefix |
| `per_object` | `true` = also one STL per building / road / ... (many files) |
| `format` | `zip` (default, everything) · `3mf` · `stl` (STLs zipped) · `project` (edit file) · `json` (manifest + links) |
| `async` | `true` = return `202 {id, status_url}` immediately (see below) |

```sh
curl -X POST https://HOST/api/build -H "X-API-Key: KEY" -H "Content-Type: application/json" \
     -d '{"demo": true, "format": "3mf"}' -o model.3mf
```

The response is the file itself (`Content-Disposition` has the name). `X-Track3D-Meta` is a JSON header with
`{name, info:{width_mm, depth_mm, height_mm,...}, counts, warnings, files}`.

The zip holds: one STL per colour (`terrain path green water roads buildings`), `*_base_combined.stl`
(everything except the path), a coloured `*.3mf`, and `*.track3d.json` (the edit file).

**`GET /api/build?demo=1&format=3mf&path_width=1.6`** does the same for simple links (no GPX; flat option names are
query parameters). `GET /api/build` with no parameters returns usage.

### Long builds
Real maps download OSM / elevation data and can take a minute or more. Use `"async": true`, then
`GET /api/build?id=ID` until `status` is `done`, then `GET /api/build?id=ID&download=zip` (or `3mf`, `stl`, `project`).
`format: "json"` keeps the files and returns links. Kept results are deleted after `--ttl-hours` (default 24).
In the manifest, each `files[].url` is a plain link that downloads without a key; the `zip` link goes through
`/api/build` and needs the key header, so fetch it with the header (`Track3D.wait` does not do this for you).

### Errors
JSON `{"error": "..."}` with: `400` bad input (unknown option, value outside its limit, unreadable GPX/edit file,
no source) · `401` key · `404` unknown id · `413` too large · `422` generation failed · `503` all build slots busy
(`Retry-After` set) · `500` unexpected.

## From another web page

```html
<script src="https://HOST/track3d-client.js"></script>
<script>
  // headless
  const r = await Track3D.build({ baseUrl: "https://HOST", apiKey: "KEY", demo: true, format: "3mf" });
  Track3D.download(r);

  // the full editor inside your page; your page is told when files are ready
  Track3D.embed("#holder", { baseUrl: "https://HOST", src: "demo", autorun: true,
                             onGenerated: e => console.log(e.files),   // [{name, size, url}], e.zip
                             onCompiled:  e => console.log(e.files) });
</script>
```
Browsers only allow `Track3D.build` from pages the server lists: `--cors-origin https://your-site.example`
(or `TRACK3D_CORS_ORIGINS`). The embedded UI works from any page unless you restrict it with
`--frame-ancestors`. The iframe accepts `?embed=1` (hides the header), `?src=demo`, `?lat=..&lon=..&radius=..`,
`?autorun=1` and `?origin=https://your-site.example` (where it sends its events).
Python and curl examples are in `examples/`.

## Server options

Each option has a `TRACK3D_*` environment variable (`.env.example`).

| option | default | |
|---|---|---|
| `--api-key KEY[,KEY]` | none | required for `/api/build` as `X-API-Key: KEY` or `Authorization: Bearer KEY` |
| `--cors-origin ORIGINS` | none | pages allowed to call `/api/build`, `/api/schema`, `/files`, `/zip` from a browser; `*` = any |
| `--frame-ancestors ORIGINS` | any | which pages may embed the UI |
| `--max-builds N` | 2 | builds at once; extra requests wait `--queue-wait` s (30), then get `503` |
| `--ttl-hours H` | 24 | retention for kept / async API results |
| `--max-body-mb N` | 40 | request size limit (an edit file embeds the terrain: about 1.5 MB at the default grid, up to ~4 MB at the maximum) |

## Deploying — read this
* **Only `/api/build` is protected by the API key.** The UI's own endpoints (`/api/generate`, `/api/compile`, `/files/…`)
  are unauthenticated and not opened to other origins. If the server is reachable from the internet, either
  expose only the API paths through your reverse proxy, or put the whole thing behind your own login.
  `GET /files/api-…` links (from `format: "json"`) rely on unguessable ids and expire after the TTL.
* Behind a proxy: forward `Host` / `X-Forwarded-Proto` (used for absolute links), allow request bodies of
  `--max-body-mb` and read timeouts of a few minutes for synchronous builds.
* Place / GPX builds need outbound internet (OpenStreetMap, Nominatim, elevation tiles). `demo: true` does not.
* The server is Python's standard `ThreadingHTTPServer`: fine for a handful of builds at a time; scale by running more
  containers, not by raising `--max-builds` past your CPU and memory.

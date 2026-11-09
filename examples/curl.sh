#!/bin/sh
# Finished files in one call. Replace the host (and the key, if the server has one).
HOST=http://localhost:8765; KEY=change-me

curl -sS -X POST "$HOST/api/build" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"demo": true, "format": "3mf"}' -o model.3mf

# From a GPX file (jq builds the JSON body):
jq -n --rawfile gpx route.gpx '{gpx: $gpx, name: "my-route"}' |
  curl -sS -X POST "$HOST/api/build" -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d @- -o my-route.zip

# Simple GET (good for <a href> links): flat setting names work as query parameters
curl -sS -H "X-API-Key: $KEY" "$HOST/api/build?demo=1&format=3mf&path_width=1.6" -o model.3mf

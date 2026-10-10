#!/usr/bin/env bash
# Generate synthetic FHIR R4 patients with Synthea. No real patient data is ever used.
#
#   data/synthea/generate.sh [count] [seed]
#
# Output: data/synthea/output/fhir/*.json (git-ignored), or $SYNTHEA_OUTPUT/fhir if set.
# Requires Java 11+. Eval cases copy a generated bundle and edit it so the expected
# verdicts are known by construction.
#
# Synthea is pinned to a tagged release and checked against its sha256, so the same seed
# gives the same patients. To upgrade, change VERSION and SHA256 together.
set -euo pipefail

VERSION="v4.0.0"
SHA256="ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"

COUNT="${1:-10}"
SEED="${2:-20261009}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${SYNTHEA_OUTPUT:-$HERE/output}"
JAR="$HERE/bin/synthea-$VERSION.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/$VERSION/synthea-with-dependencies.jar"

verify() { echo "$SHA256  $JAR" | shasum -a 256 -c - >/dev/null 2>&1; }

if [[ ! -f "$JAR" ]] || ! verify; then
  mkdir -p "$HERE/bin"
  echo "Downloading Synthea $VERSION..."
  curl -fsSL -o "$JAR" "$URL"
fi
if ! verify; then
  echo "Synthea jar does not match the pinned sha256 ($SHA256); refusing to run it." >&2
  exit 1
fi

rm -rf "$OUT"
java -jar "$JAR" \
  -p "$COUNT" \
  -s "$SEED" \
  -a 40-75 \
  --exporter.baseDirectory "$OUT" \
  --exporter.fhir.export true \
  --exporter.fhir_stu3.export false \
  --exporter.fhir_dstu2.export false \
  --exporter.ccda.export false \
  --exporter.csv.export false \
  --exporter.hospital.fhir.export false \
  --exporter.practitioner.fhir.export false \
  --generate.only_alive_patients true \
  Massachusetts

echo "Wrote $(ls "$OUT/fhir" | wc -l | tr -d ' ') bundles to $OUT/fhir"

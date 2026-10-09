#!/usr/bin/env bash
# Generate synthetic FHIR R4 patients with Synthea. No real patient data is ever used.
#
#   data/synthea/generate.sh [count] [seed]
#
# Output: data/synthea/output/fhir/*.json (git-ignored). Same seed => same patients.
# Requires Java 11+. Eval cases copy a generated bundle and edit it so the expected
# verdicts are known by construction.
set -euo pipefail

COUNT="${1:-10}"
SEED="${2:-20261009}"
HERE="$(cd "$(dirname "$0")" && pwd)"
JAR="$HERE/bin/synthea-with-dependencies.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar"

if [[ ! -f "$JAR" ]]; then
  mkdir -p "$HERE/bin"
  echo "Downloading Synthea..."
  curl -fsSL -o "$JAR" "$URL"
fi

rm -rf "$HERE/output"
java -jar "$JAR" \
  -p "$COUNT" \
  -s "$SEED" \
  -a 40-75 \
  --exporter.baseDirectory "$HERE/output" \
  --exporter.fhir.export true \
  --exporter.fhir_stu3.export false \
  --exporter.fhir_dstu2.export false \
  --exporter.ccda.export false \
  --exporter.csv.export false \
  --exporter.hospital.fhir.export false \
  --exporter.practitioner.fhir.export false \
  --generate.only_alive_patients true \
  Massachusetts

echo "Wrote $(ls "$HERE/output/fhir" | wc -l | tr -d ' ') bundles to $HERE/output/fhir"

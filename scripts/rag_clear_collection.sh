#!/usr/bin/env bash
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
COLLECTION="${1:-minibot_chunks}"

echo "Deleting collection '$COLLECTION' from $QDRANT_URL ..."
curl -s -X DELETE "$QDRANT_URL/collections/$COLLECTION" | python3 -m json.tool
echo "Done."

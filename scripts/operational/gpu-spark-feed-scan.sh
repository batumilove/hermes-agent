#!/bin/bash
cd ~
OUTPUT=$(~/blogwatcher-cli scan 2>&1)
NEW_COUNT=$(echo "$OUTPUT" | grep -oP 'Found \K\d+' | tail -1)

if [ "$NEW_COUNT" != "0" ] && [ -n "$NEW_COUNT" ]; then
    echo "📰 GPU/Inference Research Update — $NEW_COUNT new articles found:"
    echo ""
    ~/blogwatcher-cli articles 2>&1 | head -80
else
    # Silent — no new articles
    exit 0
fi

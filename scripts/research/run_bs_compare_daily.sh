#!/bin/bash
# Daily OCR vs ML B/S signal comparison
# Auto-computes date range: last 30 days up to today

END_DATE=$(date +%Y%m%d)
START_DATE=$(date -v-30d +%Y%m%d 2>/dev/null || date -d '30 days ago' +%Y%m%d)

cd /Volumes/extension/projects/Chenyiyun2087
python3 scripts/research/compare_bs_sources.py \
    --batch-a config_1 \
    --batch-b ml_detect_v3 \
    --start "$START_DATE" \
    --end "$END_DATE"

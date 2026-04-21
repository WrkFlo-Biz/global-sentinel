#!/bin/bash
# Wrapper: daily mode normally, full retrain on Saturdays
DAY=$(date +%u)  # 6 = Saturday
if [ "$DAY" -eq 6 ]; then
    echo "Saturday — running full retrain"
    /usr/bin/python3 /opt/global-sentinel/src/research/multi_agent_ensemble.py --mode retrain
else
    echo "Weekday — running daily signal generation"
    /usr/bin/python3 /opt/global-sentinel/src/research/multi_agent_ensemble.py --mode daily
fi
